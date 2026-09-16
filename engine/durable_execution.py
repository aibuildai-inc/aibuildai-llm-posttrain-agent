"""Durable identities, typed Actions, and physical Attempts over DBOS."""

from __future__ import annotations

import asyncio
import dataclasses
import functools
import importlib
from dataclasses import dataclass
import inspect
import logging
import types
import uuid
from collections.abc import AsyncIterator, Awaitable, Iterable, Mapping
from contextlib import asynccontextmanager
from contextvars import ContextVar, copy_context
from abc import ABC
from enum import Enum
from pathlib import Path, PurePosixPath
from functools import cache
from typing import (
    Annotated,
    Any,
    Callable,
    ClassVar,
    Collection,
    Final,
    Generic,
    Literal,
    NoReturn,
    TYPE_CHECKING,
    Type,
    TypeVar,
    Union,
    cast,
    final,
    get_args,
    get_origin,
    get_type_hints,
    overload,
)

from dbos import (
    DBOS,
    DBOSConfig,
    SetWorkflowID,
    WorkflowHandleAsync,
)

# Package-internal, and the one authority for "which workflow tasks exist in
# this process": DBOS records a task here at creation, so reading it has no
# window a late child can slip through. The product pins its DBOS version.
from dbos._dbos import _get_dbos_instance
from dbos._context import get_local_dbos_context
from pydantic import (
    BaseModel,
    ConfigDict,
    Field,
    PrivateAttr,
    PydanticUserError,
    SkipValidation,
    TypeAdapter,
    ValidationInfo,
    field_validator,
    model_validator,
)

from engine.base import IGNORED_MODEL_ATTR_TYPES
from engine.capability import ExecutionCapability
from engine.execution_output import ExecutionOutput, SuccessfulOutput
from engine.failure import Failure, FailureKind
from engine.program_environment import ProgramEnvironmentError
from aibuildai_version import APP_VERSION
from infra.fs.artifact_dir import prepare_dir
from infra.util.clock import _LimitExpired


# DBOS and Pydantic select some models at run time, so their boundaries use Any.
_logger = logging.getLogger(__name__)

if TYPE_CHECKING:
    from config import AgentConfig
    from engine.event.base import Event
    from engine.event.store import SearchApplication
    from engine.program_environment import ProgramEnvironment
    from infra.host_resource.controller import HostResourceController
    from infra.util.clock import Clock

    # ``DurableExecution.search`` still names the built-in application class,
    # because application code reads built-in services off it. The universal
    # Search declares the services this module itself reads, and the root
    # runner below takes that universal type, so the composition path does not
    # require an AIBuildAISearch even though this attribute is typed as one.
    from engine.builtin.aibuildai.search import AIBuildAISearch
    from engine.search.base import Search
    from engine.search.base import SearchInputT, SearchSuccessT
    from engine.run_state import RunState


ExecutionInputT = TypeVar("ExecutionInputT", covariant=True)
ChildSuccessT = TypeVar("ChildSuccessT", bound=SuccessfulOutput, covariant=True)
ChildRequestT = TypeVar("ChildRequestT")
# What one Handle observes: the Action's declared success, or that success
# or a Failure when the spawn captured Failures.
HandleResultT = TypeVar("HandleResultT", bound=ExecutionOutput[bool], covariant=True)
# What one Action method may return: its declared success or a Failure,
# directly or behind an async boundary.
_ActionReturn = Union[
    "Awaitable[Union[ChildSuccessT, Failure]]", ChildSuccessT, Failure
]
# One declared Action bound method, taking its one typed request or none.
_ActionTarget = Union[
    "Callable[[ChildRequestT], _ActionReturn[ChildSuccessT]]",
    "Callable[[], _ActionReturn[ChildSuccessT]]",
]
# One Action invocation, fully bound: the Action to start, what that one call
# may spend, and the request it takes when it declares one. The tuple's LENGTH
# carries the last part -- two entries is an Action that declares no request,
# three is an Action whose request is the third entry -- so ``None`` stays an
# ordinary request value here exactly as ``spawn`` keeps it, instead of being
# spent to mean "no request". The capability travels with the call and not
# with the identity, so one chain may run a cheap mechanical check and an
# expensive reading one against the same producer.
ActionInvocation = Union[
    "tuple[_ActionTarget[ChildRequestT, ChildSuccessT], ExecutionCapability]",
    """tuple[
        _ActionTarget[ChildRequestT, ChildSuccessT],
        ExecutionCapability,
        ChildRequestT,
    ]""",
]
"""One Action invocation, fully bound: the Action, what it may spend, and its request when it declares one."""
WaitHandleT = TypeVar(
    "WaitHandleT",
    bound="Handle[ExecutionOutput[bool]]",
)
ResultT = TypeVar("ResultT")
RecordedEventT = TypeVar("RecordedEventT", bound="Event")


class _RuntimeValueDescriptor:
    pass


# One event group: each entry is a fact type, what it targets, and its own facts.
# The public execution SDK surface. Everything else in this module is the
# internal interpreter: DBOS entry points, journal recording, and lifecycle
# helpers a Search, Composite, or Agent author never calls. ``DurableExecution``
# itself is internal on purpose: an author subclasses a family (Search, Agent,
# Program, Composite), never the base.
__all__ = [
    "ActionInvocation",
    "ExecutionContext",
    "FileRef",
    "Handle",
    "WaitResult",
    "action",
    "own_files",
]

# Cython cannot evaluate built-in ``type[...]`` in a module-level value.
EventEntries = tuple[
    tuple[Type["Event"], "DurableExecution | str | None", dict[str, object]], ...
]
DURABLE_EXECUTION_TYPES: dict[str, type["DurableExecution"]] = {}


@dataclass(frozen=True)
class _RunServices:
    """The live process services of the one run this process serves.

    They belong to the RUN, not to any Definition: the kernel, the Agent
    family and the Program family reach them through the module functions
    below, and no Search, Agent, Program or Composite has a member that
    returns the running root or any service on it."""

    search: "Search"
    configs: "AgentConfig"
    run_state: "RunState"
    store: "SearchApplication"
    clock: "Clock"
    host: "HostResourceController"
    program_environment: "ProgramEnvironment"


_RUN: "_RunServices | None" = None
_IN_STEP: ContextVar[bool] = ContextVar("aibuildai_in_step", default=False)
# The request stream a served conversation Step publishes on, set only around
# the one ``self.step`` call ``_serve`` starts. A Step task copies the context
# when it is created, so the served Step sees it and nothing else does.
_SERVING: ContextVar[str | None] = ContextVar("aibuildai_serving", default=None)
_WORKFLOW_RUN_STATE: ContextVar["RunState | None"] = ContextVar(
    "aibuildai_workflow_run_state", default=None
)
_WORKFLOW_PATH: ContextVar[str | None] = ContextVar(
    "aibuildai_workflow_path", default=None
)
_WORKFLOW_TASK: ContextVar[asyncio.Task[object] | None] = ContextVar(
    "aibuildai_workflow_task", default=None
)
_WORKFLOW_TASKS_BY_ID: dict[str, asyncio.Task[object]] = {}
_WORKFLOW_STARTS_BY_PARENT: dict[str, set[asyncio.Task[object]]] = {}


def workflow_run_state() -> "RunState | None":
    """Return the journal state at this workflow's current position."""
    return _WORKFLOW_RUN_STATE.get() if _uses_workflow_position() else None


def _services() -> "_RunServices":
    run = _RUN
    if run is None:
        raise AssertionError("durable execution services are not ready")
    return run


def bind_run_services(
    *,
    search: "Search",
    configs: "AgentConfig",
    run_state: "RunState",
    store: "SearchApplication",
    clock: "Clock",
    host: "HostResourceController",
    program_environment: "ProgramEnvironment",
) -> None:
    """Bind this process's one run, once. Production assembly is the only caller."""
    global _RUN
    if _RUN is not None:
        raise AssertionError("this process already serves a run")
    _RUN = _RunServices(
        search=search,
        configs=configs,
        run_state=run_state,
        store=store,
        clock=clock,
        host=host,
        program_environment=program_environment,
    )


def latest_run_state() -> "RunState":
    """Return the journal state this position reads and writes.

    Inside a workflow body or its active step that is the workflow's own
    position; everywhere else it is the run's live state."""
    current = workflow_run_state()
    if current is not None:
        return current
    return _services().run_state


def run_configs() -> "AgentConfig":
    """The configuration of the run this process serves."""
    return _services().configs


def run_store() -> "SearchApplication":
    """The event store of the run this process serves."""
    return _services().store


def run_clock() -> Clock:
    """The clock of the run this process serves."""
    return _services().clock


def run_host() -> "HostResourceController":
    """The host resource controller of the run this process serves."""
    return _services().host


def run_program_environment() -> "ProgramEnvironment":
    """The prepared Program environment of the run this process serves."""
    environment = _services().program_environment
    if environment is None:
        raise AssertionError("Program environment is not bound")
    return environment


def _advance_workflow(version: int) -> None:
    """Fold committed facts through this workflow's existing execution objects."""
    state = _WORKFLOW_RUN_STATE.get()
    if state is None or version <= state.version:
        return
    events = run_store().select_events(
        originator_id=state.id, gt=state.version, lte=version
    )
    state.fold(cast("tuple[Event, ...]", events), notify=False)
    if state.version != version:
        raise AssertionError(
            f"workflow journal stopped at {state.version}, expected {version}"
        )


def _owns_workflow_task() -> bool:
    """Return whether this task is the workflow task that set the context."""
    current = asyncio.current_task()
    return current is not None and current is _WORKFLOW_TASK.get()


def _uses_workflow_position() -> bool:
    """Return whether this code runs in the workflow body or its active step."""
    if _WORKFLOW_RUN_STATE.get() is None:
        return False
    if _owns_workflow_task():
        return True
    context = get_local_dbos_context()
    return context is not None and context.is_step()


def _advance_replayed_live_events(path: str, step: int) -> None:
    """Take live facts left by this execution's incomplete step."""
    state = _WORKFLOW_RUN_STATE.get()
    if state is None:
        return
    last = state.version
    # The step reads its workflow's replay position. Its missing live facts
    # are bounded by the run's current state, not that same old position.
    ceiling = _services().run_state.version
    cursor = state.version
    while cursor < ceiling:
        events = run_store().select_events(
            originator_id=state.id, gt=cursor, lte=ceiling, limit=200
        )
        if not events:
            break
        for event in events:
            event = cast("Event", event)
            if (
                event.live
                and event.workflow_path == path
                and event.workflow_step == step
            ):
                last = event.originator_version
        cursor = events[-1].originator_version
    _advance_workflow(last)


def resolve_execution_type(type_name: str) -> type["DurableExecution"]:
    """Resolve a stored type and import its named module on a miss."""
    if type_name.startswith("aibuildai_meta_"):
        from engine.generated_definitions import activate_generated_type

        activate_generated_type(type_name)
    execution_type = DURABLE_EXECUTION_TYPES.get(type_name)
    if execution_type is None:
        module_name, separator, _ = type_name.partition(":")
        if not separator:
            raise AssertionError(f"malformed execution type {type_name!r}")
        try:
            importlib.import_module(module_name)
        except ImportError as exc:
            raise AssertionError(
                f"unknown execution type {type_name!r}: importing "
                f"{module_name!r} to register it failed ({exc})"
            ) from exc
        execution_type = DURABLE_EXECUTION_TYPES.get(type_name)
    if execution_type is None:
        raise AssertionError(f"unknown execution type {type_name!r}")
    return execution_type


class RunSuspending(BaseException):
    """Stop the process epoch while its workflow stays resumable."""


class RunTerminalFailure(BaseException):
    """Carry one terminal child Failure to the root without copying it to owners."""

    def __init__(self, origin_path: str, failure: Failure) -> None:
        self.origin_path = origin_path
        self.failure = failure


def exit_epoch_recording_failure(
    execution: "DurableExecution", ordinal: int, failure: Failure
) -> "NoReturn":
    """Record one recoverable Failure on the exact Action and end this epoch."""
    from engine.event.events import ExecutionFailureRecorded

    _logger.error("%s #%d failed recoverably: %s", execution.path, ordinal, failure.reason)
    if execution.record.action(ordinal).ended_at_s is None:
        record_live_events(
            (
                (
                    ExecutionFailureRecorded,
                    execution,
                    {"ordinal": ordinal, "failure": failure},
                ),
            )
        )
    else:
        _logger.error(
            "%s #%d already recorded its result; the epoch ends without a "
            "Failure fact",
            execution.path,
            ordinal,
        )
    search = running_search()
    run_id = latest_run_state().require_run_config().run_id
    root_task = _WORKFLOW_TASKS_BY_ID.get(run_id)
    current = asyncio.current_task()
    joiner = search._joiner
    if joiner is not None and joiner is not current:
        (root_task or current or joiner).add_done_callback(lambda _: joiner.cancel())
    if (
        root_task is not None
        and root_task is not current
        and not root_task.cancelling()
    ):
        root_task.cancel()
    raise RunSuspending


ActionMethodT = TypeVar("ActionMethodT", bound=Callable[..., object])

_ACTION_MARK: Final = "__aibuildai_action__"


class _NoRequest:
    """The absent request, so ``None`` stays available as a request value."""


_NO_REQUEST: Final = _NoRequest()


def action(method: ActionMethodT) -> ActionMethodT:
    """Mark one ordinary method as an externally invokable durable Action.

    ``identity.method`` stays Python's normal bound method and ``ctx.spawn``
    receives it. The body runs only under a permission the framework grants
    for that exact method and the wrapper spends on entry, so a direct call
    from outside, from another Action, or from the same method recursively
    bypasses the journal, workflow, upstream, and completion, and is refused."""
    if isinstance(method, (classmethod, staticmethod)):
        raise TypeError("@action requires an instance method")
    name = method.__name__

    def take_permission(execution: "DurableExecution[object]") -> None:
        if execution._action_permit != name:
            raise AssertionError(
                f"{type(execution).__name__}.{name} is an Action: start it with "
                "ctx.spawn instead of calling it"
            )
        execution._action_permit = None

    if inspect.iscoroutinefunction(method):

        async def guarded_async(self: Any, *args: object) -> object:
            take_permission(self)
            return await cast(Callable[..., Awaitable[object]], method)(self, *args)

        guarded: Callable[..., object] = guarded_async
    else:

        def guarded_sync(self: Any, *args: object) -> object:
            take_permission(self)
            return method(self, *args)

        guarded = guarded_sync
    functools.update_wrapper(guarded, method)
    setattr(guarded, _ACTION_MARK, True)
    return cast(ActionMethodT, guarded)


@dataclasses.dataclass(frozen=True)
class ActionSpec:
    """The settled ABI of one declared Action: class formation decides it once, and every runtime reader consumes it instead of re-inspecting the class."""

    name: str
    function: Callable[..., object]
    attribute: Callable[..., object]
    request: tuple[str, object] | None
    success: type[SuccessfulOutput]
    output_adapter: TypeAdapter[SuccessfulOutput | Failure]


_ACTION_ABI: dict[type, dict[str, ActionSpec]] = {}


def _settle_action_abi(
    execution_type: type["DurableExecution[object]"],
) -> dict[str, ActionSpec]:
    """Read one concrete class's Action declarations and settle their ABI."""
    abi: dict[str, ActionSpec] = {}
    for name in dir(execution_type):
        attribute = inspect.getattr_static(execution_type, name, None)
        if not getattr(attribute, _ACTION_MARK, False):
            continue
        function = inspect.unwrap(cast(Callable[..., object], attribute))
        success = _declared_success_type(execution_type, name, function)
        abi[name] = ActionSpec(
            name=name,
            function=function,
            attribute=cast(Callable[..., object], attribute),
            request=_declared_request(execution_type, name, function),
            success=success,
            output_adapter=TypeAdapter(
                Annotated[success | Failure, Field(discriminator="failed")]
            ),
        )
    if not abi:
        raise AssertionError(f"{execution_type.__name__} declares no @action method")
    # One public Action is the common shape, and a name adds nothing to it: the
    # class already says what the identity is, so the method can only say "do
    # the thing it exists for". Naming it once, here, is what lets a caller
    # invoke any single-Action identity -- an Agent, a Program, a Search, a
    # Composite -- without asking which family it belongs to. A definition with
    # several Actions has real choices to name, and keeps its business verbs.
    if len(abi) == 1 and "run" not in abi:
        (only,) = abi
        raise AssertionError(
            f"{execution_type.__name__}.{only} is its one public Action, so it "
            "must be named run; use business verbs only where a definition "
            "declares several Actions"
        )
    _ACTION_ABI[execution_type] = abi
    return abi


def _action_abi(
    execution_type: type["DurableExecution[object]"],
) -> dict[str, ActionSpec]:
    abi = _ACTION_ABI.get(execution_type)
    if abi is None:
        raise AssertionError(
            f"{execution_type.__name__} is not a concrete execution definition"
        )
    return abi


def _action_spec(
    execution_type: type["DurableExecution[object]"], method_name: str
) -> ActionSpec:
    spec = _action_abi(execution_type).get(method_name)
    if spec is None:
        raise AssertionError(f"{execution_type.__name__}.{method_name} is no Action")
    return spec


def _legal_action_names(
    execution_type: type["DurableExecution[object]"],
) -> tuple[str, ...]:
    """The method names the journal may record for one identity type."""
    return tuple(_ACTION_ABI.get(execution_type, {}))


def _action_target(target: object) -> tuple["DurableExecution[object]", str]:
    """The identity and Action name one ordinary bound method carries."""
    execution = getattr(target, "__self__", None)
    function = getattr(target, "__func__", None)
    name = getattr(function, "__name__", "")
    if isinstance(execution, DurableExecution):
        spec = _action_abi(type(execution)).get(name)
        if spec is not None and spec.attribute is function:
            return execution, name
    raise TypeError(
        f"an Action target is a declared Action bound method, not {target!r}"
    )


def _declared_request(
    execution_type: type["DurableExecution[object]"],
    method_name: str,
    function: Callable[..., object],
) -> tuple[str, object] | None:
    """The one business request beside ``self`` of a declared method, ``@action`` or ``@tool``.

    Only the request SYNTAX is shared, because it is genuinely one rule: zero or one
    positional, annotated parameter with no default, so the caller supplies it or it is
    absent and the journal never fills one in. What each declaration may RETURN is not
    shared and must not be: an Action owes the durable ``SuccessfulOutput`` ABI, and a
    tool owes only a type Pydantic can serialize for its model."""
    parameters = list(inspect.signature(function).parameters.values())[1:]
    if not parameters:
        return None
    parameter = parameters[0]
    if (
        len(parameters) > 1
        or parameter.kind
        not in (
            inspect.Parameter.POSITIONAL_ONLY,
            inspect.Parameter.POSITIONAL_OR_KEYWORD,
        )
        or parameter.annotation is inspect.Parameter.empty
        or parameter.default is not inspect.Parameter.empty
    ):
        raise AssertionError(
            f"{execution_type.__name__}.{method_name} must accept zero or one "
            "positional annotated business request beside self, with no default"
        )
    return parameter.name, get_type_hints(function)[parameter.name]


def _encoded_request(
    execution_type: type["DurableExecution[object]"],
    method_name: str,
    request: object,
) -> object | None:
    """Encode one Action's business request as its canonical journal payload."""
    declared = _action_spec(execution_type, method_name).request
    what = f"{execution_type.__name__}.{method_name}"
    if isinstance(request, _NoRequest):
        if declared is not None:
            raise TypeError(f"{what} requires its {declared[0]} request")
        return None
    if declared is None:
        raise TypeError(f"{what} declares no request")
    return TypeAdapter(declared[1]).dump_python(request, mode="json")


def _decoded_request(
    execution_type: type["DurableExecution[object]"],
    method_name: str,
    payload: object | None,
) -> tuple[object, ...]:
    """Validate one recorded request payload into the Action's call argument."""
    declared = _action_spec(execution_type, method_name).request
    if declared is None:
        return ()
    return (TypeAdapter(declared[1]).validate_python(payload),)


def _declared_success_type(
    execution_type: type["DurableExecution[object]"],
    method_name: str,
    function: Callable[..., object],
) -> type[SuccessfulOutput]:
    """The one success type an ``@action``'s return annotation names; a family type variable (``Agent[Input, Success]``) resolves through the concrete class's generic binding.

    This is the durable Action success ABI and nothing else declares against it. A ``@tool``
    settles its result from its own annotation, because its value is read by a model rather
    than journalled as an execution's outcome."""
    annotation = get_type_hints(function).get("return")
    members = (
        get_args(annotation)
        if get_origin(annotation) in (Union, types.UnionType)
        else (annotation,)
    )
    candidates = [item for item in members if item is not Failure]
    success = candidates[0] if len(candidates) == 1 else None
    if isinstance(success, TypeVar):
        for base in execution_type.__mro__:
            metadata = getattr(base, "__pydantic_generic_metadata__", {})
            origin = metadata.get("origin")
            parameters = getattr(origin, "__parameters__", ())
            if success in parameters:
                success = metadata["args"][parameters.index(success)]
                break
    if isinstance(success, type) and issubclass(success, SuccessfulOutput):
        return success
    raise AssertionError(
        f"{execution_type.__name__}.{method_name} must return one "
        "SuccessfulOutput type, optionally in a union with Failure"
    )


def action_output(
    record: "ExecutionRecord", ordinal: int
) -> SuccessfulOutput | Failure | None:
    """The typed result one exact recorded Action settled with, if it has."""
    action_record = record.action(ordinal)
    if action_record.output is None:
        return None
    return _action_spec(type(record.execution), action_record.method).output_adapter.validate_python(
        action_record.output
    )


class Handle(Generic[HandleResultT]):
    """One exact Action occurrence and its durable workflow, as a future."""

    def __init__(
        self,
        run_id: str,
        path: str,
        ordinal: int,
        adapter: TypeAdapter[SuccessfulOutput | Failure],
        raw: WorkflowHandleAsync[dict[str, object]],
        *,
        capture_failure: bool,
    ) -> None:
        self._run_id = run_id
        self._path = path
        self._ordinal = ordinal
        self._adapter = adapter
        self._raw = raw
        self._capture_failure = capture_failure

    @property
    def _workflow_id(self) -> str:
        return cast(str, self._raw.workflow_id)

    def files(self) -> "FileRef":
        """The tree of the execution whose Action this Handle names.

        A resource named by the Action that produced it. It grants nothing on
        its own: passing a Handle as ``upstream`` binds no file, and only
        passing this reference to ``read`` or ``write`` does."""
        return FileRef(root=FileRoot.EXECUTION, owner=self._path)

    async def result(self) -> HandleResultT:
        """Return the Action result under the Failure policy fixed at spawn."""
        payload = await self._raw.get_result()
        _advance_workflow(cast(int, payload["journal_version"]))
        output = self._adapter.validate_python(payload["output"])
        if output.failed is True and not self._capture_failure:
            raise RunTerminalFailure(self._path, cast(Failure, output))
        return cast(HandleResultT, output)


async def recorded_handle(path: str, ordinal: int) -> "Handle[ExecutionOutput[bool]]":
    """The Handle of one Action this run recorded, from the workflow it kept.

    Every workflow restores its own execution objects, so the Handle its
    starter received belongs to that workflow alone. A caller that must name
    an Action it did not itself start -- a combination naming the candidates
    it consumed, a Search naming the Action that produced what it returns --
    asks here, and the journal answers with the workflow of that Action's
    current Attempt, which every Attempt writes as it opens. An Action that
    has opened none has no workflow to name. The Handle reads its Action's
    result rather than raising it, because an Action reached this way has
    already settled and its ending is not this caller's to suffer. The journal
    is also the whole existence check: the record says this Action ran, so
    asking the workflow store the same question again would only spend a step
    of the asking workflow on an answer the fact already gives."""
    state = latest_run_state()
    record = state.records.get(path)
    if record is None:
        raise ValueError(f"{path} is not an execution this run recorded")
    action_record = record.action(ordinal)
    if action_record.workflow_id is None:
        raise ValueError(f"{path} #{ordinal} has not opened an Attempt")
    return Handle(
        state.require_run_config().run_id,
        path,
        ordinal,
        _action_spec(type(record.execution), action_record.method).output_adapter,
        await DBOS.retrieve_workflow_async(
            action_record.workflow_id, existing_workflow=False
        ),
        capture_failure=True,
    )


@dataclasses.dataclass(frozen=True)
class WaitResult(Generic[WaitHandleT]):
    """The terminal and nonterminal Handles seen at one durable barrier."""

    completed: tuple[WaitHandleT, ...]
    pending: frozenset[WaitHandleT]


def declared_upstream(
    refs: Iterable[tuple[str, int]],
) -> tuple[tuple[str, int], ...]:
    """Name each source Action once, in first-declared order.

    An Action names the Actions whose Output it consumed. Every one of them
    has already ended, so naming one of them twice states nothing the first
    naming did not: it cannot order the sources, close a cycle, or widen what
    this Action may read. Both doors that record the fact normalize it here,
    so a consumer of the recorded tuple reads each source exactly once."""
    named: list[tuple[str, int]] = []
    for ref in refs:
        if ref not in named:
            named.append(ref)
    return tuple(named)


class FileRoot(Enum):
    """The three durable anchors a logical file reference hangs from.

    Each is a run-structural fact the runtime already owns, so a reference
    made of one of them plus a relative name survives a resume and a moved
    run home. There is no fourth anchor and no product meaning here: what
    ``public``, ``board``, or ``code`` denotes belongs to the package that
    owns that resource."""

    EXECUTION = "execution"
    RUN = "run"
    INPUT = "input"


class FileRef(BaseModel):
    """One logical filesystem resource an Action may be granted.

    A durable value, not a path: it names an anchor, the relative name its
    owner gave the resource, and whether reading it means reading what its
    symlinks point at. It carries no host absolute path, no live object and
    no callback, so a restored journal rebuilds the same grant, and the tree
    it names may gain and lose files without the grant changing."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    root: FileRoot
    # The execution whose directory the reference hangs from; EXECUTION only.
    # Empty under EXECUTION is the self-reference: whichever execution runs the
    # Action this grant belongs to. It is what lets a caller grant a child its
    # OWN live tree, which the caller cannot name because the child has no
    # journal path until the same spawn records it.
    owner: str = ""
    # The resource's name under that anchor. Empty means the anchor itself.
    relative: str = ""
    # Whether the real target of every symlink inside also joins the grant: an
    # index of links is only as readable as the directories it names. The owner
    # that mints the reference decides this, because only it knows whether its
    # resource is a tree of bytes or an index.
    links: bool = False

    @field_validator("owner", "relative")
    @classmethod
    def _stays_under_its_anchor(cls, v: str, info: ValidationInfo) -> str:
        """Make both address fields keep the promise their names make.

        A logical reference is an anchor, the execution that anchor hangs
        from, and a name under it. An absolute spelling silently discards the
        anchor and names a host path instead, and a ``..`` step walks out of
        the resource its owner meant to share -- either one turns a reference
        into an address of something else, so both are refused here rather
        than normalised into a different grant: a grant nobody declared is
        worse than a call that fails. The same rule holds for both fields
        because both are joined onto a directory."""
        if not v:
            return v
        candidate = PurePosixPath(v)
        if candidate.is_absolute():
            raise ValueError(
                f"{info.field_name}={v!r} is an absolute path; a FileRef names "
                "a resource under its root, never a host location"
            )
        if any(part in ("..", ".") for part in candidate.parts):
            raise ValueError(
                f"{info.field_name}={v!r} leaves its root; a FileRef names a "
                "resource inside the anchor it declares"
            )
        return v

    @model_validator(mode="after")
    def _owner_suits_its_root(self) -> "FileRef":
        """Only an EXECUTION reference hangs from a named execution.

        The run home and the task folder are the run's own anchors, so an
        owner beside them would name a second anchor that nothing resolves."""
        if self.owner and self.root is not FileRoot.EXECUTION:
            raise ValueError(
                f"a {self.root.value} reference has no owner execution; "
                f"owner={self.owner!r} names one"
            )
        return self

    def path(self) -> str:
        """The one host directory this reference names, resolved now.

        String work only: a persisted record reads no filesystem. What a
        reference BINDS, links resolved, is ``engine.file_grants``, which is
        also where a self-reference learns which execution it means.

        An EXECUTION owner is resolved against the journal, not concatenated:
        a well-formed address that names no recorded execution is an address
        of nothing, and a directory built from it would be a location this
        run never gave anybody."""
        state = latest_run_state()
        if self.root is FileRoot.EXECUTION:
            if not self.owner:
                raise AssertionError(
                    "a self-reference names no execution on its own; it is "
                    "resolved against the Action that holds the grant"
                )
            if self.owner not in state.records:
                raise ValueError(
                    f"owner={self.owner!r} is not an execution this run "
                    "recorded; a file grant hangs from a durable address"
                )
            anchor = state.execution_directory(self.owner)
        elif self.root is FileRoot.RUN:
            anchor = state.run_home
        else:
            anchor = run_configs().task_folder
        return str(Path(anchor, self.relative)) if self.relative else anchor


def own_files() -> FileRef:
    """The whole live tree of whichever execution runs the Action this grant is given to.

    The one reference a caller cannot write out, because the child it is
    granting has no journal path until the spawn that records the grant. An
    Action that starts descendants of its own during its own run -- a role that
    manages a search from inside it -- reads them through this and through
    nothing wider."""
    return FileRef(root=FileRoot.EXECUTION)


def declared_grants(refs: Collection[FileRef]) -> tuple[FileRef, ...]:
    """Name each granted resource once, in first-declared order."""
    named: list[FileRef] = []
    for ref in refs:
        if not isinstance(ref, FileRef):
            raise TypeError(
                f"a file grant holds {type(ref).__name__}, not a FileRef: "
                "authority is explicit, and no str, Path, Input field or "
                "upstream Handle becomes one"
            )
        if ref not in named:
            named.append(ref)
    return tuple(named)


async def _observe_gpu_placement(
    count: int, candidates: "tuple[int, ...] | None", caller_path: str
) -> list[int]:
    """Observe the host once and choose the cards for one Action.

    This is the whole non-deterministic part of starting an Action, so it is
    all that runs inside the Step: its answer is checkpointed beside the
    Action that asked for it, and a replay reads that answer instead of
    asking the host a second time. A recovery must not depend on telemetry
    that can now fail, time out, or name different cards."""
    caller = latest_run_state().execution_for(caller_path)
    placed = await run_host().select_gpus(
        count,
        lambda: run_clock().effective_remaining_s(caller),
        candidates=candidates,
    )
    return list(placed)


class ExecutionContext:
    """The exact running Action: it binds identities and starts their Actions."""

    def __init__(self, path: str, ordinal: int, method: str) -> None:
        self.path = path
        self.ordinal = ordinal
        self.method = method
        self._handles: dict[tuple[str, int], Handle[ExecutionOutput[bool]]] = {}
        self._served = 0
        # The ending the last served Step checkpointed with its result. Empty
        # until one has, and never equal to another attempt's ending.
        self._ending = ""

    @property
    def _workflow_id(self) -> str | None:
        return DBOS.workflow_id

    @overload
    async def spawn(
        self,
        target: "_ActionTarget[ChildRequestT, ChildSuccessT]",
        request: "ChildRequestT | _NoRequest" = _NO_REQUEST,
        *,
        upstream: Collection["Handle[ExecutionOutput[bool]]"] = (),
        read: Collection[FileRef] = (),
        write: Collection[FileRef] = (),
        capability: ExecutionCapability | None = None,
        gpu_candidates: Collection[int] | None = None,
        capture_failure: Literal[False] = False,
    ) -> Handle[ChildSuccessT]: ...

    @overload
    async def spawn(
        self,
        target: "_ActionTarget[ChildRequestT, ChildSuccessT]",
        request: "ChildRequestT | _NoRequest" = _NO_REQUEST,
        *,
        upstream: Collection["Handle[ExecutionOutput[bool]]"] = (),
        read: Collection[FileRef] = (),
        write: Collection[FileRef] = (),
        capability: ExecutionCapability | None = None,
        gpu_candidates: Collection[int] | None = None,
        capture_failure: Literal[True],
    ) -> Handle[ChildSuccessT | Failure]: ...

    async def spawn(
        self,
        target: "_ActionTarget[ChildRequestT, ChildSuccessT]",
        request: "ChildRequestT | _NoRequest" = _NO_REQUEST,
        *,
        upstream: Collection["Handle[ExecutionOutput[bool]]"] = (),
        read: Collection[FileRef] = (),
        write: Collection[FileRef] = (),
        capability: ExecutionCapability | None = None,
        gpu_candidates: Collection[int] | None = None,
        capture_failure: bool = False,
    ) -> "Handle[ChildSuccessT] | Handle[ChildSuccessT | Failure]":
        """Start one declared Action and return its workflow Handle.

        The first Action of an identity records the identity under this
        Action; later Actions on the same author-side object address the
        same journal path.

        ``read`` and ``write`` are this exact Action's filesystem authority:
        the logical resources it may read, and the ones it may also write.
        They are recorded beside its request and ``upstream``, and none of the
        three implies another -- an upstream Action does not expose its files,
        a granted resource creates no result dependency, and owning an
        execution does not make its descendants readable. Several Actions may
        hold ``write`` on one resource at once; ordering and collaboration
        belong to the Search, not to the runtime."""
        if _IN_STEP.get():
            raise AssertionError("a step cannot start an Action")
        run_state = latest_run_state()
        parent = run_state.execution_for(self.path)
        self._admit_exploration_cost(parent)
        execution, method_name = _action_target(target)
        payload = _encoded_request(type(execution), method_name, request)
        if execution._record is None:
            self._bind(execution)
        elif execution.parent_path != self.path:
            raise ValueError(f"{execution.path} is not owned by {self.path}")
        declared = capability or ExecutionCapability()
        type(execution)._check_action_capability(declared)
        from engine.event.events import ActionStarted

        started = record_durable_event(
            ActionStarted,
            execution,
            method=method_name,
            request=payload,
            ordinal=len(execution.record.actions) + 1,
            caller_ordinal=self.ordinal,
            upstream=self._resolve_upstream(upstream),
            read=declared_grants(read),
            write=declared_grants(write),
            capability=await self._placed_capability(
                declared,
                gpu_candidates=gpu_candidates,
                what=f"{execution.path} {method_name}",
            ),
        )
        handle = await self._dispatch(
            execution,
            started.ordinal,
            method_name,
            payload,
            capture_failure=capture_failure,
        )
        self._handles[(execution.path, started.ordinal)] = handle
        return cast(Handle[ChildSuccessT], handle)

    async def _placed_capability(
        self,
        capability: ExecutionCapability,
        *,
        gpu_candidates: "Collection[int] | None",
        what: str,
    ) -> ExecutionCapability:
        """Turn a card COUNT into the exact cards, once, before the Action exists.

        Both ways of arriving at cards end at the same Run boundary. An exact
        tuple skips the placement heuristic, which is why an author names
        cards, and is still checked against the cards this run may use. A
        count is resolved once, inside a Step, and the answer becomes the
        Action's recorded fact: no Attempt, Resume, or later reader asks the
        host again. Nothing is reserved either way -- another Action may be
        placed on the same card."""
        run_host().check_within_run(
            cpu_max_cores=capability.cpu_max_cores,
            memory_max_gb=capability.memory_max_gb,
            what=what,
        )
        pool = None if gpu_candidates is None else tuple(gpu_candidates)
        requested = capability.gpus
        if requested is None:
            return capability
        if isinstance(requested, tuple):
            return capability.model_copy(
                update={"gpus": run_host().placed_gpus(requested, what=what)}
            )
        if requested == 0:
            return capability.model_copy(update={"gpus": ()})
        placed = tuple(
            cast(
                "list[int]",
                await self.step(_observe_gpu_placement, requested, pool, self.path),
            )
        )
        if placed and len(placed) != requested:
            raise AssertionError(
                f"{what} asked for {requested} GPU(s) and was placed on {list(placed)}"
            )
        return capability.model_copy(update={"gpus": placed})

    def _bind(self, execution: "DurableExecution[object]") -> None:
        """Record one author-side object as an identity under this Action."""
        run_state = latest_run_state()

        from engine.event.events import ExecutionCreated

        execution_type = type(execution)
        name = execution_type.uid_name()
        count = sum(
            type(item).uid_name() == name
            for item in run_state.children_of(self.path)
        )
        created = record_durable_event(
            ExecutionCreated,
            self.path,
            parent_action_ordinal=self.ordinal,
            type_name=execution_type.type_key(),
            uid=f"{name}_{count + 1}",
            input=cast(
                dict[str, object],
                TypeAdapter(execution_type.input_type()).dump_python(
                    execution.input, mode="json"
                ),
            ),
        )
        execution._record = run_state.records[f"{self.path}/{created.uid}"]

    def _resolve_upstream(
        self, sources: Collection["Handle[ExecutionOutput[bool]]"]
    ) -> tuple[tuple[str, int], ...]:
        run_id = latest_run_state().require_run_config().run_id
        refs: list[tuple[str, int]] = []
        for source in sources:
            if not isinstance(source, Handle):
                raise TypeError(f"upstream holds {type(source).__name__}, not a Handle")
            if source._run_id != run_id:
                raise ValueError("upstream Handle belongs to another run")
            refs.append((source._path, source._ordinal))
        return declared_upstream(refs)

    def _admit_exploration_cost(self, parent: "DurableExecution") -> None:
        """Refuse new work after the exploration cost budget is spent.

        The one cost limit that decides admission. A caller's own
        ``cost_cap_usd`` is not consulted: it is that Agent's own bill, and a
        child it starts has its own."""
        state = latest_run_state()
        if not state.charges_exploration_budget(parent.path):
            return
        remaining = state.exploration_cost_remaining_usd()
        if remaining is not None and remaining <= 0:
            limit = state.require_run_config().cost_budget_usd
            raise ValueError(
                f"the exploration cost budget (${limit:.2f}) is spent, "
                "so no new exploration work is admitted"
            )

    async def _dispatch(
        self,
        execution: DurableExecution[object],
        ordinal: int,
        method_name: str,
        request: object | None,
        *,
        capture_failure: bool,
    ) -> Handle[ExecutionOutput[bool]]:
        child_type = type(execution)
        parent_workflow_id = self._workflow_id
        if parent_workflow_id is None:
            raise AssertionError("a child Execution has no parent DBOS workflow")
        start = asyncio.create_task(
            DBOS.start_workflow_async(
                execute_durable,
                child_type.type_key(),
                execution.path,
                ordinal,
                method_name,
                request,
                latest_run_state().version,
                capture_failure,
            )
        )
        starts = _WORKFLOW_STARTS_BY_PARENT.setdefault(parent_workflow_id, set())
        starts.add(cast(asyncio.Task[object], start))

        def start_finished(task: asyncio.Task[object]) -> None:
            starts.discard(task)
            if not starts:
                _WORKFLOW_STARTS_BY_PARENT.pop(parent_workflow_id, None)
            if not task.cancelled():
                task.exception()

        start.add_done_callback(start_finished)
        raw = await asyncio.shield(start)
        return Handle(
            latest_run_state().require_run_config().run_id,
            execution.path,
            ordinal,
            _action_spec(child_type, method_name).output_adapter,
            raw,
            capture_failure=capture_failure,
        )

    async def wait(
        self,
        handles: Collection[WaitHandleT],
        *,
        min_completed: int | None = None,
    ) -> WaitResult[WaitHandleT]:
        """Wait for all, any, or at least k terminal children."""
        ordered = tuple(sorted(handles, key=lambda item: item._workflow_id))
        if len({id(handle) for handle in ordered}) != len(ordered):
            raise ValueError("wait handles must be unique")
        threshold = len(ordered) if min_completed is None else min_completed
        if not 0 <= threshold <= len(ordered):
            raise ValueError("min_completed must be between zero and handle count")
        if not ordered:
            return WaitResult(completed=(), pending=frozenset())
        snapshot = await self._status_snapshot(ordered)
        while (
            sum(
                status not in {"PENDING", "ENQUEUED", "DELAYED"}
                for _, status, _ in snapshot
            )
            < threshold
        ):
            active = {
                workflow_id
                for workflow_id, status, _ in snapshot
                if status in {"PENDING", "ENQUEUED", "DELAYED"}
            }
            await DBOS.wait_first_async(
                [handle._raw for handle in ordered if handle._workflow_id in active]
            )
            snapshot = await self._status_snapshot(ordered)
        by_id = {handle._workflow_id: handle for handle in ordered}
        completed = [
            (by_id[workflow_id], updated_at)
            for workflow_id, status, updated_at in snapshot
            if status not in {"PENDING", "ENQUEUED", "DELAYED"}
        ]
        completed.sort(key=lambda item: (item[1], item[0]._workflow_id))
        completed_handles = tuple(handle for handle, _ in completed)
        return WaitResult(
            completed=completed_handles,
            pending=frozenset(
                handle for handle in ordered if handle not in completed_handles
            ),
        )

    async def _status_snapshot(
        self,
        handles: tuple[WaitHandleT, ...],
    ) -> tuple[tuple[str, str, int], ...]:
        async def observe() -> tuple[tuple[tuple[str, str, int], ...], int]:
            statuses = [await handle._raw.get_status() for handle in handles]
            return (
                tuple(
                    (status.workflow_id, status.status, status.updated_at or 0)
                    for status in statuses
                ),
                latest_run_state().version,
            )

        snapshot, version = await DBOS.run_step_async(
            {"retries_allowed": False},
            observe,
        )
        _advance_workflow(version)
        return snapshot

    async def cancel(
        self,
        handle: "Handle[ExecutionOutput[bool]]",
        failure: Failure,
        *,
        grace_s: float = 0.0,
    ) -> None:
        """Give one running child its grace, then end it and record the ending."""
        record = latest_run_state().records.get(handle._path)
        if record is None:
            raise AssertionError(f"missing child record {handle._path}")
        action_record = record.action(handle._ordinal)
        if action_record.ended_at_s is not None:
            return
        execution = record.execution
        if grace_s > 0:
            await execution.notify_stop(failure.reason)
            waiter = asyncio.ensure_future(handle._raw.get_result())
            await asyncio.wait({waiter}, timeout=grace_s)
            if not waiter.done():
                waiter.cancel()
            else:
                payload = waiter.result()
                _advance_workflow(cast(int, payload["journal_version"]))
        if action_record.ended_at_s is not None:
            return
        await DBOS.cancel_workflow_async(handle._workflow_id, cancel_children=True)
        await self.step(lambda: None)
        if action_record.ended_at_s is not None:
            self._handles.pop((handle._path, handle._ordinal), None)
            return
        settle_lost_descendants(handle._path, handle._ordinal, failure)
        execution.settle_lost(handle._ordinal, failure)
        _logger.info("%s #%d cancelled: %s", handle._path, handle._ordinal, failure.reason)
        await _join_cancelled_subtree(handle._workflow_id)
        self._handles.pop((handle._path, handle._ordinal), None)

    async def _cancel_started_children(self, failure: Failure) -> None:
        """End every Action this context started that has not finished."""
        for handle in tuple(self._handles.values()):
            await self.cancel(handle, failure)

    async def step(
        self,
        func: Callable[..., ResultT],
        *args: object,
        preemptible: bool = False,
    ) -> ResultT:
        """Run one non-deterministic leaf operation as a DBOS step."""
        # Set only by ``_serve``, and only for the Step it serves.
        serving = _SERVING.get()

        async def stamped() -> tuple[ResultT, int, str]:
            context = get_local_dbos_context()
            if context is None or not context.is_step():
                raise AssertionError("DBOS step context is not active")
            _advance_replayed_live_events(self.path, context.curr_step_function_id)
            # A served Step marks its request stream when it stops publishing,
            # however it stops, and the mark carries THIS attempt's own ending.
            # The ending is decided HERE, inside the Step, so a replayed epoch
            # never invents one: this body does not run at all when the output
            # is already recorded, and an interrupted attempt's mark stays in
            # the stream forever, so only the recorded ending says which mark
            # ends the servicing.
            ending = uuid.uuid4().hex if serving is not None else ""
            try:
                result = func(*args)
                if inspect.isawaitable(result):
                    result = await result
            except _LimitExpired:
                raise
            except Exception as exc:
                _logger.exception(
                    "%s raised an unknown exception inside a step", self.path
                )
                exit_epoch_recording_failure(
                    latest_run_state().execution_for(self.path),
                    self.ordinal,
                    Failure(
                        kind=FailureKind.UNEXPECTED,
                        reason=f"{type(exc).__name__}: {exc}",
                    ),
                )
            finally:
                if serving is not None:
                    await DBOS.write_stream_async(serving, (ending, None, None))
            return cast(ResultT, result), latest_run_state().version, ending

        token = _IN_STEP.set(True)
        try:
            result, version, recorded = await DBOS.run_step_async(
                {"retries_allowed": False, "preemptible": preemptible},
                stamped,
            )
            _advance_workflow(version)
            if serving is not None:
                self._ending = recorded
            return result
        finally:
            _IN_STEP.reset(token)

    async def _request(self, present: Callable[[str, bool], ResultT], tool: str, arguments: dict) -> ResultT:
        """Carry one live tool call from this opaque Step to its own Workflow and back.

        This is the whole Step side of the crossing. What comes back is a text and a failure
        flag, and ``present`` -- supplied by the caller that knows what the model reads, and
        bound into the published handler beside the tool's name -- is what turns those two into
        the answer that caller's model expects. This side never learns what that answer is, so
        the durable kernel stays out of every model protocol. The call key is the request's
        identity: a Step's stream write is at-least-once, so a repeated envelope carries the same
        key and the Workflow answers it with what that key already produced."""
        key = uuid.uuid4().hex
        await DBOS.write_stream_async(f"requests_{self._served}", (key, tool, arguments))
        async for reply_key, text, failed in DBOS.read_stream_async(cast(str, self._workflow_id), f"replies_{self._served}"):
            if reply_key == key:
                return present(text, failed)
        raise AssertionError(f"{self.path} ended before its {tool} call was answered")

    async def _serve(self, execution: "DurableExecution[object]", tools: "Mapping[str, tuple[TypeAdapter | None, TypeAdapter]]", func: Callable[..., ResultT], *args: object, preemptible: bool = False) -> ResultT:
        """Run one opaque Step while this Workflow runs the declared methods that Step calls.

        A declared method runs HERE, outside the Step, so what it does may start real child
        Actions through this same context and they belong to the ordinary ownership graph. This
        one workflow task performs every checkpointed Workflow operation, in request-stream
        order, so a recovered epoch repeats them in that same order, re-derives ``served`` from
        the same stream, and answers a duplicated Step-side request from it instead of running
        the method a second time.

        Servicing ends at the end-of-requests mark of the attempt whose Step output was
        recorded, and never at one an interrupted attempt left behind. The Step decides that
        ending and checkpoints it with its result, so this Workflow only ever consumes a
        recorded value. Finishing is not enough on its own to identify the mark: on a replayed
        epoch the Step is already checkpointed and is done before the first request is read, so
        every mark in the stream would satisfy it and servicing would stop at whichever attempt
        wrote the first one, leaving the requests behind it unanswered and every later
        checkpointed operation shifted.

        A method that returns ``Failure`` answers its caller with that reason and an error; the
        method itself decides that. Any other exception is a defect in this framework or in the
        method, so it leaves this Action the ordinary way and its recovery is the ordinary
        durable one. That includes the decode of the request: the arguments were already
        validated against the declared type's own published JSON schema before the caller was
        allowed to send them, so a request that passes there and fails here means the published
        schema and the declared type disagree, which no retry can fix. It fails the same way on
        every epoch, because a recovered epoch decodes the recorded arguments without the
        publishing side running at all, so such a run stops for good rather than looping."""
        self._served += 1
        self._ending = ""
        requests, replies = f"requests_{self._served}", f"replies_{self._served}"
        served: dict[str, tuple[str, bool]] = {}
        # Only the Step task runs in the serving context. This Workflow never
        # enters it, so the servicing below, and every ctx.step a declared method
        # runs, cannot see it at all.
        serving = copy_context()
        serving.run(_SERVING.set, requests)
        task = asyncio.get_running_loop().create_task(self.step(func, *args, preemptible=preemptible), context=serving)
        stream = DBOS.read_stream_async(cast(str, self._workflow_id), requests)
        reading = asyncio.ensure_future(anext(stream, None))
        try:
            while (call := await reading) is not None:
                reading = asyncio.ensure_future(anext(stream, None))
                key, name, arguments = call
                if name is None:
                    if not task.done():
                        await asyncio.wait((reading, task), return_when=asyncio.FIRST_COMPLETED)
                    if task.done():
                        task.result()
                        if key == self._ending:
                            break
                    continue
                if key not in served:
                    request, answer = tools[name]
                    value = await getattr(execution, name)(
                        *(() if request is None else (request.validate_python(arguments),))
                    )
                    failed = isinstance(value, Failure)
                    served[key] = (
                        value.reason
                        if failed
                        else answer.dump_json(answer.validate_python(value)).decode(),
                        failed,
                    )
                await DBOS.write_stream_async(replies, (key, *served[key]))
        except BaseException:
            task.cancel()
            raise
        finally:
            reading.cancel()
        return await task


_KERNEL_RESERVED: frozenset[str] | None = None

# Single-underscore members of a built-in base that code OUTSIDE the engine may
# redefine on purpose: the documented override hooks, plus the ABC bookkeeping
# slot every class gets. Everything else with one leading underscore on an
# ``engine.`` base is kernel-private and a redefinition is a silent shadow.
_EXTENSION_OVERRIDABLE: frozenset[str] = frozenset({
    "_abc_impl",
    "_initial_state",  # Program: "Return this Program identity's initial durable state."
    "_intermediate",  # the documented shared-base marker (meta-search-design SKILL.md)
})


def _kernel_reserved_members() -> frozenset[str]:
    """Names a DurableExecution subclass must not redefine: the kernel's private
    attributes and every member marked ``@final`` on DurableExecution itself.
    Read off the class once, so the guard and the code cannot disagree."""
    global _KERNEL_RESERVED
    if _KERNEL_RESERVED is None:
        names = set(DurableExecution.__private_attributes__)
        for name, member in vars(DurableExecution).items():
            target = member.fget if isinstance(member, property) else member
            target = getattr(target, "__func__", target)
            if getattr(target, "__final__", False):
                names.add(name)
        _KERNEL_RESERVED = frozenset(names)
    return _KERNEL_RESERVED


class DurableExecution(BaseModel, Generic[ExecutionInputT], ABC):
    """One durable state owner: an address, an Input, and typed Action methods."""

    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        frozen=True,
        ignored_types=IGNORED_MODEL_ATTR_TYPES,
    )
    _start_per_attempt: ClassVar[bool] = False

    input: ExecutionInputT = Field(frozen=True)
    # ``_record`` is the one bound-or-unbound fact of an author-side object.
    # Everything below it is live state of the one Action this instance runs.
    _record: ExecutionRecord | None = PrivateAttr(default=None)
    _ctx: ExecutionContext | None = PrivateAttr(default=None)
    # The one direct-call permission the framework grants for the exact
    # Action it is about to run; the @action wrapper consumes it on entry.
    _action_permit: str | None = PrivateAttr(default=None)
    _failure_captured: bool = PrivateAttr(default=False)
    # The recorded Action a managed worker process was launched for. That
    # process has no journal to read one from, so the launch protocol hands it
    # the exact record instead of letting it infer a declaration.
    _worker_action: "ActionRecord | None" = PrivateAttr(default=None)

    @final
    def _recorded_path(self, run_state: "RunState", *, what: str) -> str:
        record = self._record
        if record is None or run_state.records.get(record.path) is not record:
            raise ValueError(f"{what} {type(self).__name__} is not in this run")
        return record.path

    @classmethod
    @final
    def type_key(cls) -> str:
        return f"{cls.__module__}:{cls.__qualname__}"

    def __setattr__(self, name: str, value: object) -> None:
        descriptor = getattr(type(self), name, None)
        if isinstance(descriptor, property) and descriptor.fset is not None:
            descriptor.fset(self, value)
            return
        if isinstance(descriptor, _RuntimeValueDescriptor):
            # Pydantic calls heterogeneous data descriptors through this boundary.
            cast(Any, descriptor).__set__(self, value)
            return
        super().__setattr__(name, value)

    @property
    @final
    def record(self) -> "ExecutionRecord":
        record = self._record
        if record is None:
            raise AssertionError(
                f"{type(self).__name__} is not recorded: an identity has a path, "
                "directory and record only from its first spawn on; read them "
                "after ctx.spawn(...) has started an Action on it, never on a "
                "freshly constructed object"
            )
        return record

    @property
    @final
    def ctx(self) -> ExecutionContext:
        ctx = self._ctx
        if ctx is None:
            raise AssertionError(f"{type(self).__name__} is not running")
        return ctx

    @property
    @final
    def _action(self) -> "ActionRecord":
        """The exact Action this live instance is running.

        A managed worker process runs one Action with no journal of its own,
        so it carries that Action's record from the launch. Every other
        instance reads the live journal, which is the same record."""
        if self._worker_action is not None:
            return self._worker_action
        return self.record.action(self.ctx.ordinal)

    @property
    @final
    def path(self) -> str:
        return self.record.path

    @property
    @final
    def directory(self) -> str:
        return latest_run_state().execution_directory(self.path)

    @property
    def scratch_dir(self) -> str:
        """The identity's scratch directory: a state owner keeps one workspace across its Actions; a WorkUnit answers per Action Attempt instead."""
        return f"{self.directory}/state/scratch"

    @property
    def artifacts_dir(self) -> str:
        return f"{self.directory}/state/artifacts"

    @final
    def files(self, relative: str = "", *, links: bool = False) -> FileRef:
        """A logical reference to this execution's own tree, or to one resource inside it.

        The whole tree with no argument: everything this execution and its
        descendants write, including files that appear after the grant is
        recorded. A ``relative`` name instead identifies one resource this
        execution owns and named itself -- a shared codebase, a record index,
        a board -- so a package can hand a child exactly that resource
        without any central list of resource names. ``links`` says the
        resource is an index whose entries point at directories elsewhere, so
        reading it means reading what it names."""
        return FileRef(
            root=FileRoot.EXECUTION,
            owner=self.path,
            relative=relative,
            links=links,
        )

    @property
    @final
    def uid(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    @final
    def parent_path(self) -> str | None:
        head, separator, _ = self.path.rpartition("/")
        return head if separator else None

    @property
    @final
    def parent(self) -> "DurableExecution | None":
        parent_path = self.parent_path
        if parent_path is None:
            return None
        return latest_run_state().execution_for(parent_path)

    @property
    @final
    def capability(self) -> ExecutionCapability:
        """What the Action this instance is running may spend.

        Read from that Action's own record, so an Attempt and a Resume see
        the identical resolved declaration -- the same seconds, the same
        dollars, the same exact cards -- and nothing above this Action can
        widen or narrow it."""
        return self._action.capability

    def identity_wall_clock_seconds(self) -> float | None:
        """The whole time this identity may spend across every Action it runs.

        None where nothing configures a lifetime for this kind of execution,
        which is every family that is not a configured WorkUnit role."""
        return None

    def identity_cost_cap_usd(self) -> float | None:
        """The whole USD this identity may spend across every Action it runs.

        None where nothing configures a lifetime bill for this kind of
        execution, which is every family that buys nothing."""
        return None

    @classmethod
    def _check_action_capability(cls, capability: ExecutionCapability) -> None:
        """Refuse a declaration this family cannot answer an Action under.

        Asked once, at the spawn that records the Action, so a family whose
        every call needs a clock -- or whose calls may spend nothing at all --
        says so before the Action exists rather than when a launcher reaches
        the missing value."""
        del capability

    @classmethod
    def uid_name(cls) -> str:
        """Return the name used to number same-type siblings."""
        raise AssertionError(f"{cls.__name__} has no UID name")

    def _new_family_record(self) -> object | None:
        return None

    def apply_action_started(self, state: "RunState", event: object) -> None:
        """Apply one declared method invocation after all sources completed."""
        ordinal = cast(int, getattr(event, "ordinal"))
        method = cast(str, getattr(event, "method"))
        request = getattr(event, "request")
        caller_ordinal = cast("int | None", getattr(event, "caller_ordinal"))
        upstream = cast(tuple[tuple[str, int], ...], getattr(event, "upstream"))
        read = cast(tuple[FileRef, ...], getattr(event, "read"))
        write = cast(tuple[FileRef, ...], getattr(event, "write"))
        capability = cast(ExecutionCapability, getattr(event, "capability"))
        if ordinal != len(self.record.actions) + 1 or method not in _legal_action_names(
            type(self)
        ):
            raise ValueError(f"{self.path} has no valid Action #{ordinal} {method!r}")
        _decoded_request(type(self), method, request)
        parent_path = self.record.parent_path
        if parent_path is None:
            if caller_ordinal is not None:
                raise ValueError(f"root {self.path} names a caller Action")
        else:
            if caller_ordinal is None:
                raise ValueError(f"{self.path} #{ordinal} names no caller Action")
            caller = state.records[parent_path].action(caller_ordinal)
            if caller.ended_at_s is not None:
                raise ValueError(
                    f"{parent_path} #{caller_ordinal} has ended and starts no Action"
                )
        for path, source_ordinal in upstream:
            source = state.records.get(path)
            if (
                source is None
                or source_ordinal < 1
                or source_ordinal > len(source.actions)
            ):
                raise ValueError(
                    f"{self.path} names missing Action {path} #{source_ordinal}"
                )
            if source.actions[source_ordinal - 1].ended_at_s is None:
                raise ValueError(
                    f"{self.path} names active Action {path} #{source_ordinal}"
                )
        self.record.actions = (
            *self.record.actions,
            ActionRecord(
                ordinal=ordinal,
                method=method,
                request=request,
                caller_ordinal=caller_ordinal,
                upstream=upstream,
                read=read,
                write=write,
                capability=capability,
                started_at_s=cast(float, getattr(event, "ts")),
            ),
        )

    @final
    def _create_run_dirs(self) -> None:
        """Create the current Attempt directories."""
        for directory in (self.scratch_dir, self.artifacts_dir):
            prepare_dir(directory)

    @final
    def _record_started(self) -> None:
        """Record the start of this Action's current Attempt."""
        from engine.event.events import ActionAttemptStarted

        if self._action.attempt_started_at_s is not None:
            return
        record_durable_event(
            ActionAttemptStarted,
            self,
            ordinal=self.ctx.ordinal,
            workflow_id=self.ctx._workflow_id,
        )

    @final
    async def _run_in_context(
        self, ctx: ExecutionContext, request: object | None
    ) -> SuccessfulOutput | Failure:
        """Run this instance's one Action to its recorded completion."""
        if self._ctx is not None:
            raise AssertionError(f"{self.path} is already running")
        self._ctx = ctx
        self._create_run_dirs()
        if not self._start_per_attempt:
            self._record_started()
        try:
            try:
                async with run_clock().limit(self):
                    output = await self._invoke_action(ctx.method, request)
            except RunTerminalFailure as terminal:
                owner_failure = Failure(
                    kind=FailureKind.PERMANENT,
                    reason=(
                        f"{self.path} ended because required execution "
                        f"{terminal.origin_path} made the run terminal"
                    ),
                )
                await ctx._cancel_started_children(owner_failure)
                self._complete(owner_failure)
                raise
            except _LimitExpired:
                # "cannot outlive its ancestry" holds while the ancestry is
                # running with a finite budget somewhere in it. Two states
                # defeat it, and this Failure is never raised there: a paused
                # Action outside the exploration window (ruled acceptable,
                # 2026-08-14; bounded only by ``llm.retry_ceiling_s``)
                # and the root Search outside that window, which has no Local
                # Budget of its own (known and left alone). Clock.refresh
                # keeps the two apart. Do not "fix" either by clamping the
                # infinite remainder; that kills legitimate out-of-window work.
                failure = Failure(
                    kind=FailureKind.TIMEOUT,
                    reason=(
                        f"{self.path} used all effective wall-clock time; "
                        "an execution cannot outlive its ancestry"
                    ),
                )
                await ctx._cancel_started_children(failure)
                self._complete(failure)
                if not self._failure_captured and self.parent_path is not None:
                    raise RunTerminalFailure(self.path, failure) from None
                return failure
            except ProgramEnvironmentError as exc:
                await ctx._cancel_started_children(exc.failure)
                exit_epoch_recording_failure(self, ctx.ordinal, exc.failure)
            except Exception:
                await ctx._cancel_started_children(
                    Failure(kind=FailureKind.UNEXPECTED, reason="Action failed")
                )
                raise
            self._complete(output)
            return output
        finally:
            self._ctx = None
            self._action_permit = None

    async def _invoke_action(
        self, method_name: str, request: object | None
    ) -> SuccessfulOutput | Failure:
        # The recorded facts reach the implementation as one ordinary bound
        # method call; AIBuildAI does not reimplement Python method binding.
        # The permission is for this exact method and is spent on entry.
        self._action_permit = method_name
        result = getattr(self, method_name)(
            *_decoded_request(type(self), method_name, request)
        )
        if inspect.isawaitable(result):
            result = await result
        return _action_spec(type(self), method_name).output_adapter.validate_python(result)

    @final
    def _complete(self, output: SuccessfulOutput | Failure) -> None:
        """Record this Action's terminal result once, with its family facts."""
        from engine.event.events import ActionCompleted

        ordinal = self.ctx.ordinal
        if self._action.ended_at_s is not None:
            return
        latest_run_state().assert_no_open_children(self.path, ordinal)
        record_durable_events(
            (
                *self._terminal_entries(output),
                (
                    ActionCompleted,
                    self,
                    {"ordinal": ordinal, "output": output.terminal_payload()},
                ),
            )
        )

    def _terminal_entries(self, output: SuccessfulOutput | Failure) -> EventEntries:
        """Family facts committed in the same group as the Action's completion."""
        del output
        return ()

    def apply_attempt_started(
        self, ordinal: int, *, workflow_id: str | None, ts: float
    ) -> None:
        """Open the next Attempt of one exact active Action."""
        action_record = self.record.action(ordinal)
        if action_record.ended_at_s is not None:
            raise AssertionError(f"{self.path} #{ordinal} has ended")
        if action_record.attempt_started_at_s is not None:
            raise AssertionError(f"{self.path} #{ordinal} already has an active Attempt")
        action_record.workflow_id = workflow_id
        action_record.attempts += 1
        action_record.attempt_started_at_s = ts

    def apply_output_recorded(self, ordinal: int, output: object, ts: float) -> None:
        spec = _action_spec(type(self), self.record.action(ordinal).method)
        self._apply_action_output(ordinal, spec.output_adapter.validate_python(output), ts)

    @final
    def settle_lost(self, ordinal: int, failure: Failure) -> None:
        """Settle one exact active Action after its worker is lost."""
        from engine.event.events import ActionCompleted

        if self.record.action(ordinal).ended_at_s is not None:
            return
        latest_run_state().assert_no_open_children(self.path, ordinal)
        record_events(
            (
                (
                    ActionCompleted,
                    self,
                    {"ordinal": ordinal, "output": failure.terminal_payload()},
                ),
            )
        )

    async def notify_stop(self, reason: str) -> None:
        """Tell a live execution to wrap up, so its grace is worth giving."""
        del reason

    @final
    def _apply_action_output(
        self,
        ordinal: int,
        output: SuccessfulOutput | Failure,
        ts: float,
    ) -> None:
        action_record = self.record.action(ordinal)
        if action_record.ended_at_s is not None:
            raise AssertionError(f"{self.path} #{ordinal} already has a result")
        if action_record.attempt_started_at_s is not None:
            action_record.active_s += max(0.0, ts - action_record.attempt_started_at_s)
            action_record.attempt_started_at_s = None
        action_record.output = output.terminal_payload()
        action_record.ended_at_s = ts
        if not isinstance(output, Failure):
            action_record.recorded_failure = None

    def may_retry(self, failure: "Failure") -> "str | None":
        """Return a retry refusal, or None to permit it."""
        del failure
        return None

    @classmethod
    @cache
    @final
    def input_type(cls) -> type[object]:
        """Return this concrete execution's Input type."""
        input_type = cls.model_fields["input"].annotation
        if not isinstance(input_type, type):
            raise AssertionError(f"{cls.__name__} has no concrete Input")
        return input_type

    @classmethod
    @final
    def _is_concrete_definition(cls) -> bool:
        """Return whether this class is one runnable Definition."""
        metadata = getattr(cls, "__pydantic_generic_metadata__", {})
        return metadata.get("origin") is None and not cls.__dict__.get(
            "_intermediate", False
        )

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        super().__pydantic_init_subclass__()
        # A subclass may add fields, Actions and helpers freely, but the private
        # attributes and @final members of DurableExecution are the kernel's own
        # state and identity. Redefining one shadows it silently: a generated
        # program's ``def _record(...)`` helper made ``_record`` hold a function,
        # so ``spawn`` took the object for already-bound and the run died at
        # ``self.record.path`` ("'function' object has no attribute 'path'")
        # after setup and meta had already been paid for. Refusing at class
        # formation names the clash while the author can still rename it, and
        # the package verifier reports it before publication.
        base_private = DurableExecution.__private_attributes__
        own = set(vars(cls)) | {
            name
            for name, attr in cls.__private_attributes__.items()
            if base_private.get(name) is not attr
        }
        clashes = sorted(own & _kernel_reserved_members())
        if clashes:
            raise TypeError(
                f"{cls.__module__}.{cls.__qualname__} redefines "
                f"{', '.join(clashes)}, which DurableExecution reserves; "
                "rename the member (e.g. _record -> _eval_row)"
            )
        # Beyond the kernel's own state: every single-underscore member of a
        # built-in base is engine-private. Built-in classes override those as
        # hooks (``_intermediate``, ``_prepare_conversation``, ...); a class from
        # OUTSIDE the engine -- a generated package or an extension -- has no
        # such contract, and redefining one is always a silent shadow: a
        # generated trainer Program's ``def _run(self, argv, log_path)`` helper
        # replaced ``WorkUnit._run``, the Action driver, and the run died at its
        # first Action with "_run() missing 2 required positional arguments".
        if not cls.__module__.startswith("engine."):
            private = {
                name
                for base in cls.__mro__[1:]
                if base.__module__.startswith("engine.")
                for name in vars(base)
                if name.startswith("_")
                and not name.startswith("__")
                and name not in _EXTENSION_OVERRIDABLE
            }
            shadowed = sorted(own & private)
            if shadowed:
                raise TypeError(
                    f"{cls.__module__}.{cls.__qualname__} redefines "
                    f"{', '.join(shadowed)}, engine-private member(s) of its "
                    "base; rename the helper (e.g. _run -> _run_command)"
                )
        if not cls._is_concrete_definition():
            return
        input_type = cls.model_fields["input"].annotation
        if not isinstance(input_type, type):
            if inspect.isabstract(cls):
                return
            raise AssertionError(
                f"{cls.__name__} must specialize DurableExecution[Input]"
            )
        _assert_frozen_value_type(input_type, f"{cls.__name__}.Input")
        try:
            _settle_action_abi(cls)
        except PydanticUserError as exc:
            raise AssertionError(
                f"{cls.__name__}: an Action Output cannot form the terminal "
                f"Success | Failure union discriminated on 'failed' ({exc}). "
                f"An Output subclasses SuccessfulOutput and never redeclares "
                f"'failed': the base already declares failed: Literal[False] "
                f"= False."
            ) from exc
        key = cls.type_key()
        prior = DURABLE_EXECUTION_TYPES.setdefault(key, cls)
        if prior is not cls:
            raise AssertionError(f"duplicate durable execution type {key!r}")


class ActionRecord(BaseModel):
    """One exact Action occurrence: its request, workflow, Attempts, and result."""

    model_config = ConfigDict(extra="forbid")

    ordinal: int
    method: str
    request: object | None = None
    # The exact Action of the owner identity that started this one; None
    # for a root Action. Completion, cancellation, and lost-descendant
    # settlement walk this relation, never the identity tree.
    caller_ordinal: int | None = None
    upstream: tuple[tuple[str, int], ...]
    # This Action's whole application filesystem authority, beside its request
    # and its sources. Its own workspace and the launch's system necessities
    # are family and runtime facts, and are never repeated here.
    read: tuple[FileRef, ...] = ()
    write: tuple[FileRef, ...] = ()
    # What this one invocation may spend. Its cards are exact physical
    # indices: a count was resolved before this Action was recorded.
    capability: ExecutionCapability = ExecutionCapability()
    started_at_s: float
    workflow_id: str | None = None
    attempts: int = 0
    active_s: float = 0.0
    attempt_started_at_s: float | None = None
    rate_limited_at_s: float | None = None
    recorded_failure: Failure | None = None
    ended_at_s: float | None = None
    output: dict[str, object] | None = None


class ExecutionRecord(BaseModel):
    model_config = ConfigDict(
        arbitrary_types_allowed=True,
        extra="forbid",
        ignored_types=IGNORED_MODEL_ATTR_TYPES,
    )

    path: str = Field(frozen=True, min_length=1)
    parent_action_ordinal: int | None = None
    type_name: str
    input: dict[str, object]
    actions: tuple[ActionRecord, ...] = ()
    charges_exploration_budget: bool = Field(default=False, frozen=True)
    family_state: SkipValidation[object] | None = None

    _execution: DurableExecution[object] = PrivateAttr()

    def model_post_init(self, __context: object) -> None:
        del __context
        self._execution = rebuild_execution(
            resolve_execution_type(self.type_name), self.input
        )
        self._execution._record = self
        family_state = self._execution._new_family_record()
        if family_state is not None:
            object.__setattr__(
                self,
                "family_state",
                TypeAdapter(type(family_state)).validate_python(self.family_state),
            )

    @classmethod
    def from_execution(
        cls,
        path: str,
        execution: DurableExecution[object],
        *,
        parent_action_ordinal: int | None = None,
        charges_exploration_budget: bool = False,
    ) -> "ExecutionRecord":
        execution_type = type(execution)
        registered_type = resolve_execution_type(execution_type.type_key())
        if registered_type is not execution_type:
            raise AssertionError(
                f"execution type {execution_type.type_key()!r} changed identity"
            )
        return cls(
            path=path,
            parent_action_ordinal=parent_action_ordinal,
            type_name=execution_type.type_key(),
            input=cast(
                dict[str, object],
                TypeAdapter(execution_type.input_type()).dump_python(
                    execution.input, mode="json"
                ),
            ),
            charges_exploration_budget=charges_exploration_budget,
            family_state=execution._new_family_record(),
        )

    def action(self, ordinal: int) -> ActionRecord:
        """One exact recorded Action of this identity."""
        if ordinal < 1 or ordinal > len(self.actions):
            raise AssertionError(f"{self.path} has no Action #{ordinal}")
        return self.actions[ordinal - 1]

    @property
    def active_actions(self) -> tuple[ActionRecord, ...]:
        """The Actions of this identity that have not settled."""
        return tuple(item for item in self.actions if item.ended_at_s is None)

    @property
    def execution(self) -> DurableExecution[object]:
        return self._execution

    @property
    def uid(self) -> str:
        return self.path.rsplit("/", 1)[-1]

    @property
    def parent_path(self) -> str | None:
        head, separator, _ = self.path.rpartition("/")
        return head if separator else None


def rebuild_execution(
    execution_type: type["DurableExecution"],
    input: object,
) -> "DurableExecution[object]":
    """Rebuild one identity from its creation facts."""
    return TypeAdapter(execution_type).validate_python({"input": input})


def _assert_frozen_value_type(value_type: object, path: str) -> None:
    if dataclasses.is_dataclass(value_type):
        config = getattr(value_type, "__pydantic_config__", {})
        valid = (
            getattr(value_type, "__dataclass_params__").frozen
            and config.get("extra") == "forbid"
        )
    else:
        valid = (
            isinstance(value_type, type)
            and issubclass(value_type, BaseModel)
            and value_type.model_config.get("frozen") is True
            and value_type.model_config.get("extra") == "forbid"
        )
    if not valid:
        raise AssertionError(f"{path} must be frozen and forbid extra fields")


def running_search() -> "Search":
    """The root Search of the run this process serves."""
    return _services().search


def running_product_search() -> "AIBuildAISearch":
    """The run's root as the product Search the kernel's product hooks call.

    The import is inside the function on purpose: importing the engine must
    never import a Definition package, so the arrow from the kernel to the
    product shell exists only while a product run is actually being served."""
    from engine.builtin.aibuildai.search import AIBuildAISearch

    search = _services().search
    if not isinstance(search, AIBuildAISearch):
        raise AssertionError(f"{type(search).__name__} is not an AIBuildAISearch")
    return search


@DBOS.workflow()
async def execute_durable(
    type_name: str,
    path: str,
    ordinal: int,
    method_name: str,
    request: object | None,
    journal_version: int,
    capture_failure: bool = False,
) -> dict[str, object]:
    """Run one exact Action through the generic DBOS entry point."""
    execution_type = resolve_execution_type(type_name)
    current = run_store().restore(version=journal_version)
    token = _WORKFLOW_RUN_STATE.set(current)
    path_token = _WORKFLOW_PATH.set(path)
    task = asyncio.current_task()
    if task is None:
        raise AssertionError("durable workflow has no asyncio task")
    workflow_id = DBOS.workflow_id
    if workflow_id is None:
        raise AssertionError("durable workflow has no DBOS workflow ID")
    prior_task = _WORKFLOW_TASKS_BY_ID.get(workflow_id)
    if prior_task is not None and prior_task is not task:
        raise AssertionError(f"workflow {workflow_id} already has a live task")
    _WORKFLOW_TASKS_BY_ID[workflow_id] = cast(asyncio.Task[object], task)
    task_token = _WORKFLOW_TASK.set(task)
    try:
        execution = current.execution_for(path)
        if type(execution) is not execution_type:
            raise AssertionError(f"{path} is recorded as {type(execution).__name__}")
        action_record = execution.record.action(ordinal)
        if action_record.method != method_name or action_record.request != request:
            raise AssertionError(
                f"{path} Action #{ordinal} replayed with different facts"
            )
        execution._failure_captured = capture_failure
        try:
            try:
                output = await execution._run_in_context(
                    ExecutionContext(path, ordinal, method_name), request
                )
            except RunTerminalFailure as terminal:
                output = terminal.failure
            except Exception as exc:
                _logger.exception("%s #%d raised an unknown exception", path, ordinal)
                exit_epoch_recording_failure(
                    execution,
                    ordinal,
                    Failure(
                        kind=FailureKind.UNEXPECTED,
                        reason=f"{type(exc).__name__}: {exc}",
                    ),
                )
        except RunSuspending:
            raise asyncio.CancelledError from None
        return {
            "output": output.terminal_payload(),
            "journal_version": current.version,
        }
    finally:
        _WORKFLOW_TASK.reset(task_token)
        _WORKFLOW_PATH.reset(path_token)
        _WORKFLOW_RUN_STATE.reset(token)
        if _WORKFLOW_TASKS_BY_ID.get(workflow_id) is task:
            del _WORKFLOW_TASKS_BY_ID[workflow_id]


def _dbos_application_name(run_id: str) -> str:
    """Encode the complete Run ID inside DBOS's 30-character name limit."""
    value = int(run_id, 16)
    digits = "0123456789abcdefghijklmnopqrstuvwxyz"
    encoded = ""
    while value:
        value, remainder = divmod(value, len(digits))
        encoded = digits[remainder] + encoded
    return f"aib-{encoded.rjust(25, '0')}"


def configure_durable_execution(search: "Search") -> bool:
    """Start DBOS after every service for this run is ready, and say whether this process resumes one.

    A run that has already opened recorded its root ``run`` Action, so the root
    Search's own record is the fact: no Action means this process starts the
    root workflow, an Action means it retrieves the one already running."""
    if _services().search is not search:
        raise AssertionError("durable execution serves a different root Search")
    from engine.event.store import SCHEMA_VERSION
    from infra.postgresql import database_url

    run_id = latest_run_state().require_run_config().run_id
    DBOS.destroy(destroy_registry=False)
    DBOS(
        config=DBOSConfig(
            name=_dbos_application_name(run_id),
            executor_id=str(run_id),
            system_database_url=database_url(),
            system_database_engine=run_store().engine,
            dbos_system_schema=f"dbos_{SCHEMA_VERSION}",
            use_listen_notify=True,
            application_version=APP_VERSION,
            console_log_level="ERROR",
        )
    )
    run_clock().bind_state_provider(workflow_run_state)
    if not isinstance(getattr(_get_dbos_instance(), "_workflow_tasks", None), set):
        raise AssertionError(
            "this DBOS build does not track its workflow tasks in "
            "_workflow_tasks; interrupt teardown cannot join the epoch"
        )
    return bool(search.record.actions)


def launch_durable_execution() -> None:
    """Start DBOS after the caller has opened any resume-only services."""
    DBOS.launch()


def shutdown_durable_execution() -> None:
    """Close this run's DBOS process state."""
    global _RUN
    DBOS.destroy(destroy_registry=False)
    _RUN = None


def _entry_builder(
    entries: EventEntries,
) -> "Callable[[Callable[..., None]], None]":
    def build(emit: Callable[..., None]) -> None:
        for event_type, target, facts in entries:
            emit(event_type, target, **facts)

    return build


def record_durable_events(entries: EventEntries) -> tuple["Event", ...]:
    """Append facts through one DBOS-owned eventsourcing transaction.

    Every durable recording in the product arrives here, so this is where a
    recording proves it got back the fact it asked for. The transaction
    returns whatever the journal recorded at this workflow's step position, so
    a replay that reaches a different durable step than the run that recorded
    them is handed another step's events; naming that here fails at the call
    that lost its place, with the count or the step kinds in the message,
    instead of at the first attribute the wrong event does not carry."""
    events, version = _append(
        entries, live=False, workflow_path=None, workflow_step=None
    )
    if len(events) != len(entries):
        raise AssertionError(
            f"recording {len(entries)} facts returned {len(events)}: this "
            "workflow reached a different durable step than the run that "
            "recorded them, so its replay skipped or added a step"
        )
    for (requested, _, _), recorded in zip(entries, events, strict=True):
        if not isinstance(recorded, requested):
            raise AssertionError(
                f"recording {requested.__name__} returned "
                f"{type(recorded).__name__}: this workflow reached a different "
                "durable step than the run that recorded them, so its replay "
                "skipped or added a step"
            )
    _advance_workflow(version)
    return events


def record_live_events(entries: EventEntries) -> tuple["Event", ...]:
    """Append one live event group through SearchApplication."""
    context = get_local_dbos_context()
    workflow_step = (
        context.curr_step_function_id
        if _uses_workflow_position() and context is not None and context.is_step()
        else None
    )
    workflow_path = _WORKFLOW_PATH.get() if workflow_step is not None else None
    events, version = _append(
        entries,
        live=True,
        workflow_path=workflow_path,
        workflow_step=workflow_step,
    )
    if workflow_step is not None:
        _advance_workflow(version)
    return events


def record_events(entries: EventEntries) -> tuple["Event", ...]:
    """Record facts at the current workflow or live-task position."""
    if _owns_workflow_task() and not _IN_STEP.get():
        return record_durable_events(entries)
    return record_live_events(entries)


def record_durable_event(
    event_type: type[RecordedEventT],
    target: "DurableExecution | str | None",
    **facts: object,
) -> RecordedEventT:
    """Append one fact and return the recorded event, typed as that fact.

    ``record_durable_events`` is where a recording proves it got back what it
    asked for; this only carries that type through to the caller, which is
    what lets its two call sites read the record they just made without a
    cast."""
    return cast(RecordedEventT, record_durable_events(((event_type, target, facts),))[0])


def record_event(
    event_type: type["Event"],
    target: "DurableExecution | str | None",
    **facts: object,
) -> "Event":
    return record_events(((event_type, target, facts),))[0]


def settle_lost_descendants(path: str, ordinal: int, failure: Failure) -> None:
    """Settle every open Action started under one exact Action, deepest first.

    The walk follows each child Action's recorded caller, so a sibling Action
    of the same owner identity, and everything it started, is untouched."""
    state = latest_run_state()
    for record in tuple(state.records.values()):
        if record.parent_path != path:
            continue
        for action_record in record.active_actions:
            if action_record.caller_ordinal != ordinal:
                continue
            settle_lost_descendants(record.path, action_record.ordinal, failure)
            record.execution.settle_lost(action_record.ordinal, failure)


def _append(
    entries: EventEntries,
    *,
    live: bool,
    workflow_path: str | None,
    workflow_step: int | None,
) -> tuple[tuple["Event", ...], int]:
    """Commit pending events, then apply the committed events to live state."""
    workflow_state = _WORKFLOW_RUN_STATE.get()
    token = _WORKFLOW_RUN_STATE.set(None)
    path_token = _WORKFLOW_PATH.set(None)
    task_token = _WORKFLOW_TASK.set(None)
    try:
        events, version = run_store().save_pending(
            _entry_builder(entries),
            ts=run_clock().run_elapsed_s(),
            live=live,
            workflow_path=workflow_path,
            workflow_step=workflow_step,
            durable=not live,
        )
        state = latest_run_state()
        state.fold(events)
        if workflow_state is not None and workflow_state is not state:
            new_events = run_store().select_events(
                originator_id=workflow_state.id,
                gt=workflow_state.version,
                lte=version,
            )
            workflow_state.fold(cast("tuple[Event, ...]", new_events), notify=False)
    finally:
        _WORKFLOW_TASK.reset(task_token)
        _WORKFLOW_PATH.reset(path_token)
        _WORKFLOW_RUN_STATE.reset(token)
    return events, version


async def _close_epoch() -> None:
    """Cancel and join every workflow task in this process epoch."""
    joiner = asyncio.current_task()
    while True:
        await asyncio.sleep(0)
        live = {
            task
            for task in _get_dbos_instance()._workflow_tasks
            if task is not joiner and not task.done()
        }
        if not live:
            return
        for task in live:
            task.cancel()
        await asyncio.gather(*live, return_exceptions=True)


async def _join_cancelled_subtree(workflow_id: str) -> None:
    """Cancel and join every workflow start and task below one workflow."""
    dbos = _get_dbos_instance()
    current = asyncio.current_task()
    cancelled_ids: set[str] = set()
    while True:
        await asyncio.sleep(0)
        workflow_ids = {
            workflow_id,
            *dbos._sys_db.get_workflow_children(workflow_id),
        }
        starts = {
            task
            for parent_id in workflow_ids
            for task in _WORKFLOW_STARTS_BY_PARENT.get(parent_id, ())
            if not task.done()
        }
        if starts:
            await asyncio.gather(
                *(asyncio.shield(task) for task in starts),
                return_exceptions=True,
            )
            continue
        new_ids = workflow_ids - cancelled_ids
        if new_ids:
            await DBOS.cancel_workflows_async(sorted(new_ids), cancel_children=True)
            cancelled_ids.update(workflow_ids)
        tasks = {
            task
            for child_id in workflow_ids
            if (task := _WORKFLOW_TASKS_BY_ID.get(child_id)) is not None
            and task is not current
            and not task.done()
        }
        for task in tasks:
            task.cancel()
        if tasks:
            await asyncio.gather(*tasks, return_exceptions=True)
        latest_ids = {
            workflow_id,
            *dbos._sys_db.get_workflow_children(workflow_id),
        }
        if latest_ids != workflow_ids:
            continue
        if any(
            not task.done()
            for parent_id in latest_ids
            for task in _WORKFLOW_STARTS_BY_PARENT.get(parent_id, ())
        ):
            continue
        active = set(dbos._active_workflows_set.activeList()) & workflow_ids
        if not active and not any(
            task is not current and not task.done()
            for child_id in workflow_ids
            if (task := _WORKFLOW_TASKS_BY_ID.get(child_id)) is not None
        ):
            return


@asynccontextmanager
async def durable_runtime(
    search: "Search", *, resume_services: bool = False
) -> AsyncIterator[bool]:
    """Configure, run, join, and close one durable process epoch."""
    configured = False
    try:
        resuming = configure_durable_execution(search)
        configured = True
        if resume_services and resuming:
            from engine.builtin.aibuildai.search import AIBuildAISearch

            if isinstance(search, AIBuildAISearch):
                _claim_resume_levels(search)
                async with search._checked_mcp_client():
                    launch_durable_execution()
                    yield resuming
                return
        launch_durable_execution()
        yield resuming
    finally:
        # A force cancels the joining supervisor, which lands as a
        # CancelledError inside the JOIN: cutting the wait short is what
        # force means, and CLOSE still runs.
        if _RUN is not None and _RUN.search is search:
            try:
                if configured:
                    await _close_epoch()
            finally:
                shutdown_durable_execution()
        elif configured:
            raise AssertionError("durable runtime lost its configured Search")


async def run_durable(
    search: "Search[SearchInputT, SearchSuccessT]",
) -> "SearchSuccessT | Failure":
    """Run or resume the root Search's ``run`` Action #1."""
    from engine.builtin.aibuildai.search import AIBuildAISearch

    async with durable_runtime(search, resume_services=True) as resuming:
        if isinstance(search, AIBuildAISearch):
            search._joiner = asyncio.current_task()
            run_id = run_configs().run.require_run_id()
        else:
            run_id = latest_run_state().require_run_config().run_id
        if resuming:
            handle = await DBOS.retrieve_workflow_async(run_id)
        else:
            from engine.event.events import ActionStarted

            record_durable_event(
                ActionStarted,
                search,
                ordinal=1,
                method="run",
                request=None,
                caller_ordinal=None,
                upstream=(),
                # The run root is a structural level: ``resources.run`` is the
                # Run cgroup's own ceiling and bounds every Action under it,
                # so repeating it here would be a second aggregate authority.
                capability=ExecutionCapability(),
            )
            with SetWorkflowID(run_id):
                handle = await DBOS.start_workflow_async(
                    execute_durable,
                    type(search).type_key(),
                    search.path,
                    1,
                    "run",
                    None,
                    latest_run_state().version,
                    False,
                )
        payload = await handle.get_result()
    return cast(
        "SearchSuccessT | Failure",
        _action_spec(type(search), "run").output_adapter.validate_python(payload["output"]),
    )


def _claim_resume_levels(search: "AIBuildAISearch") -> None:
    """Claim unfinished owner levels before child recovery starts."""
    from engine.work_unit.base import WorkUnit

    unfinished = [
        record
        for record in latest_run_state().records.values()
        if record.active_actions and not isinstance(record.execution, WorkUnit)
    ]
    for record in sorted(unfinished, key=lambda item: item.path.count("/")):
        run_host().claim_level(record.path)

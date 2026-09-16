"""The durable execution boundary shared by every real unit of work."""

from __future__ import annotations

import logging
import math
from abc import abstractmethod
from collections.abc import Callable
from typing import (
    TYPE_CHECKING,
    Any,
    cast,
    ClassVar,
    Generic,
    overload,
    Self,
    TypeVar,
    final,
)

from pydantic import BaseModel, PrivateAttr
from engine.durable_execution import (
    DurableExecution,
    EventEntries,
    _RuntimeValueDescriptor,
    action_output,
    latest_run_state,
    record_durable_event,
    run_clock,
    run_configs,
    run_host,
    running_product_search,
)
from engine.capability import ExecutionCapability, level_ceilings
from engine.execution_output import SuccessfulOutput
from engine.failure import Failure, FailureKind
from engine.work_unit.events import (
    ActionAttemptFailed,
)

if TYPE_CHECKING:
    from engine.event.base import Event
    from engine.run_state import RunState

WorkUnitInputT = TypeVar("WorkUnitInputT")
_RuntimeValueT = TypeVar("_RuntimeValueT")
logger = logging.getLogger(__name__)


def _checked_wall_clock(
    unit_type: "type[WorkUnit[Any]]", capability: ExecutionCapability
) -> float | None:
    """This Action's own clock, when the call that started it declared one.

    An Action-local clock is an optional extra restriction on the call. The
    identity's configured budget is the lifetime total that bounds it either
    way, so an omitted clock means "nothing stricter than the lifetime", not
    "no limit"."""
    seconds = capability.wall_clock_seconds
    if seconds is None:
        return None
    if not math.isfinite(seconds) or seconds <= 0:
        raise ValueError(
            f"{unit_type.__name__} declares a local wall_clock_seconds that "
            "is not a finite positive number"
        )
    return float(seconds)


class _RuntimeValue(_RuntimeValueDescriptor, Generic[_RuntimeValueT]):
    """Give a WorkUnit a typed view of one runtime-record field."""

    def __init__(self, name: str) -> None:
        self.name = name

    @overload
    def __get__(self, instance: None, owner: type[object] | None = None) -> Self: ...

    @overload
    def __get__(
        self, instance: "WorkUnit", owner: type[object] | None = None
    ) -> _RuntimeValueT: ...

    def __get__(
        self, instance: "WorkUnit | None", owner: type[object] | None = None
    ) -> "_RuntimeValueT | Self":
        del owner
        return (
            self
            if instance is None
            else cast(_RuntimeValueT, getattr(instance._runtime, self.name))
        )

    def __set__(self, instance: "WorkUnit", value: _RuntimeValueT) -> None:
        setattr(instance._runtime, self.name, value)


class WorkUnitRuntimeRecord(BaseModel):
    """The journal facts of one WorkUnit identity."""



class WorkUnit(
    DurableExecution[WorkUnitInputT],
    Generic[WorkUnitInputT],
):
    """One persisted execution governed by resource and sandbox boundaries."""

    _intermediate: ClassVar[bool] = False
    _start_per_attempt: ClassVar[bool] = True
    name: ClassVar[str | None] = None
    kind: ClassVar[str]
    _cgroups: tuple[str, str | None] | None = PrivateAttr(default=None)

    @classmethod
    def _check_action_capability(cls, capability: ExecutionCapability) -> None:
        # Every WorkUnit Action runs against a real deadline, so the call that
        # starts one says how long it may take.
        _checked_wall_clock(cls, capability)

    @property
    def _local_wall_clock(self) -> float | None:
        """This invocation's own settled wall clock, or None when it declared none.

        A unit that starts a child of its own passes this along, so the child
        states the same boundary without asking configuration for a row of its
        own. Nothing is inherited: this is one caller choosing to declare for
        its child exactly what was declared for it."""
        return _checked_wall_clock(type(self), self.capability)

    def identity_wall_clock_seconds(self) -> float | None:
        """The whole time this identity may spend across every Action it runs.

        The configured role budget, which is the lifetime total: a second call
        of the same identity continues to spend it instead of receiving a
        fresh one. A unit that a selected package configures itself has no
        such row and therefore no lifetime total, and its calls are bounded by
        what each one declares and by the Run. That is an ordinary answer
        here, so it is asked as a question; the caller that cannot run
        without a row asks ``work_unit_time_for``, which raises, and the cli
        reports its failure as the user config error it is."""
        name = type(self).name
        if name is None:
            return None
        configs = run_configs()
        time_config = configs.work_unit_time_or_none(type(self).kind, name)
        if time_config is None:
            return None
        return time_config.maximum_seconds(
            pipeline_minutes=configs.run.budget.wall_clock_minutes
        )

    @property
    def _runtime(self) -> WorkUnitRuntimeRecord:
        runtime = self.record.family_state
        if not isinstance(runtime, WorkUnitRuntimeRecord):
            raise AssertionError(f"{self.path!r} has no WorkUnit runtime record")
        return runtime

    def _new_family_record(self) -> WorkUnitRuntimeRecord:
        return WorkUnitRuntimeRecord()

    @staticmethod
    @final
    def placement_key(path: str, ordinal: int) -> str:
        """The host placement address of one exact Action's Attempts.

        Card claims and cgroups are keyed here, below the identity's own
        path, so two Actions of one identity never share a physical slot."""
        return f"{path}/action_{ordinal}"

    @abstractmethod
    async def execute(self, *request: object) -> SuccessfulOutput | Failure:
        """Run one attempt of this unit's real work.

        ``request`` is the running Action's decoded zero-or-one business
        request, forwarded unchanged to every Attempt. A family that declares
        exactly one Action narrows this to that Action's declared result; a
        multi-Action family answers each Action from its own annotation."""

    def accept(self, event: object, ts: float) -> None:
        """Fold one fact this unit owns, or say which fact it does not own."""
        if not self.apply_event(event, ts):
            raise AssertionError(
                f"{type(self).__name__} does not accept {type(event).__name__}"
            )

    def apply_event(self, event: object, ts: float) -> bool:
        """Apply one Event understood by every WorkUnit."""
        del event, ts
        return False

    async def prepare_resources(self) -> None:
        """Place this Action's own process level.

        Nothing is chosen here. This Action's cores, memory and exact cards
        were settled before it was recorded, so an Attempt after a failure and
        a Resume in a new process both place the identical limits instead of
        asking the host again."""
        await self._prepare_live_resources()

    async def _prepare_live_resources(self) -> None:
        """Place a normal local WorkUnit on its own level of the resource tree."""
        process, commands = run_host().place_work_unit(
            self.placement_key(self.path, self.ctx.ordinal),
            self.kind,
            **level_ceilings(self.capability),
        )
        self._cgroups = (str(process), None if commands is None else str(commands))

    async def _release_live_resources(self) -> None:
        """Release a normal local WorkUnit's process tree.

        No card is released, because none was held: a placement says where
        this Action ran, and another Action may share the same card."""
        if self._cgroups is not None:
            run_host().release_work_unit(self.placement_key(self.path, self.ctx.ordinal))
        self._cgroups = None

    @property
    @final
    def cgroups(self) -> tuple[str, str | None]:
        if self._cgroups is None:
            raise AssertionError(f"{self.path!r} has no live resource placement")
        return self._cgroups

    @property
    @final
    def device_indices(self) -> tuple[int, ...]:
        """The exact cards this Action runs on, read from its own declaration.

        The launcher reads the same field of the same record, so the view a
        child process is given and the devices it is actually launched with
        cannot drift apart."""
        return self.capability.gpu_indices

    @property
    @final
    def run_number(self) -> int:
        """Which Attempt of this instance's Action is active, counting from 1."""
        return max(1, self._action.attempts)

    @property
    @final
    def run_dir(self) -> str:
        return latest_run_state().execution_action_dir(
            self.path, self.ctx.ordinal, self.run_number
        )

    def artifacts_dir_at(self, run_state: "RunState", ordinal: int, attempt: int) -> str:
        """This unit's artifacts directory for one exact Action Attempt.

        The Web reads an older occurrence through the same door the writer
        used, so a selected occurrence can never be answered with the latest
        one's files."""
        return f"{run_state.execution_action_dir(self.path, ordinal, attempt)}/artifacts"

    @property
    def artifacts_dir(self) -> str:
        return self.artifacts_dir_at(
            latest_run_state(), self.ctx.ordinal, self.run_number
        )

    @property
    @final
    def step_name(self) -> str:
        """Return the flattened UID path for this Attempt of the WorkUnit."""
        return self.step_name_of(self.ctx.ordinal)

    @final
    def step_name_of(self, ordinal: int) -> str:
        """The flattened UID path of one exact Action's current Attempt."""
        attempts = self.record.action(ordinal).attempts
        return f"{self.path.replace('/', '_')}_action_{ordinal}_attempt_{max(1, attempts)}"

    @final
    def apply_retry(self, event: ActionAttemptFailed, ts: float) -> None:
        """Settle one failed Attempt and prepare the same exact Action's next try."""
        action_record = self.record.action(event.ordinal)
        if action_record.attempt_started_at_s is not None:
            action_record.active_s += max(0.0, ts - action_record.attempt_started_at_s)
            action_record.attempt_started_at_s = None
        action_record.rate_limited_at_s = None
        self._reset_for_retry(event.ordinal)

    def _reset_for_retry(self, ordinal: int) -> None:
        """Drop whatever else this kind wrote about the Attempt that just failed."""
        del ordinal

    async def _run(self, *request: object) -> SuccessfulOutput | Failure:
        """Run Attempts until one settles this Action."""
        from engine.durable_execution import _action_spec

        while True:
            result = await self._run_attempt(*request)
            settled = action_output(self.record, self.ctx.ordinal)
            if settled is not None:
                return settled
            output = cast(
                SuccessfulOutput | Failure,
                _action_spec(type(self), self.ctx.method).output_adapter.validate_python(
                    result["output"]
                ),
            )
            if not self._retry(output):
                return output
            self._create_run_dirs()

    async def _run_attempt(self, *request: object) -> dict[str, object]:
        """Run one attempt in the workflow body."""
        output = await self._execute_attempt(*request)
        return {"output": output.terminal_payload()}

    @final
    def _retry(self, output: SuccessfulOutput | Failure) -> bool:
        """Record one failed Attempt when the Action may try again, and say so."""
        if not isinstance(output, Failure):
            return False
        logger.info("%s failed: %s", self.path, output.reason)
        if not running_product_search().grant_retry(self, output):
            return False
        record_durable_event(ActionAttemptFailed, self, ordinal=self.ctx.ordinal)
        run_clock().refresh(self)
        return True

    def _terminal_entries(self, output: SuccessfulOutput | Failure) -> EventEntries:
        entries: list[
            tuple[type[Event], DurableExecution | str | None, dict[str, object]]
        ] = []

        def emit(
            event_type: type[Event],
            target: DurableExecution | str | None,
            **event_facts: object,
        ) -> None:
            entries.append((event_type, target, event_facts))

        self._emit_attempt_facts(emit, output)
        return cast("EventEntries", tuple(entries))

    def _emit_attempt_facts(
        self,
        emit: Callable[..., None],
        output: SuccessfulOutput | Failure,
    ) -> None:
        del emit, output

    async def _execute_attempt(self, *request: object) -> SuccessfulOutput | Failure:
        """Run the one black-box attempt this step owns, holding its own GPU claim while it runs."""
        try:
            await self.prepare_resources()
            self._record_started()
            output = await self.execute(*request)
            if output is None:
                raise AssertionError(
                    f"{type(self).__name__} returned without an Output"
                )
            if (
                isinstance(output, Failure)
                and not self._failure_captured
                and not running_product_search().grant_retry(self, output)
                and output.kind
                not in (
                    FailureKind.TIMEOUT,
                    FailureKind.COST_LIMIT,
                    FailureKind.PERMANENT,
                )
            ):
                # A non-budget Failure can change when the world, framework,
                # or environment changes. Decide HERE, inside the attempt
                # boundary, so a Program step persists no failed attempt and
                # a later Resume re-attempts it. Hard budget exhaustion returns
                # as a typed terminal Failure instead.
                from engine.durable_execution import exit_epoch_recording_failure

                exit_epoch_recording_failure(self, self.ctx.ordinal, output)
            return output
        finally:
            await self._release_live_resources()

    @property
    def llm_cost(self) -> float:
        """Declare the LLM spend owned by this execution."""
        return 0.0

    def action_llm_cost(self, ordinal: int) -> float:
        """Declare the LLM spend one exact Action of this execution owns."""
        del ordinal
        return 0.0

    @classmethod
    def __pydantic_init_subclass__(cls, **kwargs: object) -> None:
        super().__pydantic_init_subclass__()
        if not cls._is_concrete_definition():
            return
        kind = cls.kind
        if not isinstance(kind, str) or not kind:
            raise AssertionError(
                f"WorkUnit subclass {cls.__name__} must declare a non-empty kind"
            )
        name = cls.name
        if not isinstance(name, str) or not name:
            raise AssertionError(
                f"WorkUnit subclass {cls.__name__} must declare a non-empty name"
            )

    @classmethod
    @final
    def name_segment(cls) -> str:
        """Return this unit's business name."""
        if cls.name is None:
            raise AssertionError(f"{cls.__name__} has no name")
        return cls.name

    @classmethod
    @final
    def uid_name(cls) -> str:
        return cls.name_segment()


async def run_work_unit_attempt(path: str) -> dict[str, object]:
    """Run one WorkUnit attempt as one preemptible black-box step."""

    unit = latest_run_state().execution_for(path)
    if not isinstance(unit, WorkUnit):
        raise TypeError(f"{path} is not a WorkUnit")
    output = await unit._execute_attempt()
    return {"output": output.terminal_payload()}



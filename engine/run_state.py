"""The event-sourced business record of one run.

RunState keeps one flat map of execution records, addressed by journal path such as
``search_1/coder_candidate_3/coder_1``. Ownership is that path: an execution's owner is
the path with its last segment removed. Topology is the second relation: each
Action names the exact upstream Action occurrences it invoked from. Nothing
here holds a second executable tree of Python objects, and nothing here decides
where a program resumes -- DBOS owns the running tree, and this map records what
the run did.
"""

from __future__ import annotations

import logging
from dataclasses import replace
from datetime import datetime
from pathlib import Path
from typing import TYPE_CHECKING, Literal, TypeVar, cast, get_type_hints
from uuid import UUID

import orjson
from eventsourcing.domain import Aggregate, event
from pydantic import TypeAdapter, model_validator
from pydantic_core import to_json

from engine.metric_contract import MetricContractInput
from engine.base import WorkflowBaseModel
from engine.paths import WORKSPACE_DIRNAME
from engine.failure import Failure
from engine.event.events import RunOpened
from engine.composite import Composite
from engine.work_unit.base import WorkUnit
from engine.work_unit.agent.base import Agent


if TYPE_CHECKING:
    from collections.abc import Collection

    from engine.capability import ExecutionCapability
    from engine.durable_execution import DurableExecution, ExecutionRecord, FileRef
    from engine.event.base import Event
    from engine.builtin.aibuildai.search import AIBuildAISearch
    from engine.search.base import Search


_logger = logging.getLogger(__name__)


WorkUnitT = TypeVar("WorkUnitT", bound=WorkUnit)


def _rooted_capability(
    capability: "ExecutionCapability", *, what: str
) -> "ExecutionCapability":
    """Resolve a root Action's cards the way every other Action's are resolved.

    A recorded Action always names exact physical cards, so a root one may not
    be recorded holding a request. Nothing here observes the host: zero cards
    is zero cards, and named cards are checked against the run's own. A root
    Action cannot ask the host to CHOOSE cards, because the durable step that
    would settle that choice belongs to a caller it does not have -- so it
    names them or asks for none."""
    from engine.durable_execution import run_host

    requested = capability.gpus
    if requested is None or requested == ():
        return capability
    if isinstance(requested, tuple):
        return capability.model_copy(
            update={"gpus": run_host().placed_gpus(requested, what=what)}
        )
    if requested == 0:
        return capability.model_copy(update={"gpus": ()})
    raise ValueError(
        f"{what} is a root Action asking for {requested} card(s) to be "
        "chosen; a root Action names the exact cards it runs on"
    )


class RunWarningRecord(WorkflowBaseModel):
    """One run-level content-contract downgrade, rebuilt from a RunWarning event so the dashboard overview count and the run-summary listing render identically live and on replay.

    source is the emitting stage boundary; category is the stable downgrade-kind tag; message is the one-line human explanation. at_s is the run-relative elapsed seconds at which the warning was recorded, so a rendered timestamp is identical live and on replay. The ``run_warnings`` list is a lasting record (persisted in the event store and not cleared on resume): a downgrade that happened is a permanent fact of the run."""

    source: str
    category: str
    message: str
    at_s: float = 0.0


class RunConfigRecord(WorkflowBaseModel):
    """The run facts settled in the first atomic journal commit.

    The Run Budget and Work Unit time rules stay fixed for this run. A later
    process uses these facts even if the archived config changed."""

    run_id: str
    task_name: str
    model: str
    cost_budget_usd: float | None
    run_budget_s: float
    work_units: list[dict[str, object]]
    models_routed: bool
    is_anthropic: bool

    @model_validator(mode="after")
    def _reject_empty_strings(self) -> "RunConfigRecord":
        for field in ("run_id", "task_name", "model"):
            if not getattr(self, field):
                raise AssertionError(f"run_config.{field} must be non-empty")
        # null is the one spelling of no limit, here as in RunBudgetConfig. A
        # journal written before that rule stores 0.0 for "no limit"; say so
        # plainly rather than let it reach the cost panel's headline / cap
        # division. SCHEMA_VERSION refuses such a journal on the normal replay
        # path; this catches the --allow-version-mismatch path too.
        if self.cost_budget_usd is not None and self.cost_budget_usd <= 0:
            raise AssertionError(
                "run_config.cost_budget_usd must be > 0 when set; null "
                "means no limit. A stored 0.0 comes from a journal written "
                "before schema 26, when 0 meant unlimited."
            )
        if self.run_budget_s <= 0:
            raise AssertionError("run_config.run_budget_s must be > 0")
        return self


def merge_generated_definition(
    definitions: "list[tuple[str, str]]",
    module_name: str,
    package_relpath: str,
) -> None:
    """Fold one publication into the packages this run has published.

    What a REPEAT means is one rule, and this is it: a publication is recorded
    every time the Search reaches it, because a caller inside a workflow may
    never decide from folded state whether to record a durable step, so the
    same module at the same path arrives more than once and is the same
    publication. Only a module recorded at two different paths is a conflict.

    Two readers answer that question -- the run state's own fold and the
    journal reader that activates packages before a restore -- and they answer
    it here, together, so a stream one accepts cannot be a stream the other
    rejects.
    """
    for name, published in definitions:
        if name != module_name:
            continue
        if published != package_relpath:
            raise ValueError(
                f"generated package {module_name!r} changed path from "
                f"{published!r} to {package_relpath!r}"
            )
        return
    definitions.append((module_name, package_relpath))


class _RunStateMethods:
    """Queries and recorded changes owned by RunState."""

    ROOT_PATH = "search_1"

    records: dict[str, "ExecutionRecord"]
    search: "Search"
    run_home: str
    run_started_at_unix_s: float
    durable_elapsed_floor_s: float
    generated_definitions: list[tuple[str, str]]

    def publish_generated_definition(
        self, module_name: str, package_relpath: str
    ) -> None:
        """Record one generated package before any execution uses it."""
        if not module_name.startswith("aibuildai_meta_"):
            raise ValueError(f"invalid generated module name {module_name!r}")
        if Path(package_relpath).is_absolute() or ".." in Path(package_relpath).parts:
            raise ValueError(f"invalid generated package path {package_relpath!r}")
        merge_generated_definition(
            self.generated_definitions, module_name, package_relpath
        )

    def execution_for(self, path: str) -> "DurableExecution":
        """Resolve one journal address."""
        record = self.records.get(path)
        if record is None:
            raise AssertionError(f"missing execution {path!r}")
        return record.execution

    def settle_clock(self, elapsed_s: float) -> None:
        """Take the run clock's latest reading."""
        self.durable_elapsed_floor_s = max(self.durable_elapsed_floor_s, elapsed_s)

    def workspace_directory(self) -> Path:
        """The run's workspace root, the one owner of that formula: every execution directory and the Web workspace browser derive from it."""
        return Path(self.run_home) / WORKSPACE_DIRNAME

    def execution_directory(self, path: str) -> str:
        """The directory one execution owns: workspace root + durable path segments."""
        return f"{self.workspace_directory()}/{path}"

    def execution_action_dir(self, path: str, ordinal: int, attempt: int) -> str:
        """The directory one Attempt of one Action owns.

        Reader and writer share this one formula, so the Web can address an
        older occurrence without keeping a second copy of the layout."""
        return f"{self.execution_directory(path)}/actions/action_{ordinal}/attempt_{attempt}"

    def children_of(self, path: str) -> tuple["DurableExecution", ...]:
        """Return the executions recorded directly under one address."""
        return tuple(
            execution
            for record in self.records.values()
            if record.parent_path == path
            for execution in (record.execution,)
        )

    def descendants_of(self, path: str) -> tuple["DurableExecution", ...]:
        """Return every execution recorded below one address."""
        prefix = f"{path}/"
        return tuple(
            execution
            for child_path, record in self.records.items()
            if child_path.startswith(prefix)
            for execution in (record.execution,)
        )

    @property
    def composites(self) -> tuple["Composite", ...]:
        """Every Composite of the run in durable creation order.

        Durable paths are the run-global identity. A Composite UID is only its
        display label among one owner's direct children."""
        return tuple(
            execution
            for record in self.records.values()
            for execution in (record.execution,)
            if isinstance(execution, Composite)
        )

    def walk_units(self):
        """Yield every WorkUnit this run recorded."""
        for record in tuple(self.records.values()):
            execution = record.execution
            if isinstance(execution, WorkUnit):
                yield execution

    def assert_no_open_children(self, path: str, ordinal: int) -> None:
        """Refuse to end one Action while a child it recorded still has an open Action.

        Structured direct-child lifetime is universal, so this asks only what this state holds: joining, independent completion, and cancellation all close a child Action, and walking away from one does not. It reads nothing outside the state it is given, so the live preflight before a terminal event is written and the fold that later applies that event ask exactly the same question."""
        open_children = tuple(
            f"{record.uid} #{child_action.ordinal}"
            for record in self.records.values()
            if record.parent_path == path
            for child_action in record.active_actions
            if child_action.caller_ordinal == ordinal
        )
        if open_children:
            raise AssertionError(
                f"{path} #{ordinal} cannot end with open {open_children}"
            )

    @staticmethod
    def require_agent(unit: WorkUnit) -> Agent:
        """Read one resolved unit as the Agent an Agent fact needs."""
        if not isinstance(unit, Agent):
            raise AssertionError(
                f"Agent fact requires an Agent sender, got {type(unit).__name__!r}"
            )
        return unit

    def attach_execution(
        self,
        *,
        type_name: str,
        uid: str,
        parent_path: str | None,
        parent_action_ordinal: int | None,
        input: object,
    ) -> "DurableExecution":
        """Apply one recorded create fact under the exact owner Action.

        Action topology is recorded only when an Action starts."""
        from engine.durable_execution import rebuild_execution, resolve_execution_type

        execution_type = resolve_execution_type(type_name)
        execution = rebuild_execution(execution_type, input)
        path = uid if parent_path is None else f"{parent_path}/{uid}"
        if path in self.records:
            raise AssertionError(f"duplicate execution {path!r}")
        if parent_path is not None:
            if parent_action_ordinal is None:
                raise AssertionError(f"{path} names no owner Action")
            owner = self.records[parent_path].action(parent_action_ordinal)
            if owner.ended_at_s is not None:
                raise AssertionError("an ended Action cannot add a child")
        from engine.durable_execution import ExecutionRecord

        self.records[path] = ExecutionRecord.from_execution(
            path,
            execution,
            parent_action_ordinal=parent_action_ordinal,
            charges_exploration_budget=(
                parent_path is not None and self.charges_exploration_budget(parent_path)
            ),
        )
        return self.records[path].execution

    # --- Topology: Action references are the only producer relation. ---
    def upstream_of(self, path: str, ordinal: int) -> "tuple[DurableExecution, ...]":
        """Return the source identities of one exact Action, in declared order."""
        record = self.records.get(path)
        if record is None:
            raise AssertionError(f"missing execution {path!r}")
        return tuple(
            self.execution_for(source_path)
            for source_path, _ in record.action(ordinal).upstream
        )

    def _exploration_root(self) -> "AIBuildAISearch | None":
        """The product root whose record carries the exploration window, if any."""
        from engine.builtin.aibuildai.search import AIBuildAISearch

        record = self.records.get("search_1")
        execution = None if record is None else record.execution
        return execution if isinstance(execution, AIBuildAISearch) else None

    def exploration_window(self) -> "tuple[float | None, float | None]":
        """The recorded exploration boundaries ``(started_at_s, finished_at_s)``.

        ``(None, None)`` outside a product run or before exploration starts."""
        root = self._exploration_root()
        if root is None:
            return (None, None)
        return (root.exploration_started_at_s, root.exploration_finished_at_s)

    def charges_exploration_budget(self, owner_path: str) -> bool:
        """Whether work created under this owner right now spends the exploration budget.

        The scope belongs to the invocation, never to a class. The run root's
        own children are inside the budget exactly when the exploration window
        is open as they are created, and everything deeper inherits its
        owner's answer. So Setup and its verifiers, the Submitter, the
        Finalizer, the Writer, and the run-level Router before Setup all stay
        outside, while Meta authoring, its review, the generated Search, and
        everything under it -- a per-execution Router included -- are inside. The
        run decides this and records it, so generated code cannot place its
        own work outside the budget."""
        if owner_path == "search_1":
            started_s, finished_s = self.exploration_window()
            return started_s is not None and finished_s is None
        return self.records[owner_path].charges_exploration_budget

    def exploration_cost_used_usd(self) -> float:
        """USD the exploration window has consumed, frozen at the recorded finish.

        The sum of tracked spend over the Agents whose own invocation was
        created inside the exploration window: Meta authoring and review, and
        every authored or algorithm role under the generated Search. A
        Submitter running beside the exploration charges nothing to it, and
        neither does Setup before it or the Finalizer after it."""
        root = self._exploration_root()
        if root is None:
            return 0.0
        frozen = root.exploration_cost_usd
        if frozen is not None:
            return frozen
        if root.exploration_started_at_s is None:
            return 0.0
        total = 0.0
        for unit in self.walk_units():
            if (
                unit.kind != "llm_agent"
                or not self.records[unit.path].charges_exploration_budget
            ):
                continue
            cost = getattr(unit, "cost", None)
            if cost is None:
                raise AssertionError(
                    f"Agent {type(unit).__name__} does not expose cost"
                )
            total += cost.total_cost_usd
        return total

    def own_cost_usd(self, path: str, ordinal: int | None = None) -> float:
        """USD one identity's own provider work recorded. Zero for a non-Agent.

        This identity's LIFETIME bill, which ``run.budget.per_agent_cost_cap_usd``
        is compared with. Naming an ``ordinal`` narrows it to that one Action,
        which is what its own ``cost_cap_usd`` is compared with. A durable child
        Agent owns a separate ``Cost`` record and appears in neither."""
        execution = self.execution_for(path)
        if not isinstance(execution, WorkUnit):
            return 0.0
        if ordinal is None:
            return execution.llm_cost
        return execution.action_llm_cost(ordinal)

    def subtree_cost_usd(self, path: str) -> float:
        """USD spend recorded under one execution path, itself included.

        A REPORTING roll-up, for a Composite or UI row that answers "what did
        all the work below here cost?". It is not a budget: no cap is compared
        with it, and it never decides whether work may start. Records are keyed
        by durable path, ``walk_units`` yields each unit once, and ``llm_cost``
        is its folded total, so each cost fact is added once."""
        prefix = f"{path}/"
        total = 0.0
        for unit in self.walk_units():
            if unit.kind != "llm_agent":
                continue
            unit_path = unit.path
            if unit_path == path or unit_path.startswith(prefix):
                total += unit.llm_cost
        return total


def _snapshot_state(value: dict[str, object]) -> dict[str, object]:
    from engine.durable_execution import ExecutionRecord

    snapshot_type = type(
        "_RunStateSnapshot", (), {"__annotations__": RunState.__annotations__}
    )
    types = get_type_hints(
        snapshot_type,
        globalns=globals(),
        localns={"ExecutionRecord": ExecutionRecord},
    ) | {"_created_on": datetime, "_modified_on": datetime}
    if value.keys() != types.keys():
        raise TypeError(
            "RunState snapshot fields differ: "
            f"missing={sorted(types.keys() - value.keys())}, "
            f"unexpected={sorted(value.keys() - types.keys())}"
        )
    return {
        name: TypeAdapter(value_type).validate_python(value[name])
        for name, value_type in types.items()
    }


class RunState(Aggregate, _RunStateMethods):
    """The event-sourced business record of one run.

    ``records`` is the flat runtime record map, addressed by journal path. The remaining fields are run-wide facts and infrastructure observations.
    """

    INITIAL_VERSION = 2

    records: dict[str, "ExecutionRecord"]
    # The run's own directory, and the run clock. Every one of these is a fact
    # about the run, not about any one Search, so they sit beside the record map
    # rather than inside the root Search's own journal state.
    run_home: str
    run_started_at_unix_s: float
    durable_elapsed_floor_s: float
    generated_definitions: list[tuple[str, str]]
    # Run-level content-contract downgrades, rebuilt from
    # RunWarning events. A durable accumulating list, so it surfaces in the
    # run-end summary and survives a resume. Replay rebuilds the same list from
    # the RunWarning events.
    run_warnings: list["RunWarningRecord"]
    versions_used: int
    metric_contract: "MetricContractInput | None"
    run_config: "RunConfigRecord | None"
    # The exact Action of the newest recoverable Failure recorded in the
    # current process epoch, or None. Cleared when a new epoch opens; the
    # failure itself sits on that Action's record, and history stays in the events.
    recoverable_failure: "tuple[str, int] | None"

    @staticmethod
    def create_id(run_id: str) -> UUID:
        return UUID(hex=run_id)

    def __deepcopy__(self, memo: dict[int, object]) -> "RunState":
        """Copy through the same typed state boundary snapshots use."""
        del memo
        snapshot = self.Snapshot.take(self)
        json_state = orjson.loads(to_json(snapshot.state))
        return replace(snapshot, state=json_state).mutate(None)

    class Snapshot(Aggregate.Snapshot):
        def mutate(self, _: None) -> "RunState":
            snapshot = replace(self, state=_snapshot_state(self.state))
            return cast("RunState", Aggregate.Snapshot.mutate(snapshot, None))

    @event(RunOpened)
    def __init__(
        self,
        *,
        run_id: str,
        run_home: str,
        app_version: str,
        gpu_visibility: str | None,
        archive_location: str,
        schema_version: int,
        search_kind: str,
        search_input: dict,
        run_started_at_unix_s: float,
        ts: float,
        timestamp: datetime,
    ) -> None:
        if self.id != self.create_id(run_id):
            raise AssertionError("RunState identity differs from its Run ID")
        del app_version, archive_location
        del gpu_visibility, schema_version, timestamp
        from engine.builtin import search_type_for_kind

        # Resume loads the archived kind's own package and the shared product
        # Definitions it needs, never every built-in package.
        search_type = search_type_for_kind(search_kind)
        search = search_type(
            input=TypeAdapter(search_type.input_type()).validate_python(search_input),
        )
        from engine.durable_execution import ExecutionRecord

        self.records = {
            self.ROOT_PATH: ExecutionRecord.from_execution(self.ROOT_PATH, search)
        }
        self.run_home = run_home
        self.run_started_at_unix_s = run_started_at_unix_s
        self.durable_elapsed_floor_s = 0.0
        self.generated_definitions = []
        self.run_warnings = []
        self.versions_used = 0
        self.metric_contract = None
        self.run_config = None
        self.recoverable_failure = None

    def fold(self, events: "tuple[Event, ...]", *, notify: bool = True) -> None:
        """Apply one committed event group, in order, exactly once.

        The run's own bookkeeping, so it lives with the state it changes: an
        event already folded is skipped, a gap in the sequence is refused, and
        every log line an event added is said as it arrives. The root Search
        gets each event after it is applied, so a concrete Search may keep views
        of its own in step with the record."""
        for ev in events:
            if ev.seq <= self.version:
                continue
            if ev.seq != self.version + 1:
                raise AssertionError(
                    f"journal event {ev.seq} cannot follow {self.version}"
                )
            ev.mutate(self)
            if notify:
                self.search._after_event_saved(
                    ev,
                    None if ev.target is None else self.execution_for(ev.target),
                    event_ts=ev.ts,
                    now=ev.timestamp.timestamp(),
                )

    def record_recoverable_failure(
        self, path: str, ordinal: int, failure: Failure
    ) -> None:
        """Keep the latest recoverable Failure on the exact Action it came from."""
        self.records[path].action(ordinal).recorded_failure = failure
        self.recoverable_failure = (path, ordinal)

    def latest_recoverable_failure(self) -> "tuple[str, Failure] | None":
        """The newest unresolved recoverable Failure of this epoch, if any."""
        if self.recoverable_failure is None:
            return None
        path, ordinal = self.recoverable_failure
        failure = self.records[path].action(ordinal).recorded_failure
        if failure is None:
            return None
        return (path, failure)

    def require_run_config(self) -> RunConfigRecord:
        if self.run_config is None:
            raise AssertionError("RunConfigRecorded has not been applied")
        if not isinstance(self.run_config, RunConfigRecord):
            raise AssertionError(
                f"run_config must be RunConfigRecord, got {type(self.run_config).__name__}"
            )
        return self.run_config

    def exploration_cost_remaining_usd(self) -> float | None:
        """The exploration cost budget that remains, or None when no global cost limit is configured."""
        limit = self.require_run_config().cost_budget_usd
        if limit is None:
            return None
        return max(0.0, limit - self.exploration_cost_used_usd())

    @property
    def search(self) -> "Search":
        """The run root. Any Search may be one, not only a built-in method."""
        from engine.search.base import Search

        search = self.execution_for(self.ROOT_PATH)
        if not isinstance(search, Search):
            raise AssertionError("the run root is not a Search")
        return search

    @property
    def aibuildai_search(self) -> "AIBuildAISearch":
        """The run root as the product's own Search.

        Product code reads Setup, grading, composite, and delivery services off
        the root; this is where that narrowing happens, so the universal
        runtime never has to assume them."""
        from engine.builtin.aibuildai.search import AIBuildAISearch

        search = self.search
        if not isinstance(search, AIBuildAISearch):
            raise AssertionError(f"{type(search).__name__} is not an AIBuildAISearch")
        return search

    def create_root(
        self,
        target: object,
        request: object,
        *,
        capability: "ExecutionCapability | None" = None,
        upstream: tuple[tuple[str, int], ...] = (),
        read: "Collection[FileRef]" = (),
        write: "Collection[FileRef]" = (),
    ) -> "tuple[DurableExecution, int]":
        """Record one identity with no owner and its Action #1; return both.

        The other door an Action is born through. Everything ``ctx.spawn``
        settles before recording is settled here too -- the grants it may
        read and write, and cards resolved to exact indices -- because an
        Action with a different birth story would be a second kind of
        Action."""
        from engine.durable_execution import (
            _action_target,
            _encoded_request,
            declared_grants,
            declared_upstream,
            record_durable_events,
        )
        from engine.capability import ExecutionCapability
        from engine.event.events import ActionStarted, ExecutionCreated

        execution, method = _action_target(target)
        execution_type = type(execution)
        same_name = sum(
            record.parent_path is None
            and type(record.execution).uid_name() == execution_type.uid_name()
            for record in self.records.values()
        )
        uid = f"{execution_type.uid_name()}_{same_name + 1}"
        record_durable_events(
            (
                (
                    ExecutionCreated,
                    None,
                    {
                        "type_name": execution_type.type_key(),
                        "uid": uid,
                        "input": TypeAdapter(execution_type.input_type()).dump_python(
                            execution.input,
                            mode="json",
                        ),
                    },
                ),
                (
                    ActionStarted,
                    uid,
                    {
                        "ordinal": 1,
                        "method": method,
                        "request": _encoded_request(execution_type, method, request),
                        "caller_ordinal": None,
                        "upstream": declared_upstream(upstream),
                        "read": declared_grants(read),
                        "write": declared_grants(write),
                        "capability": _rooted_capability(
                            capability or ExecutionCapability(), what=uid
                        ),
                    },
                ),
            )
        )
        execution._record = self.records[uid]
        return self.records[uid].execution, 1

    def work_units_of_type(
        self,
        parent: "DurableExecution | None",
        unit_type: type[WorkUnitT],
    ) -> tuple[WorkUnitT, ...]:
        children = (
            tuple(
                execution
                for record in self.records.values()
                for execution in (record.execution,)
                if record.parent_path is None
            )
            if parent is None
            else self.children_of(parent.path)
        )
        return tuple(child for child in children if isinstance(child, unit_type))

    def push_notebook_version(self) -> None:
        """Count one notebook version this run pushed to Kaggle."""
        self.versions_used += 1

    def record_run_config(self, run_config: RunConfigRecord) -> None:
        if self.run_config is not None:
            raise AssertionError("RunConfigRecorded may occur only once")
        self.run_config = run_config

    def record_metric_contract(
        self,
        *,
        metric_name: str,
        metric_direction: Literal["max", "min"],
    ) -> None:
        """Freeze the run's metric contract, which one run states once."""
        recorded = MetricContractInput(
            metric_name=metric_name,
            metric_direction=metric_direction,
        )
        if self.metric_contract is not None and self.metric_contract != recorded:
            raise AssertionError(
                f"metric contract changed from {self.metric_contract!r} to {recorded!r}"
            )
        self.metric_contract = recorded

    def record_warning(
        self,
        *,
        source: str,
        category: str,
        message: str,
        ts: float,
    ) -> None:
        """Keep one run-level downgrade, and say it."""
        self.run_warnings.append(
            RunWarningRecord(
                source=source,
                category=category,
                message=message,
                at_s=ts,
            )
        )


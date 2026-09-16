"""Facts recorded by RunState.

RunState applies run facts. Addressed executions apply their own facts. Every event is a frozen data class. An addressed event names its target by that target's journal path.
"""

from __future__ import annotations

from dataclasses import dataclass, field
from pathlib import Path
from typing import TYPE_CHECKING, Literal

from eventsourcing.domain import Aggregate

from engine.capability import ExecutionCapability
from engine.durable_execution import FileRef
from engine.event.base import Event
from engine.failure import Failure

if TYPE_CHECKING:
    from engine.builtin.aibuildai.io import SearchOutput
    from engine.run_state import RunConfigRecord, RunState


@dataclass(frozen=True)
class GeneratedDefinitionPublished(Event):
    """One generated package became available to this run."""

    module_name: str
    package_relpath: str

    def __post_init__(self) -> None:
        path = Path(self.package_relpath)
        if not self.module_name.startswith("aibuildai_meta_"):
            raise ValueError(f"invalid generated module name {self.module_name!r}")
        if path.is_absolute() or ".." in path.parts:
            raise ValueError(f"invalid generated package path {self.package_relpath!r}")

    def apply(self, state: "RunState") -> None:
        state.publish_generated_definition(self.module_name, self.package_relpath)


@dataclass(frozen=True)
class ExecutionCreated(Event):
    """One execution was recorded under its owner with its frozen Input.

    The one creation fact for every family: the type name resolves the concrete
    class, and the target names the owner's journal path."""

    type_name: str
    uid: str
    input: dict[str, object]
    # The exact owner Action that recorded this identity; None for a root.
    parent_action_ordinal: int | None = None

    def __post_init__(self) -> None:
        if not isinstance(self.input, dict):
            raise TypeError("ExecutionCreated.input must be a JSON object")

    def apply(self, state: "RunState") -> None:
        state.attach_execution(
            type_name=self.type_name,
            uid=self.uid,
            parent_path=self.target,
            parent_action_ordinal=self.parent_action_ordinal,
            input=self.input,
        )


@dataclass(frozen=True)
class ActionStarted(Event):
    """One typed method invocation was accepted on an identity."""

    ordinal: int
    method: str
    request: object | None
    upstream: tuple[tuple[str, int], ...]
    # The logical filesystem resources this Action may read, and the ones it
    # may also write. Recorded here so a restored run rebuilds the same
    # authority without any host path or live object.
    read: tuple[FileRef, ...] = ()
    write: tuple[FileRef, ...] = ()
    # What this invocation may spend, with any card count already resolved
    # into exact physical indices. Recorded once, so every Attempt and any
    # later Resume runs under the identical declaration.
    capability: ExecutionCapability = field(default_factory=ExecutionCapability)
    # The exact owner Action that started this one; None for a root Action.
    caller_ordinal: int | None = None

    def apply(self, state: "RunState") -> None:
        self.execution(state).apply_action_started(state, self)


@dataclass(frozen=True)
class ActionAttemptStarted(Event):
    """One physical try began inside an Action workflow."""

    ordinal: int
    workflow_id: str | None

    def apply(self, state: "RunState") -> None:
        self.execution(state).apply_attempt_started(
            self.ordinal, workflow_id=self.workflow_id, ts=self.ts
        )


@dataclass(frozen=True)
class ExecutionFailureRecorded(Event):
    """One recoverable Failure, recorded once on the exact Action where it originated.

    Non-terminal: it closes nothing. The originating Action and every
    waiting owner stay unfinished, the current process epoch ends, and a
    later Resume continues the same unfinished DBOS workflow graph in place.
    Repeat failures are repeat events; every count is a projection over them."""

    ordinal: int
    failure: Failure

    def apply(self, state: "RunState") -> None:
        record = self.execution(state).record
        if record.action(self.ordinal).ended_at_s is not None:
            raise AssertionError(
                f"{self.target} #{self.ordinal} recorded a Failure after it ended"
            )
        state.record_recoverable_failure(record.path, self.ordinal, self.failure)


@dataclass(frozen=True)
class SearchExplorationStarted(Event):
    """One Search began its exploration: the budget window opens at this fact."""

    def apply(self, state: "RunState") -> None:
        self._product_search(state).start_exploration(ts=self.ts)


@dataclass(frozen=True)
class SearchExplorationFinished(Event):
    """One Search finished its exploration.

    The budget window closes at this fact: the exploration cost -- the
    exploration-budgeted Agents' spend the fold sums from the journal --
    freezes here, so later product-tail spend never changes it."""

    def apply(self, state: "RunState") -> None:
        self._product_search(state).finish_exploration(
            ts=self.ts,
            exploration_cost_usd=state.exploration_cost_used_usd(),
        )


@dataclass(frozen=True)
class SearchResultSelected(Event):
    """Replace the Search's recorded result selection."""

    output: "SearchOutput"

    def apply(self, state: "RunState") -> None:
        self._product_search(state).accept_selected(self.output)


@dataclass(frozen=True)
class ActionCompleted(Event):
    """One Action settled with its sole typed business result."""

    ordinal: int
    output: dict[str, object]

    def __post_init__(self) -> None:
        if not isinstance(self.output, dict):
            raise TypeError("ActionCompleted.output must be a JSON object")

    def apply(self, state: "RunState") -> None:
        execution = self.execution(state)
        state.assert_no_open_children(execution.path, self.ordinal)
        execution.apply_output_recorded(self.ordinal, self.output, self.ts)


@dataclass(frozen=True)
class NotebookVersionPushed(Event):
    """One notebook version this run pushed to Kaggle.

    The relay counts pushes in memory, so this event is what lets a resumed run restart it at the count the journal already holds."""

    def apply(self, state: "RunState") -> None:
        state.push_notebook_version()


class RunClockHeartbeat(Event):
    """Periodic durable floor for Run Time."""

    elapsed_s: float

    def apply(self, state: "RunState") -> None:
        state.settle_clock(self.elapsed_s)


@dataclass(frozen=True)
class RunConfigRecorded(Event):
    """The run facts settled once in the first atomic journal commit."""

    run_config: "RunConfigRecord"

    def __post_init__(self) -> None:
        from engine.run_state import RunConfigRecord

        if not isinstance(self.run_config, RunConfigRecord):
            object.__setattr__(
                self,
                "run_config",
                RunConfigRecord.model_validate(self.run_config),
            )

    def apply(self, state: "RunState") -> None:
        state.record_run_config(self.run_config)


@dataclass(frozen=True)
class MetricContractRecorded(Event):
    """The run's frozen metric name and optimization direction."""

    metric_name: str
    metric_direction: Literal["max", "min"]

    def __post_init__(self) -> None:
        if not self.metric_name:
            raise ValueError("metric_name must not be empty")

    def apply(self, state: "RunState") -> None:
        state.record_metric_contract(
            metric_name=self.metric_name,
            metric_direction=self.metric_direction,
        )


@dataclass(frozen=True)
class RunSuspendRequested(Event):
    """The live run must stop now and keep its active work for resume.

    This is not a state change and it is not a stop. Task cancellation leaves each active execution RUNNING so resume can continue it."""

    reason: str

    def apply(self, state: "RunState") -> None:
        # A run already asked to suspend is not asked twice: the first request
        # is the one being served, so saying it again would only repeat itself
        # in the run's account.
        if state.aibuildai_search.run_suspend_requested is not None:
            return
        state.aibuildai_search.request_suspend(reason=self.reason, ts=self.ts)


@dataclass(frozen=True)
class RunOpened(Aggregate.Created):
    """First event for one run. It is written once when the event store is new.

    It pins the conditions the run was born under, so a later reader can verify
    the run is being resumed
    under the SAME conditions instead of silently diverging.

    The ambient-condition pins (app_version/gpu_visibility/
    archive_location/schema_version) stay write-only (
    `bootstrap._verify_store_pins` reads them straight off the journal, never
    from the event store).

    app_version: APP_VERSION at run birth (git-derived product version).
    gpu_visibility: the run's visible GPU set at birth
        (resources.cuda_visible_devices rendered as "i,j,..."; None when the
        field is unset = every host card).
    archive_location: path to the archived run config (existing-Run startup's self-describing input).
    schema_version: the stored event shape version.
    search_kind: the search method this run uses. It selects the concrete Search,
        which resolves each restored Agent class.
    search_input: the root Search's own business Input, serialized. The runtime
        never reads inside it: the concrete Search names its Input type and this
        is validated against that type when the root is rebuilt.
    run_started_at_unix_s: the run's original Unix start time."""

    run_id: str
    run_home: str
    app_version: str
    gpu_visibility: "str | None"
    archive_location: str
    schema_version: int
    search_kind: str
    search_input: dict
    run_started_at_unix_s: float
    ts: float

    def mutate(self, aggregate: "RunState | None") -> "RunState":
        return super().mutate(aggregate)  # type: ignore[return-value]


@dataclass(frozen=True)
class RunEpochOpened(Event):
    """Per-process start record — written on EVERY process start (fresh run AND resume) right after the genesis/torn-tail handling. Marks "a process opened the run here" in the stream: the transcript renders every record after the first as a resume marker, and resume verifies the first record's ``cwd`` against the current process before committing a new one.

    cwd: the process working directory at start (a read-back-fidelity pin)."""

    cwd: str

    def apply(self, state: "RunState") -> None:
        # A suspend request is served by its process's exit, so the next
        # process opens the run live rather than still suspending. The same
        # holds for the last epoch's recoverable Failure pointer: the new
        # epoch is the attempt to move past it, and the failure itself stays
        # in the ordered events and on its execution's record.
        state.aibuildai_search.open_process()
        state.recoverable_failure = None


@dataclass(frozen=True)
class RunWarning(Event):
    """A run-level downgrade the user must be able to SEE. A legitimate-yet-notable state -- one a stage chose to proceed through rather than reject -- is RECORDED, never rejected. Before this event those record-only downgrades landed only as a ``logging.warning`` on stderr — invisible on the dashboard and in the run-end summary, and not replay-reconstructable. Riding the event log makes each downgrade a first-class fact applied to ``RunState.run_warnings``, so the overview warnings count and the run-summary listing rebuild identically live and on replay — every reconstruction-affecting record rides an event.

    source: the emitting stage boundary (e.g. "setup-output",
        "composite-score", "final-deliverable").
    category: a short stable tag for the downgrade kind.
    message: the one-line human explanation."""

    source: str
    category: str
    message: str

    def apply(self, state: "RunState") -> None:
        state.record_warning(
            source=self.source,
            category=self.category,
            message=self.message,
            ts=self.ts,
        )

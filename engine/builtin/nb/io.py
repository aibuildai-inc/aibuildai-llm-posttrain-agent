"""The nb package's own business records, and how a run's configuration becomes them.

Every record here is local to this package. Another package may spell a field
the same way with its own meaning, or declare none of them. ``build_input``
below is this package's one construction entry: it resolves the user's
settings into the immutable values every child is handed, once, at startup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, Literal

from pydantic import ConfigDict, Field

from engine.base import WorkflowBaseModel
from engine.builtin.aibuildai.agents.submitter.io import ExternalScore
from engine.builtin.aibuildai.capability import score_launch
from engine.builtin.aibuildai.programs.score import ScoreLaunch, ScoreOutput
from engine.builtin.nb.agents.designer.agent import WorkerDesignerAgent
from engine.builtin.nb.agents.designer.io import WorkerDesignPlan
from engine.builtin.nb.agents.worker.agent import WorkerAgent
from engine.builtin.nb.agents.worker.io import ParentAttempt, WorkerRevision
from engine.capability import ExecutionCapability
from engine.durable_execution import FileRef
from engine.metric_contract import MetricContractInput
from engine.paths import RunPaths

if TYPE_CHECKING:
    from config import AgentConfig
    from engine.work_unit.base import WorkUnit


# --- What this package's own children may spend --------------------------
# This package resolves its own roles, from the run's configuration, once at
# startup. It is deliberately a copy of what every other package does: no
# shared layer answers for an arbitrary role name, so one package's children
# can never be named, reached, or re-priced from another. The question is
# always asked of a Definition class this package owns, never of a string.
def _capability(
    configs: "AgentConfig", unit_type: "type[WorkUnit]"
) -> ExecutionCapability:
    """What one child of this package may spend."""
    kind, name = unit_type.kind, unit_type.name
    if name is None:
        raise AssertionError(f"{unit_type.__name__} declares no configured name")
    time_config = configs.work_unit_time_for(kind, name)
    limit = configs.resources.work_unit.limit_for(kind, name)
    return ExecutionCapability(
        wall_clock_seconds=time_config.cap_seconds(
            expected_minutes=None,
            pipeline_minutes=configs.run.budget.wall_clock_minutes,
        ),
        cpu_max_cores=limit.cpu_max_cores,
        memory_max_gb=float(limit.memory_max_gb),
        gpus=0,
        # File isolation is physical, so it is declared by the unit that does
        # the work, not by a container above it. None where the run did not ask
        # for it, so a unit never argues with an owner that did.
        retry=time_config.retry,
    )


class NBSearchParameters(WorkflowBaseModel):
    """NBsearch's own business parameters: the half a user writes.

    The single chain fixes exactly one design and exactly one revision per
    Worker."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    # The single chain runs one evaluation at a time, so this ceiling is never
    # reached. It keeps its own declared range because it is a user-facing
    # parameter: narrowing what a shipped config may say is a product decision,
    # and this refactor makes none.
    parallel: int = Field(
        gt=0,
        description="max concurrent Worker evaluations.",
        json_schema_extra={"placeholder": 1},
    )
    num_designs: Literal[1] = 1
    num_revisions: Literal[1] = 1
    max_evaluations: int | None = Field(
        default=None,
        ge=0,
        description="attempted-evaluation ceiling (failures occupy a slot); 0 "
        "runs no evaluations at all. null = no count cap beyond the run-level "
        "wall-clock + cost budget.",
    )
    early_stopping: int = Field(
        default=3,
        ge=0,
        description="stop the search once this many completed evaluations in a "
        "row have all failed the same way (the same failure.kind). 0 turns "
        "this off.",
    )


class WorkerGrant(WorkflowBaseModel):
    """What one nb Worker candidate needs to run and score its own Worker.

    Frozen at the spawn site out of the Search's own immutable Input, so a
    candidate never reads configuration, never asks a resolver for a
    capability, and never queries the run to rediscover a fact its owner
    already had."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_name: str
    progress_enabled: bool
    # The single chain requires exactly one revision per Worker, win or lose:
    # the floor and the ceiling are both the user's own ``num_revisions``.
    min_revisions: int
    num_revisions: int | None
    worker: ExecutionCapability
    score: ScoreLaunch


class NBSearchInput(NBSearchParameters):
    """NBsearch's complete immutable Input.

    The business parameters above are the user's. Everything below is resolved
    once by ``build_input``, so nothing this Search creates ever reads
    configuration or asks for a capability by role name."""

    designer: ExecutionCapability
    grant: WorkerGrant


def build_input(
    parameters: dict[str, Any], configs: "AgentConfig", run_paths: RunPaths
) -> NBSearchInput:
    """Complete this package's Input from the user's validated parameters."""
    revisions: int = parameters["num_revisions"]
    return NBSearchInput(
        **parameters,
        designer=_capability(configs, WorkerDesignerAgent),
        grant=WorkerGrant(
            task_name=configs.run.task_name,
            progress_enabled=configs.progress.enable,
            min_revisions=revisions,
            num_revisions=revisions,
            worker=_capability(configs, WorkerAgent),
            score=score_launch(configs, run_paths),
        ),
    )


class WorkerCandidateInput(WorkflowBaseModel):
    """One complete Worker attempt: the chain's first link, or its next one.

    The two differ by the business facts below, not by a second Composite
    class: a first link states no parent and no revision, and a later one
    states both."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: WorkerDesignPlan
    revision: WorkerRevision | None
    # The accepted facts of the link this one continues, which its own parent
    # stated in its Output and the Search passed here at the spawn site.
    parent: ParentAttempt | None
    # The parent link's own tree. ``parent`` says what that link scored and
    # which directories to read; this is what lets this Worker read them.
    parent_files: "FileRef | None"
    # Advisory external results recorded so far, newest last. Empty when
    # sharing is off, when no submission has been made yet, or when none has
    # returned -- three states the Worker's own prompt must not conflate.
    # The run's own metric and its direction, so the Worker that is told to
    # refine against its parent's formal score knows which way is better.
    metric_contract: MetricContractInput
    external_scores: tuple[ExternalScore, ...]
    grant: WorkerGrant

    @property
    def requested_gpus(self) -> int:
        return (
            self.plan.requested_gpus
            if self.revision is None
            else self.revision.requested_gpus
        )


class WorkerCandidateOutput(ScoreOutput):
    """The terminal success Output of one scored nb Worker candidate.

    It IS the Score Program's own Output plus this package's extra business
    facts, so the score, the attempt directory and the components have one
    owner and are stored once. Everything the next link needs is here, which
    is why that link is handed values rather than a directory to walk."""

    # This candidate's own directory: where its Worker kept the source, and
    # what the next link copies from. A Worker candidate always writes its own
    # source, so this is never None.
    source_dir: str
    # The plan this candidate ran. Its next link runs the same plan, and reads
    # it here instead of walking up to the design that first stated it.
    plan: WorkerDesignPlan
    revisions: tuple[WorkerRevision, ...]
    feedback: str

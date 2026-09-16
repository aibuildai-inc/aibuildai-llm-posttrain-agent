"""The nb_tree package's own business records, and how a run's configuration becomes them.

Every record here is local to this package. Another package may spell a field
the same way with its own meaning, or declare none of them. ``build_input``
below is this package's one construction entry: it resolves the user's
settings into the immutable values every child is handed, once, at startup."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, cast

from pydantic import ConfigDict, Field

from engine.base import WorkflowBaseModel
from engine.builtin.aibuildai.agents.submitter.io import ExternalScore
from engine.builtin.aibuildai.capability import score_launch
from engine.builtin.aibuildai.programs.score import ScoreLaunch, ScoreOutput
from engine.builtin.nb_tree.agents.designer.agent import WorkerDesignerAgent
from engine.builtin.nb_tree.agents.designer.io import WorkerDesignPlan
from engine.builtin.nb_tree.agents.worker.agent import WorkerAgent
from engine.builtin.nb_tree.agents.worker.io import ParentAttempt, WorkerRevision
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


class NBTreeSearchParameters(WorkflowBaseModel):
    """NBTreeSearch's own business parameters: the half a user writes.

    The single chain opened into a tree: several starting plans, several
    revision children per Worker, and a real concurrency ceiling."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parallel: int = Field(
        gt=0,
        description="max concurrent complete-Worker evaluations.",
        json_schema_extra={"placeholder": 4},
    )
    num_designs: int | None = Field(
        default=None,
        ge=1,
        description="the complete-Worker tree's starting evaluations. null = "
        "the designer decides the count from task complexity.",
    )
    num_revisions: int | None = Field(
        default=None,
        ge=1,
        description="the maximum revision children emitted by each Worker. "
        "null = the Worker decides the count; an int caps it. 0 proposals "
        "stays legal and deliberately ends that line.",
    )
    max_evaluations: int | None = Field(
        default=None,
        ge=0,
        description="attempted-evaluation ceiling (failures occupy a slot); 0 "
        "runs no evaluations at all. null = no count cap: the search stops "
        "when the run-level wall-clock + cost budget is exhausted.",
    )
    early_stopping: int = Field(
        default=3,
        ge=0,
        description="stop the search once this many completed evaluations in a "
        "row have all failed the same way (the same failure.kind). 0 turns "
        "this off.",
    )


class WorkerGrant(WorkflowBaseModel):
    """What one nb_tree Worker candidate needs to run and score its own Worker.

    Frozen at the spawn site out of the Search's own immutable Input, so a
    candidate never reads configuration, never asks a resolver for a
    capability, and never queries the run to rediscover a fact its owner
    already had."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_name: str
    progress_enabled: bool
    # The tree kind opens the single chain's fixed floor: a Worker is never
    # forced to keep its own line alive, and the user's own ``num_revisions``
    # is the ceiling.
    min_revisions: int
    num_revisions: int | None
    worker: ExecutionCapability
    score: ScoreLaunch


class NBTreeSearchInput(NBTreeSearchParameters):
    """NBTreeSearch's complete immutable Input.

    The business parameters above are the user's. Everything below is resolved
    once by ``build_input``, so nothing this Search creates ever reads
    configuration or asks for a capability by role name."""

    designer: ExecutionCapability
    grant: WorkerGrant


def build_input(
    parameters: dict[str, Any], configs: "AgentConfig", run_paths: RunPaths
) -> NBTreeSearchInput:
    """Complete this package's Input from the user's validated parameters."""
    return NBTreeSearchInput(
        **parameters,
        designer=_capability(configs, WorkerDesignerAgent),
        grant=WorkerGrant(
            task_name=configs.run.task_name,
            progress_enabled=configs.progress.enable,
            min_revisions=0,
            num_revisions=cast("int | None", parameters["num_revisions"]),
            worker=_capability(configs, WorkerAgent),
            score=score_launch(configs, run_paths),
        ),
    )


class WorkerCandidateInput(WorkflowBaseModel):
    """One complete Worker attempt: a fresh starting design, or a revision.

    The two differ by the business facts below, not by a second Composite
    class: a starting design states no parent and no revision, and a revision
    child states both."""

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
    """The terminal success Output of one scored nb_tree Worker candidate.

    It IS the Score Program's own Output plus this package's extra business
    facts, so the score, the attempt directory and the components have one
    owner and are stored once. Everything the next link needs is here, which
    is why that link is handed values rather than a directory to walk."""

    # This candidate's own directory: where its Worker kept the source, and
    # what a revision child copies from. A Worker candidate always writes its
    # own source, so this is never None.
    source_dir: str
    # The plan this candidate ran. Its revision children run the same plan,
    # and read it here instead of walking up to the design that stated it.
    plan: WorkerDesignPlan
    revisions: tuple[WorkerRevision, ...]
    feedback: str

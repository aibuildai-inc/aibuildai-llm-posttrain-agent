"""The linear package's own business records, and how a run's configuration becomes them.

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
from engine.builtin.linear.agents.aggregator.agent import AggregatorAgent
from engine.builtin.linear.agents.designer.agent import DesignerAgent
from engine.builtin.linear.agents.designer.io import DesignPlan
from engine.builtin.linear.agents.worker.agent import WorkerAgent
from engine.builtin.linear.agents.worker.io import ParentAttempt, WorkerRevision
from engine.capability import ExecutionCapability
from engine.durable_execution import FileRef
from engine.metric_contract import MetricContractInput
from engine.paths import RunPaths

if TYPE_CHECKING:
    from config import AgentConfig
    from engine.work_unit.agent.base import Agent
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


def _reviewer(
    configs: "AgentConfig", producer: "type[Agent]"
) -> ExecutionCapability | None:
    """What this role's own semantic review may spend, when this run composes one.

    None when the role composes no reviewer for this configuration. That None
    is the whole answer: it travels to the role as its frozen
    ``reviewer_capability``, so nothing asks configuration again at run time."""
    reviewer = producer.semantic_reviewer()
    if reviewer is None or not producer.composes_reviewer(configs):
        return None
    return _capability(configs, reviewer)


class LinearSearchParameters(WorkflowBaseModel):
    """LinearSearch's own business parameters: the half a user writes.

    The serial chain fixes its shape: one design, one revision per link, one
    chain -- those three accept only their fixed value (or omission)."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    num_designs: Literal[1] = 1
    num_revisions: Literal[1] = 1
    parallel: Literal[1] = 1
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


class CandidateGrant(WorkflowBaseModel):
    """What one linear candidate needs to run and score its own Worker.

    Frozen at the spawn site out of the Search's own immutable Input, so a
    candidate never reads configuration, never asks a resolver for a
    capability, and never queries the run to rediscover a fact its owner
    already had."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_name: str
    progress_enabled: bool
    # The chain ends when a Worker proposes nothing, so the floor is zero and
    # the ceiling is the user's own ``num_revisions``.
    min_revisions: int
    num_revisions: int | None
    worker: ExecutionCapability
    # None when this run composes no Worker reviewer; resolved once by startup
    # either way, so the Worker is constructed with its answer instead of
    # asking a resolver at the moment it runs.
    worker_reviewer: ExecutionCapability | None
    score: ScoreLaunch


class LinearSearchInput(LinearSearchParameters):
    """LinearSearch's complete immutable Input.

    The business parameters above are the user's. Everything below is resolved
    once by ``build_input``, so nothing this Search creates ever reads
    configuration or asks for a capability by role name."""

    designer: ExecutionCapability
    # None when this run does not compose the named role's semantic reviewer;
    # resolved once here either way, so the role's own Agent is constructed
    # with ``reviewer_capability=`` beside its own ``capability=`` and never
    # asks a resolver at the moment it runs. The Worker's own reviewer travels
    # on ``grant`` instead, since the Worker is constructed inside a Composite.
    designer_reviewer: ExecutionCapability | None
    aggregator: ExecutionCapability
    grant: CandidateGrant


def build_input(
    parameters: dict[str, Any], configs: "AgentConfig", run_paths: RunPaths
) -> LinearSearchInput:
    """Complete this package's Input from the user's validated parameters."""
    return LinearSearchInput(
        **parameters,
        designer=_capability(configs, DesignerAgent),
        designer_reviewer=_reviewer(configs, DesignerAgent),
        aggregator=_capability(configs, AggregatorAgent),
        grant=CandidateGrant(
            task_name=configs.run.task_name,
            progress_enabled=configs.progress.enable,
            # A Worker may end the chain by proposing nothing, so the floor is
            # zero; the user's own count is the ceiling.
            min_revisions=0,
            num_revisions=1,
            worker=_capability(configs, WorkerAgent),
            worker_reviewer=_reviewer(configs, WorkerAgent),
            score=score_launch(configs, run_paths),
        ),
    )


class WorkerCandidateInput(WorkflowBaseModel):
    """One complete Worker attempt: the chain's first link, or its next one.

    The two differ by the business facts below, not by a second Composite
    class: a first link states no parent and no revision, and a later one
    states both."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: DesignPlan
    revision: WorkerRevision | None
    # The accepted facts of the link this one continues, which its own parent
    # stated in its Output and the Search passed here at the spawn site.
    parent: ParentAttempt | None
    # The parent link's own tree. ``parent`` says what that link scored and
    # which directories to read; this is what lets this Worker read them.
    parent_files: "FileRef | None"
    # Advisory external results the Search had when it admitted this Worker; a
    # live read taken at the spawn site, never re-derived inside the Composite.
    # The run's own metric and its direction, so the Worker that is told to
    # refine against its parent's formal score knows which way is better.
    metric_contract: MetricContractInput
    external_scores: tuple[ExternalScore, ...]
    grant: CandidateGrant

    @property
    def requested_gpus(self) -> int:
        return (
            self.plan.requested_gpus
            if self.revision is None
            else self.revision.requested_gpus
        )


class EnsembleMember(WorkflowBaseModel):
    """One scored candidate an ensemble was told to combine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uid: str
    # This member's own tree, so an ensemble over N members is granted the N
    # members the Search chose and nothing else.
    files: FileRef
    # The exact directory this member's own score was measured on, so the
    # Aggregator opens the scored output instead of guessing which directory
    # under a Composite holds it.
    output_dir: str
    score: float


class EnsembleInput(WorkflowBaseModel):
    """One bounded combination of already scored candidates.

    Its members are also its direct ``upstream``; their facts are here because
    the Search that selected them had them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    members: tuple[EnsembleMember, ...] = Field(min_length=1)
    metric_contract: MetricContractInput
    memory_cap: str
    aggregator: ExecutionCapability
    score: ScoreLaunch

    @classmethod
    def over(
        cls,
        members: "tuple[tuple[str, FileRef, ScoreOutput], ...]",
        *,
        metric_contract: MetricContractInput,
        aggregator: ExecutionCapability,
        score: ScoreLaunch,
    ) -> "EnsembleInput":
        """The combination of the members the Search chose, as its own record.

        Each member arrives as its uid beside the Output it settled with, so
        every fact here is one the Search already held; the Aggregator is
        never asked to find a member's scored directory for itself."""
        chosen = tuple(
            EnsembleMember(
                uid=uid, files=files, output_dir=output.output_dir, score=output.score
            )
            for uid, files, output in members
        )
        return cls(
            members=chosen,
            metric_contract=metric_contract,
            memory_cap=f"{int(aggregator.memory_max_gb or 0)}G",
            aggregator=aggregator,
            score=score,
        )


class WorkerCandidateOutput(ScoreOutput):
    """The terminal success Output of one scored linear candidate.

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
    plan: DesignPlan
    revisions: tuple[WorkerRevision, ...]
    feedback: str

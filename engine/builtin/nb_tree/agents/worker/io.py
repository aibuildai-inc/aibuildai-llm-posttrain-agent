"""Input and Output records for the nb_tree package's Worker."""

from __future__ import annotations

from dataclasses import dataclass

from pydantic import ConfigDict, Field, ValidationInfo, model_validator

from engine.base import WorkflowBaseModel
from engine.builtin.aibuildai.agents.submitter.io import ExternalScore
from engine.builtin.nb_tree.agents.designer.io import Citation, WorkerDesignPlan
from engine.execution_output import SuccessfulOutput
from engine.metric_contract import MetricContractInput
from engine.work_unit.agent.base import AgentInput


class RecipeChange(WorkflowBaseModel):
    """One narrative edit to a component of the design the next link continues."""

    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        json_schema_extra={
            "description": "A narrative edit to one component of this chain's "
            "design plan."
        },
    )
    target: str = Field(
        description="Which area is being changed. Use one of: 'architecture', "
        "'data_pipeline', 'loss_function', 'regularization', 'training_config', "
        "'validation_strategy'."
    )
    from_state: str = Field(
        description="One sentence describing the current state for this target "
        "(your understanding of what your own attempt does now)."
    )
    to_state: str = Field(
        description="One sentence describing the new state — concrete and "
        "actionable, the next Worker uses this as-is."
    )
    rationale: str = Field(
        description="Why this change addresses the diagnosis. Must tie to evidence "
        "in `diagnosis`."
    )


class WorkerRevision(WorkflowBaseModel):
    """One direction the current Worker proposes for the next link of this chain.

    It is guidance, not a script: the next Worker reads its parent's own formal
    score before it starts, and refines this direction from it. There is no
    exact find/replace field here, because there is no second role to carry a
    patch across -- the Worker that applies the change owns the inherited
    source itself and edits it directly."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(
        description="Short descriptive name (e.g. 'R1_BiasInit_FixedLR')."
    )
    diagnosis: str = Field(
        description="2-4 sentences. What went wrong in your own attempt and the "
        "EVIDENCE for it: loss values you logged, errors you read, the shape of "
        "the files you wrote. 'the metric looks low' alone is not a diagnosis — "
        "name the physical phenomenon behind it."
    )
    revision_intent: str = Field(
        description="1-2 sentences. What the next attempt changes and what "
        "success looks like, in one breath."
    )
    recipe_changes: tuple[RecipeChange, ...] = Field(
        default_factory=tuple,
        description="Narrative edits to components of this chain's design plan. "
        "Empty when the next attempt only continues the same recipe with more "
        "budget.",
    )
    expected_outcome: str = Field(
        description="1-2 sentences. What signal you expect after the change."
    )
    requested_gpus: int = Field(
        default=0,
        ge=0,
        description="Number of GPUs the next attempt requires. Zero runs on CPU. "
        "Restate it every revision (it is NOT inherited) — re-pick by the revised "
        "model's scale within the 0..M host range the prompt gives you.",
    )
    references: tuple[Citation, ...] = Field(
        default_factory=tuple,
        description="External published works adopted while diagnosing this "
        "attempt, each captured verbatim from an available sub-agent search "
        "result. NEVER fabricate a reference. Empty when no external literature "
        "grounded this direction.",
    )


class ParentAttempt(WorkflowBaseModel):
    """The accepted facts of the chain link this Worker continues.

    Every field here is something the Search already held when it started this
    Worker: the parent's own formal score and its parts, what that Worker said
    about its own attempt, and the exact directories to read. Nothing here is
    recovered by walking the run workspace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str
    # The parent's own source tree, which this Worker inherits and edits in
    # its own directory, and the attempt directory the score below was
    # measured on. Both are addresses to read, never places to write.
    source_dir: str
    output_dir: str
    # The formal score the run's own score program measured, which the parent
    # Worker never saw during its own session.
    score: float
    components: tuple[tuple[str, float], ...]
    # What the parent Worker itself reported about the attempt it ran.
    feedback: str


@dataclass(frozen=True)
class WorkerInput(AgentInput):
    """One complete attempt: its design, its inherited parent, and its limits."""

    plan: WorkerDesignPlan
    # None for the chain's first link; otherwise the direction the parent
    # Worker proposed for this one.
    revision: WorkerRevision | None
    parent: ParentAttempt | None
    # The run's own metric and its direction. A parent's formal score is
    # meaningless without it: the Worker is told to refine against that
    # number, so it must know which way is better.
    metric_contract: MetricContractInput
    task_name: str
    min_revisions: int
    num_revisions: int | None
    gpu_count: int
    progress_enabled: bool
    # Advisory external results recorded so far, newest last. Empty when
    # sharing is off, when no submission has been made yet, or when none has
    # returned -- three states the prompt must not conflate.
    external_scores: tuple[ExternalScore, ...]


class WorkerOutput(SuccessfulOutput):
    revisions: tuple[WorkerRevision, ...] = Field(
        description="What the next link of this chain should try, or an empty "
        "list to end the chain deliberately."
    )
    feedback: str = Field(
        default="",
        description=(
            "What this attempt actually did and what it left behind: the "
            "measurements you took, the instability you saw, the caveats about "
            "the output you wrote. The next Worker reads this beside your "
            "parent's formal score."
        ),
    )

    @model_validator(mode="after")
    def _revision_count_fits_the_request(self, info: ValidationInfo) -> "WorkerOutput":
        """Refuse a submission that did not do the revisions it was asked for.

        The count is a property of this answer against the request that asked
        for it, so nothing outside the two is needed to decide it.

        A persistence restore re-reads an Output the run already accepted and
        carries no input, so it stops at the context check."""
        if info.context is None:
            return self
        worker_input = info.context.get("input")
        if not isinstance(worker_input, WorkerInput):
            raise ValueError("the Worker request is missing from validation context")
        count = len(self.revisions)
        minimum = worker_input.min_revisions
        maximum = worker_input.num_revisions
        if count >= minimum and (maximum is None or count <= maximum):
            return self
        if maximum == minimum:
            requirement = f"exactly {minimum} revisions"
        elif maximum is None:
            requirement = f"at least {minimum} revisions"
        else:
            requirement = f"between {minimum} and {maximum} revisions"
        raise ValueError(f"This Worker requires {requirement}; you submitted {count}.")

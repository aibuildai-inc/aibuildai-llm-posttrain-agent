"""Input and Output records for the coder Agent family."""

from __future__ import annotations

from dataclasses import dataclass
from pydantic import Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent.base import AgentInput
from engine.builtin.tree.agents.designer.io import DesignPlan
from engine.builtin.tree.agents.reviser.io import RevisionProposal



@dataclass(frozen=True)
class CoderInput(AgentInput):
    proposal: DesignPlan | RevisionProposal
    # The accepted source directory this Coder inherits and revises, or ""
    # for a fresh design with nothing to inherit. The framework copies it
    # into this Coder run's own source directory before the session starts.
    inherited_source_dir: str
    # The two judgements that put this Coder here: the Judge's own verdict on
    # THIS proposal, and the Selector's own reason for running it. They are
    # delivered because the Search held them, not left in a transcript for
    # this role to go and find.
    judge_dimensions: tuple[tuple[str, int], ...]
    judge_rationale: str
    selector_feedback: str
    # The completed candidate this run revises, when it revises one: what it
    # scored, what its own Coder reported before Training, and the attempt
    # directory that score was measured on. None throughout for a fresh design.
    parent_uid: str | None
    parent_metric: float | None
    parent_metric_components: tuple[tuple[str, float], ...]
    parent_feedback: str
    parent_output_dir: str | None
    task_name: str
    train_file_name: str
    cpu_cores: float
    gpu_count: int
    coder_budget_minutes: float
    training_budget_minutes: float | None
    progress_enabled: bool

    @property
    def proposal_is_revision(self) -> bool:
        """Return whether this run applies a revision to inherited code."""
        return isinstance(self.proposal, RevisionProposal)


class CoderOutput(SuccessfulOutput):
    # No self-reported score: the run's own score program is the one authority
    # on what an attempt is worth, and a second number here had no reader.
    expected_full_training_minutes: int = Field(
        description=(
            "Full training duration in minutes, computed as "
            "max_steps * steady-state step-time (seconds/step) / 60. "
            "Use iteration-based sizing, never epochs. Drop the first 4 smoke steps "
            "when computing step-time to avoid model-load / first-batch / "
            "DataLoader-worker startup overhead. When the Training WorkUnit uses "
            "the expected-duration calculation, it derives its cap from "
            "this value and the user-configured slack and bounds."
        ),
    )
    reasoning: str = Field(
        default="", description="Explanation of what was done and why."
    )
    feedback: str = Field(
        default="",
        description=(
            "Smoke-run findings for a later Reviser. Report measured smoke metrics, "
            "step time, instability, memory limits, failed smoke checks, and output "
            "shape caveats when applicable. Report only facts observed before Training."
        ),
    )



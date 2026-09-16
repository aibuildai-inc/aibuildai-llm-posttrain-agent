"""Input of the error-driven precision continuation Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class PrecisionSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement the worker and the fixer both serve.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    target_score: float = Field(
        default=0.99, gt=0, le=1, description="A fix round stops the loop once the evaluated score reaches this."
    )
    max_fix_rounds: int = Field(default=6, ge=1, description="Upper bound on continuation rounds regardless of budget.")
    replay_fraction: float = Field(
        default=0.2, gt=0, lt=1, description="Share of each fix dataset drawn from already-correct examples, to guard against regression."
    )
    continuation_learning_rate: float = Field(
        default=2e-6, gt=0, description="Low learning rate every continuation round trains at, one epoch, from the best checkpoint."
    )
    worker_wall_clock_seconds: int = Field(
        default=14400,
        ge=60,
        description="One wall-clock grant for the worker identity, spent across the initial SFT and every fix round.",
    )
    fixer_wall_clock_seconds: int = Field(default=600, ge=60)
    fix_round_seconds: int = Field(
        default=2400,
        ge=60,
        description="What one fix round (fixer + continuation + eval) is expected to cost in wall clock; a round starts only while the exploration remainder pays for it.",
    )

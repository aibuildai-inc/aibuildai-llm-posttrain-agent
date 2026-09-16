"""Input of the single-worker rounds Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SingleWorkerRoundsSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement the worker serves.")
    data_dir: str = Field(description="Read-only directory holding the task data and its scorer.")
    max_rounds: int = Field(default=4, ge=1, le=12, description="Hard ceiling on rounds.")
    min_round_seconds: int = Field(
        default=2400,
        ge=60,
        description="A round starts only while the exploration remainder pays for one of this size.",
    )
    worker_wall_clock_seconds: int = Field(
        default=28800,
        ge=60,
        description=(
            "One wall-clock grant for the worker identity, spent across EVERY round it "
            "runs: each round is one more Action of the same identity. Size it for all "
            "the rounds together; the exploration budget bounds it anyway."
        ),
    )

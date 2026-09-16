"""Input of the teacher-distillation Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class TeacherDistillSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every round serves.")
    data_dir: str = Field(description="Read-only directory holding the eval script and any seed material.")
    seed_prompts: tuple[str, ...] = Field(min_length=1, description="The first round's broad seed prompts.")
    max_rounds: int = Field(default=3, ge=1, le=6)
    rows_per_round: int = Field(
        default=200, ge=1, description="Target number of new rows one round's generation adds."
    )
    min_round_seconds: int = Field(
        default=300, ge=30, description="A round starts only while the exploration remainder pays for it."
    )
    worker_seconds: int = Field(
        default=18000,
        ge=60,
        description="One wall-clock grant for the worker identity, spent across every round it runs.",
    )
    diagnosis_seconds: int = Field(default=900, ge=60)

"""Input of the evaluator-optimizer Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvaluatorOptimizerSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement both roles serve.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    rubric: str = Field(description="The fixed acceptance rubric the evaluator applies every round.")
    max_rounds: int = Field(default=3, ge=1, le=8, description="Hard budget of produce-evaluate rounds.")
    producer_wall_clock_seconds: int = Field(default=1800, ge=60)
    evaluator_wall_clock_seconds: int = Field(default=600, ge=60)

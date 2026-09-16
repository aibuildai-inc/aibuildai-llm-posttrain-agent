"""Input of the batch-training Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BatchTrainingSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement the designer serves.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    max_configurations: int = Field(default=3, ge=1, le=8)
    designer_wall_clock_seconds: int = Field(default=1200, ge=60)
    training_wall_clock_seconds: int = Field(default=3600, ge=60)
    min_successful: int = Field(
        default=1, ge=1, description="Fewer terminal successes than this is a Failure."
    )

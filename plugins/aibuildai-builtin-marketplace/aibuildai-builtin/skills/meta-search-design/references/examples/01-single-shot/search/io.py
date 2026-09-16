"""Input of the single-shot Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SingleShotSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement the role serves.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    wall_clock_seconds: int = Field(
        default=1800, ge=60, description="Local budget granted to the one role."
    )

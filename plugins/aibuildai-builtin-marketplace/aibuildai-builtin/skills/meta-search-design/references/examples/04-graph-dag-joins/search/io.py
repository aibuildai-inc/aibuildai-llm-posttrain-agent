"""Input of the dependency-graph Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GraphDagJoinsSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every role serves.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    profile_wall_clock_seconds: int = Field(default=300, ge=60)
    baseline_wall_clock_seconds: int = Field(default=900, ge=60)
    selection_wall_clock_seconds: int = Field(default=300, ge=60)
    quality_wall_clock_seconds: int = Field(default=180, ge=60)
    tuning_wall_clock_seconds: int = Field(default=1800, ge=60)
    decision_wall_clock_seconds: int = Field(default=180, ge=60)

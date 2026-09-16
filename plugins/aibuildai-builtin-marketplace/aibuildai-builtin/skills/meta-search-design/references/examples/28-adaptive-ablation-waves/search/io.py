"""Input of the adaptive-ablation Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class AdaptiveAblationSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim study question every role serves.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    max_waves: int = Field(default=3, ge=1, le=6)
    max_cells_per_wave: int = Field(default=4, ge=1, le=8)
    orchestrator_wall_clock_seconds: int = Field(default=1200, ge=60)
    cell_wall_clock_seconds: int = Field(default=2400, ge=60)
    analyst_wall_clock_seconds: int = Field(default=900, ge=60)

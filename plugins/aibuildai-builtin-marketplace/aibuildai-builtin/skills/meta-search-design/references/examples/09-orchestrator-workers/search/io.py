"""Input of the orchestrator-workers Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class OrchestratorWorkersSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim regression question the whole study serves.")
    repo_dir: str = Field(description="Read-only directory holding the repository under investigation.")
    max_waves: int = Field(default=3, ge=1, le=6)
    max_assignments_per_wave: int = Field(default=3, ge=1, le=8)
    max_total_assignments: int = Field(default=6, ge=1, le=24)
    orchestrator_wall_clock_seconds: int = Field(default=900, ge=60)
    worker_wall_clock_seconds: int = Field(default=1200, ge=60)
    synthesizer_wall_clock_seconds: int = Field(default=600, ge=60)

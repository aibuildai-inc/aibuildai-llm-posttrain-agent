"""Input of the beam-search Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BeamSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every role serves.")
    data_dir: str = Field(description="Read-only directory holding the raw task data.")
    max_depth: int = Field(default=4, ge=1, le=8, description="Hard bound on transform-step depth.")
    beam_width: int = Field(default=3, ge=1, le=8, description="Surviving states kept after each depth.")
    branching_factor: int = Field(default=3, ge=1, le=6, description="Candidate children generated per parent.")
    acceptance_threshold: float = Field(description="Score at or above which a complete state stops the Search.")
    expander_wall_clock_seconds: int = Field(default=1200, ge=60)
    evaluator_wall_clock_seconds: int = Field(default=600, ge=60)

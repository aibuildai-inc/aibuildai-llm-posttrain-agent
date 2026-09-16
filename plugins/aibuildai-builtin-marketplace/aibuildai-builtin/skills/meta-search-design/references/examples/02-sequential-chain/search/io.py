"""Input of the sequential-chain Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SequentialChainSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every stage serves.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    planner_wall_clock_seconds: int = Field(default=600, ge=60)
    implementer_wall_clock_seconds: int = Field(default=2400, ge=60)

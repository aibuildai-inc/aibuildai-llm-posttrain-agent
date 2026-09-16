"""Input of the handoff swarm Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class HandoffSwarmSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every specialist serves.")
    data_dir: str = Field(description="Read-only directory holding the task's data and logs.")
    max_turns: int = Field(default=8, ge=1, le=16, description="Hard turn budget across all specialists.")
    max_visits_per_role: int = Field(default=3, ge=1, le=8, description="Turn bound for one specialist.")
    specialist_wall_clock_seconds: int = Field(default=900, ge=60)
    finalizer_wall_clock_seconds: int = Field(default=600, ge=60)

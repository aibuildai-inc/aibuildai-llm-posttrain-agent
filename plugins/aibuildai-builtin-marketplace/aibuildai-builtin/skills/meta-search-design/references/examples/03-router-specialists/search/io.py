"""Input of the router and specialists Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RouterSpecialistsSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement the router classifies.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    router_wall_clock_seconds: int = Field(default=300, ge=60)
    specialist_wall_clock_seconds: int = Field(default=1800, ge=60)
    synthesis_wall_clock_seconds: int = Field(default=600, ge=60)
    min_successful_routes: int = Field(
        default=1, ge=1, description="Fewer successful selected routes than this is a Failure."
    )

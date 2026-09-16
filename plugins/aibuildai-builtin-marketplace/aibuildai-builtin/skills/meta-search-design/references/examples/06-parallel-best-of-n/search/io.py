"""Input of the best-of-N Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BestOfNSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every candidate serves.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    approaches: tuple[str, ...] = Field(
        min_length=2,
        description="One modeling approach per candidate; the length is N.",
    )
    candidate_wall_clock_seconds: int = Field(default=1800, ge=60)
    min_successful: int = Field(
        default=1, ge=1, description="Fewer successful candidates than this is a Failure."
    )

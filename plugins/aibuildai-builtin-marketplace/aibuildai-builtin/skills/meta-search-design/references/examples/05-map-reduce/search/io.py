"""Input of the map-reduce code review Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CodeReviewMapReduceInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim review goal every mapper serves.")
    repo_dir: str = Field(description="Read-only directory holding the full repository.")
    packages: tuple[str, ...] = Field(
        min_length=2,
        max_length=12,
        description="Disjoint package paths relative to repo_dir; every one is required.",
    )
    reviewer_wall_clock_seconds: int = Field(default=1200, ge=60)
    reducer_wall_clock_seconds: int = Field(default=900, ge=60)

"""Input of the recursive divide-and-conquer Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class RecursiveDivideSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every level of recursion serves.")
    document_path: str = Field(description="Read-only path of the full document; children read the same file.")
    section_start: int = Field(ge=0, description="First section index of this level's problem, inclusive.")
    section_end: int = Field(gt=0, description="Last section index of this level's problem, exclusive.")
    depth_remaining: int = Field(ge=0, description="Recursion levels left; a child never increases this.")
    max_children: int = Field(ge=2, le=8, description="Bound on subsections one decomposition may produce.")
    base_case_max_sections: int = Field(
        ge=1, description="A problem measure at or below this size is solved directly, no decomposition."
    )
    solver_wall_clock_seconds: int = Field(default=900, ge=60)
    decomposer_wall_clock_seconds: int = Field(default=600, ge=60)
    combiner_wall_clock_seconds: int = Field(default=900, ge=60)
    child_search_wall_clock_seconds: int = Field(default=3600, ge=60)

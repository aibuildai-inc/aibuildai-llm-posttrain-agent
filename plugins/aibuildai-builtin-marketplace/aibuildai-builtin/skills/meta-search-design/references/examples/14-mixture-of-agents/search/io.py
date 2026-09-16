"""Input of the mixture-of-agents Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MemberSpec(BaseModel):
    """One layer-1 proposer: a stable id, a stance, and its diversity axis."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    member_id: str = Field(description="Stable identifier for this member, unique among members.")
    role: str = Field(description="The expertise or stance this member brings, shown to the model.")
    emphasis: str = Field(description="What this member should prioritize; the diversity axis between members.")


class MixtureSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every member and the aggregator serve.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    members: tuple[MemberSpec, ...] = Field(
        min_length=2, description="One heterogeneous proposer per candidate; the length is N."
    )
    rubric: str = Field(description="What the aggregator judges the candidate slate against.")
    min_successful_members: int = Field(
        default=1, ge=1, description="Fewer successful candidates than this is a Failure."
    )
    member_wall_clock_seconds: int = Field(default=1800, ge=60)
    aggregator_wall_clock_seconds: int = Field(default=600, ge=60)

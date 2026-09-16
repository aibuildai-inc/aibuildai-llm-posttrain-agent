"""Input of the committee-voting Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CommitteeOption(BaseModel):
    """One option already produced by an earlier stage, ready to be judged."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    option_id: str = Field(description="Stable identifier, unique among the options.")
    output_dir: str = Field(description="Absolute artifacts directory holding this option.")
    summary: str = Field(description="What this option is, in one paragraph.")


class CommitteeMemberSpec(BaseModel):
    """One declared voter and its fixed weight, set before any ballot is cast."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    member_id: str = Field(description="Stable identifier, unique among the members.")
    role: str = Field(description="The judging emphasis this member votes under.")
    weight: float = Field(default=1.0, gt=0.0, description="Fixed vote weight, declared before ballots are cast.")
    required: bool = Field(default=False, description="True when this member's ballot must be counted.")


class CommitteeSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The shared decision every member votes on.")
    rubric: str = Field(description="The fixed judging rubric every member applies.")
    options: tuple[CommitteeOption, ...] = Field(min_length=2, description="The closed ballot slate.")
    members: tuple[CommitteeMemberSpec, ...] = Field(min_length=1, description="The declared committee.")
    quorum: int = Field(ge=1, description="Minimum valid counted ballots the tally requires.")
    member_wall_clock_seconds: int = Field(default=600, ge=60)

"""Input of the tournament Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class Contestant(BaseModel):
    """One candidate already produced by an earlier stage, ready to compete."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(description="Stable identifier, unique among the contestants.")
    output_dir: str = Field(description="Absolute artifacts directory holding this candidate.")
    summary: str = Field(description="What this candidate is, in one paragraph.")
    seed_score: float = Field(description="Prior score used only to report the eventual champion's score.")


class TournamentSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every contestant serves.")
    rubric: str = Field(description="The fixed comparison rubric every match applies.")
    contestants: tuple[Contestant, ...] = Field(min_length=2, description="The bracket's starting field.")
    match_wall_clock_seconds: int = Field(default=600, ge=60)

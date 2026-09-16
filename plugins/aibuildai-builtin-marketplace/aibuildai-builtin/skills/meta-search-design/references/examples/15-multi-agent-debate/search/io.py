"""Input of the multi-agent-debate Search, and the domain types the agent Inputs share."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ParticipantSpec(BaseModel):
    """One declared debater and the stance it argues from."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    participant_id: str = Field(description="Stable identifier, unique among the participants.")
    stance: str = Field(description="The alternative hypothesis or strategy this participant argues for.")


class DebateSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim question every participant and the judge answer.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    rubric: str = Field(description="The fixed decision rubric the judge applies.")
    participants: tuple[ParticipantSpec, ...] = Field(min_length=2, description="The declared debaters.")
    minimum_positions: int = Field(ge=2, description="Fewest valid opening positions the debate accepts.")
    debater_wall_clock_seconds: int = Field(default=900, ge=60)
    judge_wall_clock_seconds: int = Field(default=600, ge=60)

"""Input of the selector group chat Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SelectorGroupChatSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every participant serves.")
    data_dir: str = Field(description="Read-only directory holding the failing run's logs and config.")
    max_turns: int = Field(default=6, ge=1, le=12, description="Hard turn budget across all participants.")
    max_turns_per_participant: int = Field(default=2, ge=1, le=6, description="Turn bound for one participant.")
    selector_wall_clock_seconds: int = Field(default=300, ge=60)
    participant_wall_clock_seconds: int = Field(default=900, ge=60)
    finalizer_wall_clock_seconds: int = Field(default=600, ge=60)

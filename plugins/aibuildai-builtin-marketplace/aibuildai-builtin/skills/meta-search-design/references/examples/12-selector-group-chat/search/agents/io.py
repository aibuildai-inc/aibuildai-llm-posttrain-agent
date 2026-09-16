"""Typed Input and Output of the five roles, plus the public conversation state."""

from __future__ import annotations

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput

ParticipantId = Literal["researcher", "engineer", "critic"]
ContributionKind = Literal["evidence", "proposal", "critique"]


class PublicMessage(BaseModel):
    """One typed public turn; the only state later participants read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_index: int
    participant_id: ParticipantId
    kind: ContributionKind
    content: str = Field(description="The bounded public contribution made this turn.")


@dataclasses.dataclass(frozen=True)
class SelectorInput(AgentInput):
    objective: str
    max_turns_per_participant: int


class SelectorOutput(SuccessfulOutput):
    """Exactly one of: choose a speaker, finalize, or fail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    directive: Literal["speak", "finalize", "fail"]
    participant_id: str = Field(description="Chosen speaker; empty unless directive is 'speak'.")
    instruction: str = Field(description="What the chosen speaker should do this turn; empty unless 'speak'.")
    reason: str = Field(description="Why the chat finalizes or fails; empty when directive is 'speak'.")


@dataclasses.dataclass(frozen=True)
class ParticipantInput(AgentInput):
    objective: str
    data_dir: str


class ParticipantOutput(SuccessfulOutput):
    """One bounded public contribution."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    kind: ContributionKind
    content: str = Field(description="The bounded public contribution made this turn.")


@dataclasses.dataclass(frozen=True)
class FinalizerInput(AgentInput):
    objective: str
    brief: str
    public_messages: tuple[PublicMessage, ...]


class FinalizerOutput(SuccessfulOutput):
    """The written fix plan and a confidence score for it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the written fix plan.")
    score: float = Field(description="Confidence the plan resolves the objective, in [0, 1].")
    summary: str = Field(description="The finalized fix plan in prose.")

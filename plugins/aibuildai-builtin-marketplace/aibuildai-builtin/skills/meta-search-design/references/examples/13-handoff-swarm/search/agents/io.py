"""Typed Input and Output of the four specialists and the finalizer."""

from __future__ import annotations

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput

SpecialistId = Literal["triage", "research", "implementation", "review"]


class HandoffMessage(BaseModel):
    """One typed public turn; the only conversation state later roles read."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    turn_index: int
    role: SpecialistId
    content: str = Field(description="The bounded public contribution made this turn.")


class ArtifactRef(BaseModel):
    """A pointer to one file the swarm produced, not the file itself."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    path: str = Field(description="Absolute path of one artifact a specialist produced.")
    description: str = Field(description="One line naming what the artifact holds.")


class ProposedResult(BaseModel):
    """The candidate final result one specialist proposes."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    summary: str = Field(description="The candidate final result in prose.")
    output_dir: str = Field(description="Absolute directory holding the candidate's files.")


class HandoffDecision(BaseModel):
    """What the active specialist wants to happen next; exactly one action."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action: Literal["handoff", "finish", "fail"]
    next_role: SpecialistId | None = Field(description="Target role; set only when action is 'handoff'.")
    reason: str = Field(description="Why this decision was made.")


@dataclasses.dataclass(frozen=True)
class SpecialistInput(AgentInput):
    objective: str
    data_dir: str
    allowed_handoffs: tuple[SpecialistId, ...]


class SpecialistOutput(SuccessfulOutput):
    """One bounded public contribution plus the control decision that follows it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    content: str = Field(description="The bounded public contribution this role makes this turn.")
    artifacts: tuple[ArtifactRef, ...]
    decision: HandoffDecision
    proposed_result: ProposedResult | None = Field(description="Set only when the decision finishes.")


@dataclasses.dataclass(frozen=True)
class FinalizerInput(AgentInput):
    objective: str
    public_messages: tuple[HandoffMessage, ...]
    artifacts: tuple[ArtifactRef, ...]
    candidate: ProposedResult


class FinalizerOutput(SuccessfulOutput):
    """The accepted result written to durable storage, and its confidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the accepted result.")
    score: float = Field(description="Confidence the result resolves the objective, in [0, 1].")
    summary: str = Field(description="The finalized result in prose.")

"""Typed data for the debate roles."""

from __future__ import annotations

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput

from ..io import ParticipantSpec


class Position(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    participant_id: str
    claim: str
    evidence: tuple[str, ...]
    proposal_dir: str


class Critique(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")

    critic_id: str
    target_id: str
    issue: str
    severity: Literal["minor", "major", "fatal"]


class DebateTurnOutput(SuccessfulOutput):
    """One opening, critique, or rebuttal turn."""

    position: Position | None = None
    critiques: tuple[Critique, ...] = ()


@dataclasses.dataclass(frozen=True)
class DebaterInput(AgentInput):
    objective: str
    data_dir: str
    participant: ParticipantSpec


class JudgeOutput(SuccessfulOutput):
    winner_id: str | None
    ranking: tuple[str, ...]
    unresolved_issues: tuple[str, ...]


@dataclasses.dataclass(frozen=True)
class JudgeInput(AgentInput):
    objective: str
    rubric: str
    positions: tuple[Position, ...]
    critiques: tuple[Critique, ...]

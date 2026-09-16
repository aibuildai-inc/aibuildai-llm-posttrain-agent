"""Typed Input and Output of the member and the aggregator."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class MemberInput(AgentInput):
    objective: str
    data_dir: str
    role: str
    emphasis: str


class MemberOutput(SuccessfulOutput):
    """One member's candidate: its artifact, its own score, and a bounded summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding this candidate.")
    score: float = Field(description="Validation score under the task metric, higher is better.")
    summary: str = Field(description="What this candidate does and why, in one paragraph.")


class CandidateSummary(BaseModel):
    """The bounded slate entry the aggregator reads; never the member's raw transcript."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    member_id: str
    role: str
    output_dir: str
    score: float
    summary: str


@dataclasses.dataclass(frozen=True)
class AggregatorInput(AgentInput):
    objective: str
    rubric: str
    candidates: tuple[CandidateSummary, ...]


class AggregatorOutput(SuccessfulOutput):
    """The chosen candidate and why the rubric prefers it over the rest of the slate."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    chosen_member_id: str = Field(description="member_id of the candidate this Search delivers.")
    output_dir: str = Field(description="output_dir of the chosen candidate, echoed for the final result.")
    score: float = Field(description="Score of the chosen candidate, echoed for the final result.")
    rationale: str = Field(description="Why the rubric prefers this candidate over the rest of the slate.")

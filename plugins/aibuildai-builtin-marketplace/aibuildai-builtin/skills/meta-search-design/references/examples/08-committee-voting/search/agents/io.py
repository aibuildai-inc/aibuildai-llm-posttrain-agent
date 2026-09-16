"""Typed Input and Output of one committee member's ballot."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput

from ..io import CommitteeMemberSpec, CommitteeOption


@dataclasses.dataclass(frozen=True)
class VoterInput(AgentInput):
    objective: str
    rubric: str
    options: tuple[CommitteeOption, ...]
    member: CommitteeMemberSpec


class VoterOutput(SuccessfulOutput):
    """One member's ballot: a chosen option, or an explicit abstention."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    member_id: str = Field(description="Must equal the member_id this ballot was cast for.")
    choice_id: str | None = Field(description="option_id of the chosen option; null when abstained.")
    abstained: bool = Field(description="True when the member declines to choose among the options.")
    rationale: str = Field(description="Why the rubric favors this choice, or why the member abstains.")

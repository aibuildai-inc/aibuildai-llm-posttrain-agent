"""Typed Input and Output of one match."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput

from ..io import Contestant


@dataclasses.dataclass(frozen=True)
class JudgeInput(AgentInput):
    objective: str
    rubric: str
    round_index: int
    match_index: int
    left: Contestant
    right: Contestant


class JudgeOutput(SuccessfulOutput):
    """A winner among the two supplied contestants, or an explicit tie."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    winner_id: str | None = Field(description="candidate_id of left or right; None when tied.")
    tied: bool = Field(description="True when the rubric does not prefer one contestant.")
    rationale: str = Field(description="Why the rubric favors the winner, or why neither wins.")

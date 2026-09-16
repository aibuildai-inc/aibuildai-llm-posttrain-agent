"""Typed Input and Output of one candidate attempt."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class CandidateInput(AgentInput):
    objective: str
    data_dir: str
    approach: str


class CandidateOutput(SuccessfulOutput):
    """One attempt's artifact and its score under the shared metric."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the model.")
    score: float = Field(description="Validation score under the task metric, higher is better.")
    summary: str = Field(description="What was trained, in one paragraph.")

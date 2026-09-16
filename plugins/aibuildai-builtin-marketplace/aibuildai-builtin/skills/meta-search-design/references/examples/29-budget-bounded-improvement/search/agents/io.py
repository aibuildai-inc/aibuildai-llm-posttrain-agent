"""Typed Input and Output of the improver role."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class ImproverInput(AgentInput):
    objective: str
    data_dir: str
    round_index: int
    previous_output_dir: str | None


class ImproverOutput(SuccessfulOutput):
    """One version of the candidate and its validation score."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding this version.")
    score: float = Field(description="Validation score under the task metric.")

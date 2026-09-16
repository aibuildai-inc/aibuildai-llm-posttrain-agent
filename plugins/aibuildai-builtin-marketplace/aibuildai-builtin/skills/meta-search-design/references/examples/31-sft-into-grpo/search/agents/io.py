"""Typed Input and Output of the one trainer role."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class TrainerInput(AgentInput):
    """Facts fixed for the whole run; which stage to run arrives as the call's message."""

    objective: str
    data_dir: str


class StageOutput(SuccessfulOutput):
    """What one stage produced: the checkpoint it trained and the score it measured on it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    stage: str = Field(description="'sft' or 'grpo': the stage this call ran.")
    checkpoint_dir: str = Field(description="Absolute directory of the checkpoint this stage produced.")
    metric_name: str = Field(description="Key of the eval report the score was read from; higher is better.")
    score: float = Field(description="The stage checkpoint's score on the fixed eval script.")
    notes: str = Field(description="What was trained, what the eval showed, and any crash that was repaired in-turn.")

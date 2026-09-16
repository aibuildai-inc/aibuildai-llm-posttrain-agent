"""Typed Input and Output of the modeler role."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class ModelerInput(AgentInput):
    """Everything the modeler needs to train and score one model."""

    objective: str
    data_dir: str


class ModelerOutput(SuccessfulOutput):
    """What the modeler left behind and how good it measured."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the model.")
    score: float = Field(description="Validation score under the task metric.")
    summary: str = Field(description="One paragraph: what was trained and how it was scored.")

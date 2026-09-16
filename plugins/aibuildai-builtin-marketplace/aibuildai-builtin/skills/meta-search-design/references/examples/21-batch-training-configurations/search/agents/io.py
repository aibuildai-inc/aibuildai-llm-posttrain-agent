"""Typed Input and Output of the designer."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class DesignerInput(AgentInput):
    objective: str
    data_dir: str
    max_configurations: int


class TrainingSpec(BaseModel):
    """One frozen configuration; nothing in it changes after the designer returns."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="Stable identifier, unique within the batch.")
    config_json: str = Field(description="The complete JSON document train.py reads as --config.")
    rationale: str = Field(description="Why this configuration is meaningfully different.")


class DesignerOutput(SuccessfulOutput):
    """The fixed source, entry contract, and the frozen configuration batch."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_dir: str = Field(description="Absolute directory holding train.py; never edited after this.")
    python_path: str = Field(description="Absolute python interpreter train.py runs under.")
    metric_name: str = Field(description="Key of metrics.json every run reports; higher is better.")
    specs: tuple[TrainingSpec, ...] = Field(min_length=1)

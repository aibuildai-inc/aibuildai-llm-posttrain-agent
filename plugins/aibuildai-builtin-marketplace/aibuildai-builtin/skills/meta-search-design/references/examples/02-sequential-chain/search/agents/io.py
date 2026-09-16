"""Typed Input and Output of each stage; stage A's Output shapes stage B's Input."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class PlannerInput(AgentInput):
    objective: str
    data_dir: str


class PlannerOutput(SuccessfulOutput):
    """A complete modeling plan the next stage can follow without re-deciding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    plan: str = Field(description="Model family, features, validation scheme, and the metric.")
    expected_pitfalls: str = Field(description="What the implementer must watch for in this data.")


@dataclasses.dataclass(frozen=True)
class ImplementerInput(AgentInput):
    objective: str
    data_dir: str
    plan: str
    expected_pitfalls: str


class ImplementerOutput(SuccessfulOutput):
    """What the implementer trained under the plan and how it measured."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the model.")
    score: float = Field(description="Validation score under the plan's metric.")
    deviations: str = Field(description="Where the implementation departed from the plan, or 'none'.")

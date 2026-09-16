"""Typed Input and Output of the expander, the rollout, and the trial roles, plus the shared action."""

from __future__ import annotations

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


class ActionSpec(BaseModel):
    """One legal transition from a state; never an arbitrary callable or module name."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    action_id: str = Field(description="Stable identifier, unique within its proposing call.")
    kind: Literal["set_hyperparameter", "evaluate"] = Field(
        description="'evaluate' ends the path and trains under every decision so far; anything else keeps it open."
    )
    detail: str = Field(description="Human-readable decision, for example 'learning_rate=3e-4'; empty for 'evaluate'.")


@dataclasses.dataclass(frozen=True)
class ExpanderInput(AgentInput):
    objective: str
    decisions: tuple[str, ...]
    branching_factor: int


class ExpanderOutput(SuccessfulOutput):
    """Up to `branching_factor` untried actions from one state; empty means no viable continuation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    actions: tuple[ActionSpec, ...] = Field(description="Distinct actions; at most the requested branching factor.")


@dataclasses.dataclass(frozen=True)
class RolloutInput(AgentInput):
    objective: str
    decisions: tuple[str, ...]
    depth: int
    max_depth: int


class RolloutOutput(SuccessfulOutput):
    """A value-only estimate of one non-terminal state's downstream promise."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    value: float = Field(ge=0.0, le=1.0, description="Estimated chance this path reaches a strong final score.")


@dataclasses.dataclass(frozen=True)
class TrialInput(AgentInput):
    """The 'evaluate' action: train once under every decision on the path and read the metric."""

    objective: str
    decisions: tuple[str, ...]
    data_dir: str
    source_dir: str
    metric_name: str


class TrialOutput(SuccessfulOutput):
    """The terminal result of one path: where the trained model is and what it scored."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the trained model and metrics.json.")
    score: float = Field(description="The value of metric_name in metrics.json; higher is better.")

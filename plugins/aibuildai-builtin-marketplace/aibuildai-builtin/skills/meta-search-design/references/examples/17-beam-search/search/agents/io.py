"""Typed Input and Output of the expander and the evaluator, plus the beam state."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


class BeamState(BaseModel):
    """One immutable partial pipeline: the steps applied so far and their dataset."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state_id: str = Field(description="Unique identifier within this Search run.")
    depth: int = Field(description="Number of transform steps already applied.")
    steps: tuple[str, ...] = Field(description="Transform names applied so far, in order.")
    dataset_dir: str = Field(description="Absolute directory holding the dataset at this state.")


class ExpansionChild(BaseModel):
    """One candidate next transform and the dataset it produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    step_name: str = Field(description="Name of the one transform this child applied.")
    dataset_dir: str = Field(description="Absolute directory holding the transformed dataset.")


@dataclasses.dataclass(frozen=True)
class ExpanderInput(AgentInput):
    objective: str
    data_dir: str
    steps_so_far: tuple[str, ...]
    dataset_dir: str
    max_children: int
    remaining_depth: int


class ExpanderOutput(SuccessfulOutput):
    """Bounded candidate next transforms, each already applied to its own dataset copy."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    children: tuple[ExpansionChild, ...] = Field(description="At most max_children candidates.")


@dataclasses.dataclass(frozen=True)
class EvaluatorInput(AgentInput):
    objective: str
    steps: tuple[str, ...]
    dataset_dir: str
    depth: int
    max_depth: int


class EvaluatorOutput(SuccessfulOutput):
    """The rubric judgment of one candidate dataset at its depth."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    score: float = Field(description="Cross-validated metric of this dataset, higher is better.")
    viable: bool = Field(description="False when the dataset breaks a hard constraint.")
    complete: bool = Field(description="True when this pipeline is ready to finalize.")
    reason: str = Field(description="One sentence explaining the score and the completeness call.")

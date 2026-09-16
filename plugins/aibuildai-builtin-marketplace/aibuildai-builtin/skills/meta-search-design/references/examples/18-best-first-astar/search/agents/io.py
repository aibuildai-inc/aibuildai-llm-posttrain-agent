"""Typed Input and Output of the expander and the heuristic role."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


class Candidate(BaseModel):
    """One proposed child pipeline: how it differs from its parent."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="Short label for this modification, unique within the wave.")
    config_json: str = Field(description="The complete JSON document train.py reads as --config.")
    change: str = Field(description="The one modification made relative to the parent config.")


@dataclasses.dataclass(frozen=True)
class ExpanderInput(AgentInput):
    objective: str
    data_dir: str
    source_dir: str
    parent_config_json: str
    branching_factor: int
    remaining_expansions: int


class ExpanderOutput(SuccessfulOutput):
    """Up to branching_factor pipeline modifications to try next."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    children: tuple[Candidate, ...] = Field(description="Proposed modifications; at most branching_factor entries.")


@dataclasses.dataclass(frozen=True)
class HeuristicInput(AgentInput):
    objective: str
    metric_name: str
    target_metric: float
    metric_lower_is_better: bool
    config_json: str
    observed_metric: float
    compute_seconds_spent: float


class HeuristicOutput(SuccessfulOutput):
    """The estimated compute a lineage still needs to reach the target metric."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    viable: bool = Field(description="False when this lineage cannot plausibly reach the target.")
    remaining_compute_seconds: float = Field(
        ge=0.0, description="Estimated additional compute seconds to reach the target."
    )
    rationale: str = Field(description="One sentence grounding the estimate in the observed trajectory.")

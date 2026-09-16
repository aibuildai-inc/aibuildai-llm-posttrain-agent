"""Typed Input and Output of every role in the graph."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class ProfileInput(AgentInput):
    """Operation A: inspect the raw data alone."""

    objective: str
    data_dir: str


class ProfileOutput(SuccessfulOutput):
    """The data shape and any columns the profiler judges risky."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    column_count: int = Field(description="Number of feature columns found.")
    flagged_columns: tuple[str, ...] = Field(
        description="Columns whose missingness or skew the profiler judges risky; empty if none."
    )
    summary: str = Field(description="One paragraph describing the data shape and any concerns.")


@dataclasses.dataclass(frozen=True)
class BaselineInput(AgentInput):
    """Operation B: train a quick default model, independent of the profile."""

    objective: str
    data_dir: str


class BaselineOutput(SuccessfulOutput):
    """The baseline model's score and which raw features it leaned on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the baseline model.")
    score: float = Field(description="Validation score under the task metric.")
    feature_importance_summary: str = Field(
        description="Which raw features the baseline weighted most, in prose."
    )


@dataclasses.dataclass(frozen=True)
class SelectorInput(AgentInput):
    """Join C: read both A's profile and B's baseline before choosing a feature set."""

    objective: str
    data_dir: str
    profile_summary: str
    flagged_columns: tuple[str, ...]
    baseline_score: float
    feature_importance_summary: str


class SelectorOutput(SuccessfulOutput):
    """The fixed training source and the frozen, feature-selected configuration."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_dir: str = Field(description="Absolute directory holding train.py; never edited after this.")
    metric_name: str = Field(description="Key of metrics.json the run reports; higher is better.")
    selected_features_json: str = Field(
        description="The complete JSON document train.py reads as --config, naming the chosen features."
    )
    rationale: str = Field(description="Why these features, given the profile and the baseline weights.")


@dataclasses.dataclass(frozen=True)
class QualityCheckInput(AgentInput):
    """Operation D: reuse A's profile only; independent of the selector's choice."""

    objective: str
    data_dir: str
    column_count: int
    flagged_columns: tuple[str, ...]
    profile_summary: str


class QualityCheckOutput(SuccessfulOutput):
    """Concrete risks a tuned model could inherit from the raw data."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    concerns: tuple[str, ...] = Field(
        description="Concrete risks that could invalidate a trained model; empty if none."
    )
    blocking: bool = Field(
        description="True when a concern is severe enough that no tuned model should be accepted."
    )


@dataclasses.dataclass(frozen=True)
class DecisionInput(AgentInput):
    """Final join F: read D's concerns and E's tuned metric together."""

    objective: str
    concerns: tuple[str, ...]
    blocking: bool
    metric: float
    metric_name: str


class DecisionOutput(SuccessfulOutput):
    """Whether the tuned model is accepted, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool = Field(description="True when the tuned model is fit to deliver.")
    summary: str = Field(description="The acceptance verdict and its reasoning, in one paragraph.")


@dataclasses.dataclass(frozen=True)
class TrainerInput(AgentInput):
    """Operation E: run the selector's frozen configuration once; decide nothing."""

    objective: str
    data_dir: str
    source_dir: str
    config_json: str
    metric_name: str


class TrainerOutput(SuccessfulOutput):
    """The one tuned run's artifacts and its metric."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding metrics.json and model.pt.")
    checkpoint_path: str = Field(description="Absolute path of the trained model.pt.")
    metric: float = Field(description="The value of metric_name read from metrics.json.")

"""Typed Input and Output of the strategist and the reviewer, plus shared records."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


class RoundRecord(BaseModel):
    """One finished round; kept so the strategist never repeats a rejected plan."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_profile: str
    accepted: bool
    reason: str = Field(description="Why the round was accepted, rejected, or offered for retry.")


class CompatibilityReport(BaseModel):
    """One environment's measured compatibility, read by the reviewer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_profile: str
    loaded: bool
    hard_passed: bool
    maximum_relative_error: float


@dataclasses.dataclass(frozen=True)
class PackagingStrategyInput(AgentInput):
    objective: str
    source_checkpoint_path: str
    offered_targets: tuple[str, ...]
    offered_precision_profiles: tuple[str, ...]
    offered_shape_profiles: tuple[str, ...]
    offered_converter_options: tuple[str, ...]
    offered_compatibility_profiles: tuple[str, ...]
    offered_tolerance_profiles: tuple[str, ...]
    prior_rounds: tuple[RoundRecord, ...]


class PackagingStrategyOutput(SuccessfulOutput):
    """One bounded export plan drawn only from the offered profiles."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    target_profile: str = Field(description="One value from offered_targets.")
    precision_profile: str = Field(description="One value from offered_precision_profiles.")
    shape_profile: str = Field(description="One value from offered_shape_profiles.")
    converter_options: tuple[str, ...] = Field(description="Subset of offered_converter_options.")
    compatibility_profiles: tuple[str, ...] = Field(
        min_length=1, description="Subset of offered_compatibility_profiles to validate this round."
    )
    tolerance_profile: str = Field(description="One value from offered_tolerance_profiles.")
    rationale: str = Field(description="Why this plan fits the objective.")


@dataclasses.dataclass(frozen=True)
class PackagingReviewInput(AgentInput):
    objective: str
    target_profile: str
    compatibility_reports: tuple[CompatibilityReport, ...]
    compatibility_failures: tuple[str, ...]
    mandatory_environment_profiles: tuple[str, ...]
    rounds_remaining: int


class PackagingReviewOutput(SuccessfulOutput):
    """Whether the round's package is acceptable, and whether another round is worth it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool = Field(description="True when the package is fit to release.")
    retry: bool = Field(description="True when a revised plan is worth another round.")
    score: float = Field(description="Comparable release quality; higher is better.")
    reason: str = Field(description="The concrete basis for the decision.")


@dataclasses.dataclass(frozen=True)
class ExportInput(AgentInput):
    """Export the immutable checkpoint once under the frozen plan; decide nothing."""

    objective: str
    source_checkpoint_path: str
    toolchain_dir: str
    target_profile: str
    precision_profile: str
    shape_profile: str
    converter_options: tuple[str, ...]


class ExportOutput(SuccessfulOutput):
    """Where the exported artifact is and its digest, fixed for every validation that follows."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    artifact_dir: str = Field(description="Absolute artifacts directory holding the exported model.")
    artifact_digest: str = Field(description="SHA-256 of the exported model file, so every validator can check it reads the same bytes.")


@dataclasses.dataclass(frozen=True)
class ValidationInput(AgentInput):
    """Validate the one exported artifact under one environment profile; never re-export."""

    objective: str
    toolchain_dir: str
    artifact_dir: str
    artifact_digest: str
    environment_profile: str
    tolerance_profile: str


class ValidationOutput(SuccessfulOutput):
    """One environment's verdict on the exported artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    environment_profile: str
    loaded: bool = Field(description="Whether the artifact loaded under this environment's runtime.")
    hard_passed: bool = Field(description="Whether prediction drift stayed under the tolerance profile.")
    maximum_relative_error: float = Field(ge=0.0)

"""Typed Input and Output of the package reviewer and the review reducer."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


class Defect(BaseModel):
    """One reviewer finding; stays attached to the package that produced it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    file: str = Field(description="Path of the affected file, relative to the package.")
    line: int = Field(ge=1, description="One-based line number of the defect.")
    severity: str = Field(description="One of: blocker, major, minor.")
    description: str = Field(description="What is wrong, in one sentence.")


class MappedPackage(BaseModel):
    """One package's typed review, ready for the reducer."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    package_path: str = Field(description="The declared package path this review covers.")
    defects: tuple[Defect, ...] = Field(description="Every defect found in this package.")
    summary: str = Field(description="What this package does and its overall health, in one paragraph.")


@dataclasses.dataclass(frozen=True)
class PackageReviewInput(AgentInput):
    objective: str
    repo_dir: str
    package_path: str


class PackageReviewOutput(SuccessfulOutput):
    """One package's defects and a one-paragraph summary."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    defects: tuple[Defect, ...] = Field(description="Every defect found in this package.")
    summary: str = Field(description="What this package does and its overall health, in one paragraph.")


@dataclasses.dataclass(frozen=True)
class ReviewReducerInput(AgentInput):
    objective: str
    declared_packages: tuple[str, ...]
    mapped_packages: tuple[MappedPackage, ...]


class ReviewReducerOutput(SuccessfulOutput):
    """The combined review, written to one artifact."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the written report.")
    report_path: str = Field(description="Absolute path of the written combined review, inside output_dir.")
    total_defects: int = Field(ge=0, description="Sum of defects across every mapped package.")
    blocker_count: int = Field(ge=0, description="Count of defects with severity 'blocker'.")

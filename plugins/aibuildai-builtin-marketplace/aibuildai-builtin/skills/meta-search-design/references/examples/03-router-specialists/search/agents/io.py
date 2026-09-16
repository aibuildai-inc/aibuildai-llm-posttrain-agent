"""Typed Input and Output of the router, the specialists, and the synthesizer."""

from __future__ import annotations

import dataclasses
from typing import Literal

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput

RouteLabel = Literal["data_analysis", "literature_review", "code_repair"]


@dataclasses.dataclass(frozen=True)
class RouterInput(AgentInput):
    objective: str
    data_dir: str


class RouterOutput(SuccessfulOutput):
    """The one or more routes this Input needs, most relevant first."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    routes: tuple[RouteLabel, ...] = Field(
        min_length=1, max_length=3, description="Distinct routes selected for this Input."
    )
    rationale: str = Field(description="Why these routes and not the others, in one paragraph.")


@dataclasses.dataclass(frozen=True)
class SpecialistInput(AgentInput):
    objective: str
    data_dir: str


class SpecialistOutput(SuccessfulOutput):
    """One route's artifact and how well it answers the objective."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding this route's work.")
    score: float = Field(description="Quality or confidence score for this route's result.")
    summary: str = Field(description="What this route found or produced, in one paragraph.")


@dataclasses.dataclass(frozen=True)
class SynthesisInput(AgentInput):
    objective: str
    reports: tuple[tuple[str, str], ...]


class SynthesisOutput(SuccessfulOutput):
    """The combined result built from every selected route's report."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the combined report.")
    score: float = Field(description="Quality score of the combined result.")
    summary: str = Field(description="How the routes were combined, in one paragraph.")

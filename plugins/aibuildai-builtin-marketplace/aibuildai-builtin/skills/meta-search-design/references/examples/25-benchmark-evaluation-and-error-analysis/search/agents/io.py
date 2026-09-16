"""Typed Input and Output of the planner and the analyst, plus the evidence records."""

from __future__ import annotations

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput

from ..io import BenchmarkProfile, CandidateRef


@dataclasses.dataclass(frozen=True)
class EvaluationPlanningInput(AgentInput):
    objective: str
    candidates: tuple[CandidateRef, ...]
    offered_benchmarks: tuple[BenchmarkProfile, ...]
    offered_slices: tuple[str, ...]
    max_evaluations: int


class BenchmarkTarget(BaseModel):
    """One frozen (candidate, benchmark) pairing; nothing in it changes after validation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_id: str = Field(description="Stable identifier, unique within the plan.")
    candidate_id: str = Field(description="Must name a candidate from the offered set.")
    candidate_path: str = Field(description="Must equal the offered candidate's own path.")
    benchmark_id: str = Field(description="Must name a benchmark from the offered set.")
    seed: int = Field(ge=0)
    repetitions: int = Field(ge=1, le=5)


class EvaluationPlanOutput(SuccessfulOutput):
    """The frozen benchmark plan; the Search validates it before any Program starts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    targets: tuple[BenchmarkTarget, ...] = Field(min_length=1)
    primary_metric: str = Field(description="Name of the metric component the analyst optimizes for.")
    metric_direction: Literal["maximize", "minimize"]
    requested_slices: tuple[str, ...] = Field(description="Must be a subset of the offered slices.")
    rationale: str = Field(description="Why this plan answers the objective.")


class SliceMetric(BaseModel):
    """One named slice's score and coverage."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    value: float
    count: int


class EvaluationRecord(BaseModel):
    """One successful evaluation's bounded evidence for the analyst."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_id: str
    candidate_id: str
    benchmark_id: str
    primary_score: float
    metric_components: tuple[tuple[str, float], ...]
    slices: tuple[SliceMetric, ...]
    evaluated_examples: int
    expected_examples: int
    report_path: str
    predictions_path: str


class EvaluationFailureRecord(BaseModel):
    """One evaluation that never produced valid evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    evaluation_id: str
    candidate_id: str
    benchmark_id: str
    expected_examples: int
    reason: str


@dataclasses.dataclass(frozen=True)
class ErrorAnalysisInput(AgentInput):
    objective: str
    primary_metric: str
    metric_direction: Literal["maximize", "minimize"]
    evaluations: tuple[EvaluationRecord, ...]
    failures: tuple[EvaluationFailureRecord, ...]


class ErrorAnalysisOutput(SuccessfulOutput):
    """The analyst's selection among the successful records, and why."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    selected_evaluation_id: str = Field(description="evaluation_id of a successful record to deliver.")
    diagnosis: str = Field(description="Which metric differences, slices, or failures drove the selection.")
    summary: str = Field(description="One sentence about what this evaluation found.")

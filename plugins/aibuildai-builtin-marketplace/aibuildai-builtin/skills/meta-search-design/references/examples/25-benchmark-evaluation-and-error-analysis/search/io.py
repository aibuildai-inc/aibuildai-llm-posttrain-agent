"""Input of the benchmark-evaluation Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CandidateRef(BaseModel):
    """One artifact the plan may name; its path is fixed before any Program runs."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    candidate_id: str = Field(description="Stable identifier, unique within the offered set.")
    candidate_path: str = Field(description="Read-only path to the candidate artifact.")


class BenchmarkProfile(BaseModel):
    """One benchmark the plan may pick; its dataset path never reaches an Agent Input."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    benchmark_id: str = Field(description="Stable identifier, unique within the offered set.")
    dataset_path: str = Field(description="Read-only path to the benchmark dataset or evaluator.")
    required_examples: int = Field(ge=1, description="Minimum examples a run must cover to count as complete.")


class BenchmarkAnalysisSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement the plan and analysis serve.")
    candidates: tuple[CandidateRef, ...] = Field(min_length=1, description="The closed set of evaluable candidates.")
    offered_benchmarks: tuple[BenchmarkProfile, ...] = Field(
        min_length=1, description="The closed set of benchmark profiles the plan may target."
    )
    offered_slices: tuple[str, ...] = Field(description="Slice names the plan may request.")
    max_evaluations: int = Field(default=6, ge=1, le=20)
    min_successful: int = Field(
        default=1, ge=1, description="Fewer evaluations with valid evidence than this is a Failure."
    )
    planner_wall_clock_seconds: int = Field(default=900, ge=60)
    benchmark_wall_clock_seconds: int = Field(default=1800, ge=60)
    analyst_wall_clock_seconds: int = Field(default=600, ge=60)

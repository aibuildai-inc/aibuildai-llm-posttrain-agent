"""Typed Input and Output of the orchestrator, the worker, and the synthesizer."""

from __future__ import annotations

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput

Role = Literal["data_check", "config_check", "eval_check"]


class Assignment(BaseModel):
    """One bounded worker task; frozen the moment the orchestrator returns it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assignment_id: str = Field(description="Stable identifier, unique across the whole study.")
    role: Role = Field(description="Which offered role should perform this assignment.")
    task_objective: str = Field(min_length=1, description="The one question this assignment answers.")


class AssignmentResult(BaseModel):
    """One completed assignment; kept in the evidence list the orchestrator sees next."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assignment_id: str
    role: Role
    finding: str
    evidence_path: str | None = Field(description="Absolute path to a file the worker wrote, or None.")


class AssignmentFailure(BaseModel):
    """One assignment a worker could not complete; evidence about execution, not a finding."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assignment_id: str
    role: Role
    reason: str


@dataclasses.dataclass(frozen=True)
class OrchestratorInput(AgentInput):
    objective: str
    repo_dir: str
    available_roles: tuple[Role, ...]


class OrchestratorOutput(SuccessfulOutput):
    """Either a bounded next wave of assignments or a decision to finalize."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assignments: tuple[Assignment, ...] = Field(description="Empty ends the study; non-empty means another wave.")
    ready_to_finalize: bool = Field(description="True once the evidence collected so far suffices.")
    unresolved_needs: tuple[str, ...] = Field(description="What is still missing, for the next wave.")
    reasoning: str = Field(description="Which uncertainty this wave separates, or why the evidence suffices.")


@dataclasses.dataclass(frozen=True)
class WorkerInput(AgentInput):
    assignment_id: str
    role: Role
    task_objective: str
    repo_dir: str


class WorkerOutput(SuccessfulOutput):
    """One typed result of exactly one assigned task."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    assignment_id: str = Field(description="Echoes the assignment this result belongs to.")
    finding: str = Field(min_length=1, description="What the worker learned, in one paragraph.")
    evidence_path: str | None = Field(default=None, description="Absolute path to a supporting file, or None.")


@dataclasses.dataclass(frozen=True)
class SynthesizerInput(AgentInput):
    objective: str
    completed: tuple[AssignmentResult, ...]
    failed: tuple[AssignmentFailure, ...]


class SynthesizerOutput(SuccessfulOutput):
    """The study's conclusion over every completed and failed assignment."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding the written report.")
    report_path: str = Field(description="Absolute path of the written root-cause report.")
    root_cause: str = Field(description="The root cause, or the best-supported explanation, in one paragraph.")
    confidence: float = Field(ge=0.0, le=1.0, description="How well the evidence supports root_cause.")

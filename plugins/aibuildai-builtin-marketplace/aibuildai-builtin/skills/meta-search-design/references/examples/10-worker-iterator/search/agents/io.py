"""Typed Input and Output of the worker and the iterator, plus the shared work item."""

from __future__ import annotations

import dataclasses
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput

from ..io import WorkKind


class WorkItem(BaseModel):
    """One bounded diagnostic action; the iterator emits exactly one per round."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    work_id: str = Field(description="Stable identifier for this work item, unique across the run.")
    kind: WorkKind = Field(description="Which bounded diagnostic action to perform.")
    target: str = Field(description="The one file, checkpoint, or probe script this action inspects or runs.")
    rationale: str = Field(description="Why this action is the single highest-value next step.")


class ObservationSummary(BaseModel):
    """One completed work item and what the worker found."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    work: WorkItem
    finding: str = Field(description="What the worker observed, in one paragraph.")
    artifact_ref: str | None = Field(default=None, description="Absolute path of any artifact the worker produced.")


@dataclasses.dataclass(frozen=True)
class WorkerInput(AgentInput):
    objective: str
    data_dir: str
    work: WorkItem


class WorkerOutput(SuccessfulOutput):
    """One typed observation from performing exactly one work item."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    finding: str = Field(description="What this action observed, in one paragraph.")
    artifact_ref: str | None = Field(default=None, description="Absolute path of any artifact this action produced.")


@dataclasses.dataclass(frozen=True)
class IteratorInput(AgentInput):
    objective: str
    available_work_kinds: tuple[WorkKind, ...]


class IteratorOutput(SuccessfulOutput):
    """Continue with one next work item, stop with a diagnosis, or fail."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    directive: Literal["continue", "stop", "fail"] = Field(description="What the search does next.")
    next_work: WorkItem | None = Field(
        default=None, description="The next bounded work item; present only when directive is continue."
    )
    report_dir: str | None = Field(
        default=None, description="Absolute directory holding the written diagnosis report; present only when directive is stop."
    )
    confidence: float | None = Field(
        default=None, ge=0.0, le=1.0, description="Confidence in the diagnosis; present only when directive is stop."
    )
    stop_reason: str | None = Field(
        default=None, description="Why the search stopped or failed; present when directive is stop or fail."
    )

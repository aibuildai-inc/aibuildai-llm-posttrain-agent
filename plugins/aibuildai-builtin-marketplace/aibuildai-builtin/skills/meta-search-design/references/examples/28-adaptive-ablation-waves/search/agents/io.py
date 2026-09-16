"""Typed Input and Output of the orchestrator and the analyst, plus the evidence ledger."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


class Cell(BaseModel):
    """One frozen ablation cell: how it differs from its parent, and nothing else."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str = Field(description="Stable identifier, unique across the study.")
    parent: str | None = Field(description="Name of the cell this one differs from; None only for the baseline.")
    config_json: str = Field(description="The complete JSON document train.py reads as --config.")
    factor_change: str = Field(description="The one factor changed relative to the parent, or 'baseline'.")


class CellRecord(BaseModel):
    """One completed cell in the evidence ledger; a failed cell stays recorded."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    cell: Cell
    wave: int
    metric: float | None = Field(description="None when the cell failed.")
    failure_reason: str | None = None


@dataclasses.dataclass(frozen=True)
class OrchestratorInput(AgentInput):
    objective: str
    data_dir: str
    max_cells: int


class OrchestratorOutput(SuccessfulOutput):
    """Either a bounded next wave or a decision to finalize."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_dir: str = Field(description="Absolute directory holding train.py; fixed after wave 1.")
    python_path: str = Field(description="Absolute python interpreter train.py runs under.")
    metric_name: str = Field(description="Key of metrics.json every cell reports; higher is better.")
    next_wave: tuple[Cell, ...] = Field(description="Empty means finalize now.")
    reasoning: str = Field(description="Which uncertainty this wave separates, or why the evidence suffices.")


@dataclasses.dataclass(frozen=True)
class AnalystInput(AgentInput):
    objective: str
    metric_name: str
    ledger: tuple[CellRecord, ...]


class AnalystOutput(SuccessfulOutput):
    """The study's conclusion over the complete ledger."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    best_cell: str = Field(description="Name of the cell whose artifact is delivered.")
    report_path: str = Field(description="Absolute path of the written ablation report.")
    conclusion: str = Field(description="Main effects, interactions, and limits, in one paragraph.")

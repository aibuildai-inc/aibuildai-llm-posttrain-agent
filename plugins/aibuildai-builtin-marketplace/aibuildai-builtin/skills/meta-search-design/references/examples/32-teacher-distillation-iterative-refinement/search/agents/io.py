"""Typed Input and Output of the worker and the diagnosis roles."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


class CategoryScore(BaseModel):
    """One named category's score from one evaluation."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    category: str
    score: float


@dataclasses.dataclass(frozen=True)
class WorkerInput(AgentInput):
    """Facts fixed for the whole run; each round's seeds arrive in the call's message."""

    objective: str
    data_dir: str
    rows_per_round: int


class RoundOutput(SuccessfulOutput):
    """What one round produced: the corpus so far, the checkpoint it trained, and its scores."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    round_index: int
    teacher_model: str = Field(description="Identifier of the teacher served this round, quantized as needed to fit the card.")
    corpus_rows: int = Field(ge=0, description="Rows in the accumulated corpus after this round's generation.")
    checkpoint_dir: str = Field(description="Absolute directory of the checkpoint this round trained on the whole corpus.")
    overall_score: float
    per_category: tuple[CategoryScore, ...] = Field(min_length=1)


@dataclasses.dataclass(frozen=True)
class DiagnosisInput(AgentInput):
    objective: str
    data_dir: str
    round_index: int
    checkpoint_dir: str
    overall_score: float
    per_category: tuple[CategoryScore, ...]
    best_overall_score: float
    remaining_seconds: float


class DiagnosisOutput(SuccessfulOutput):
    """Either targeted seeds for the next round, or a decision to finalize."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    finalize: bool = Field(description="True ends the loop; seed_prompts is then ignored.")
    weak_categories: tuple[str, ...] = Field(description="Categories the next round targets; empty when finalize.")
    seed_prompts: tuple[str, ...] = Field(description="Targeted seed prompts for the next round's generation.")
    reasoning: str = Field(description="Why these categories, or why the evidence suffices to stop.")

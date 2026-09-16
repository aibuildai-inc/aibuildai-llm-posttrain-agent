"""Typed Input and Output of the recon and the round roles."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class ReconInput(AgentInput):
    objective: str
    data_dir: str
    eval_script: str


class ReconOutput(SuccessfulOutput):
    """The frozen data policy and training configuration every round trains under."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    prepared_data_dir: str = Field(description="Absolute directory holding the rendered, decontaminated training data.")
    base_model_or_checkpoint: str = Field(description="Absolute path or identifier of the model round 1 starts from.")
    train_config_json: str = Field(description="The complete JSON document the trainer reads as its training config.")
    save_steps: int = Field(ge=1, description="Checkpoint save cadence, in training steps.")


@dataclasses.dataclass(frozen=True)
class RoundInput(AgentInput):
    """One train-tournament-soup round; every value was frozen before it starts."""

    objective: str
    data_dir: str
    eval_script: str
    soup_script: str
    sft_module: str
    round_index: int
    base_model_or_checkpoint: str
    prepared_data_dir: str
    train_config_json: str
    save_steps: int
    soup_k: int


class ScoredCheckpoint(BaseModel):
    """One checkpoint the round scored on the official evaluation script."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    checkpoint_name: str
    checkpoint_dir: str
    checkpoint_step: int
    score: float


class RoundOutput(SuccessfulOutput):
    """Every checkpoint the round scored, the soup if one was made, and the round's winner."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    scored: tuple[ScoredCheckpoint, ...] = Field(min_length=1, description="Every checkpoint of this round with its score, in step order.")
    soup_dir: str = Field(description="Absolute directory of the souped checkpoint, or empty when no soup was made.")
    soup_score: float | None = Field(description="The soup's score under the same evaluation, or null when no soup was made.")
    best_name: str = Field(description="Name of the round's winner: a checkpoint or the soup.")
    best_dir: str = Field(description="Absolute directory of the round's winner.")
    best_score: float = Field(description="The winner's score.")

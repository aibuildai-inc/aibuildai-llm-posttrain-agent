"""Typed Input and Output of the seed and the round roles."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class SeedInput(AgentInput):
    objective: str
    data_dir: str
    eval_script: str
    sample_script: str


class SeedOutput(SuccessfulOutput):
    """The supervised seed the rounds start from, its measured score, and what the rounds sample on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    seed_checkpoint: str = Field(
        description=(
            "Absolute path of the supervised checkpoint round 1 starts from. On-policy "
            "rounds train on the student's own output, so this model must already answer "
            "in the task's format."
        )
    )
    seed_score: float = Field(description="The seed's score on the official evaluation, the number every round must beat.")
    prompt_set_path: str = Field(description="Absolute path to the prompt file every round samples the student on.")
    train_config_json: str = Field(description="The complete JSON document both training methods read as their config.")
    verifier_note: str = Field(description="One sentence naming what the sample script counts as a correct completion.")


@dataclasses.dataclass(frozen=True)
class RoundInput(AgentInput):
    """One sample-train-evaluate round on the standing checkpoint; the method rule is part of the Input."""

    objective: str
    data_dir: str
    eval_script: str
    sample_script: str
    rft_module: str
    opd_module: str
    teacher_model: str
    round_index: int
    parent_checkpoint: str
    prompt_set_path: str
    train_config_json: str
    samples_per_prompt: int
    rft_min_yield: float
    forced_method: str


class RoundOutput(SuccessfulOutput):
    """What the round drew, which method it therefore ran, and the checkpoint it produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    yield_rate: float = Field(ge=0.0, le=1.0, description="Fraction of sampled completions the verifier accepted.")
    method: str = Field(description="'rft' or 'opd': the training method the yield rule (or the forced method) selected.")
    checkpoint_dir: str = Field(description="Absolute directory of the checkpoint this round trained.")
    score: float = Field(description="The round checkpoint's score on the official evaluation.")

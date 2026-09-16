"""Typed Input and Output of the parent, the two judge roles, and the trainer."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class ParentInput(AgentInput):
    objective: str
    data_dir: str
    eval_script: str
    judge_script: str


class ParentOutput(SuccessfulOutput):
    """The supervised parent, its score, the prompt set, and the graded pairs the judge is measured on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parent_checkpoint: str = Field(description="Absolute path of the supervised checkpoint the preference stage starts from and is finally souped with.")
    parent_score: float = Field(description="The parent's score on the official evaluation, the number every branch is compared against.")
    prompt_set_path: str = Field(description="Absolute path to the prompts the student writes candidates for.")
    calibration_pairs_path: str = Field(description="Absolute path to held-out completion pairs the official grader has already separated, used to measure the judge and never to train on.")
    train_config_json: str = Field(description="The complete JSON document both training methods read as their config.")
    rubric_note: str = Field(description="One sentence naming the quality the grader rewards, for the judge prompt.")


@dataclasses.dataclass(frozen=True)
class CalibrationInput(AgentInput):
    """Measure the judge against the grader on pairs the grader already separated."""

    objective: str
    judge_script: str
    judge_model: str
    calibration_pairs_path: str
    rubric_note: str


class CalibrationOutput(SuccessfulOutput):
    model_config = ConfigDict(frozen=True, extra="forbid")

    agreement: float = Field(ge=0.0, le=1.0, description="Fraction of graded pairs the judge ordered the same way as the grader.")
    pairs_measured: int = Field(ge=1)


@dataclasses.dataclass(frozen=True)
class RankingInput(AgentInput):
    """Draw candidates from the parent and rank them with the judge; never sees the grader."""

    objective: str
    data_dir: str
    sample_script: str
    judge_script: str
    judge_model: str
    parent_checkpoint: str
    prompt_set_path: str
    candidates_per_prompt: int
    rubric_note: str


class RankingOutput(SuccessfulOutput):
    model_config = ConfigDict(frozen=True, extra="forbid")

    pair_count: int = Field(ge=0, description="Chosen-rejected pairs the judge separated.")
    mean_margin: float = Field(ge=0.0, description="Mean judge score gap between the chosen and the rejected side.")
    pairs_path: str = Field(description="Absolute path of the chosen-rejected pairs for DPO.")
    best_only_path: str = Field(description="Absolute path of the top-ranked completion per prompt for RAFT.")


@dataclasses.dataclass(frozen=True)
class TrainerInput(AgentInput):
    """Train one preference method from the parent, evaluate it, soup it with the parent, evaluate the soup."""

    objective: str
    data_dir: str
    eval_script: str
    soup_script: str
    dpo_module: str
    raft_module: str
    method: str
    parent_checkpoint: str
    ranked_path: str
    train_config_json: str


class Candidate(BaseModel):
    """One scored model the branch produced."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    name: str
    checkpoint_dir: str
    score: float


class TrainerOutput(SuccessfulOutput):
    model_config = ConfigDict(frozen=True, extra="forbid")

    candidates: tuple[Candidate, ...] = Field(description="The trained model and its soup with the parent, each with a score; a step that failed is absent and named in notes.")
    notes: str = Field(description="What trained, what scored, and any step that failed and why.")

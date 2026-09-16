"""Typed Input and Output of the worker and the fixer roles."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class WorkerInput(AgentInput):
    """Facts fixed for the whole run; each call's message names the round to run."""

    objective: str
    data_dir: str
    continuation_learning_rate: float


class RoundOutput(SuccessfulOutput):
    """One trained checkpoint, its score, and the failures the evaluation recorded on it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    round_name: str = Field(description="'initial' for the first SFT, else the fix round name the message gave.")
    checkpoint_dir: str = Field(description="Absolute directory of the checkpoint this round produced.")
    score: float = Field(ge=0.0, le=1.0, description="Score on the fixed evaluation, higher is better.")
    failures_path: str = Field(description="Absolute path of failures.jsonl: one JSON object per wrong example with input, expected, produced.")
    conventions_path: str = Field(description="Absolute path of the doc naming the output conventions the eval enforces, for the fixer to read.")
    replay_data_dir: str = Field(description="Absolute directory of the initial SFT data the fixer draws replay samples from.")


@dataclasses.dataclass(frozen=True)
class FixerInput(AgentInput):
    objective: str
    failures_path: str
    conventions_path: str
    replay_data_dir: str
    replay_fraction: float
    current_score: float


class FixerOutput(SuccessfulOutput):
    """The diagnosed, fixable error categories and the synthetic dataset that targets them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    fixable_errors: tuple[str, ...] = Field(
        description="Categories the fixer judged fixable by data, e.g. 'omits optional arg X'. Empty means none of the failures are fixable by more data."
    )
    fix_data_dir: str = Field(
        description="Absolute directory holding the synthetic fix dataset plus its mixed-in replay sample; empty when fixable_errors is empty."
    )

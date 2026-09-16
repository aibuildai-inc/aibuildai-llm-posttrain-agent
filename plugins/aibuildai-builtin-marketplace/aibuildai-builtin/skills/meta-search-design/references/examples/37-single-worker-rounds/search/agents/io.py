"""Typed Input and Output of the worker role."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class WorkerInput(AgentInput):
    """Facts fixed for the whole run; each round's instruction arrives as the call's message."""

    objective: str
    data_dir: str


class RoundOutput(SuccessfulOutput):
    """What one round produced: the best checkpoint it measured and what it learned."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    round_index: int
    checkpoint_dir: str = Field(description="Absolute directory of the best checkpoint this round measured; loadable as a full model.")
    score: float = Field(description="That checkpoint's score on the official scorer, on the same held-out slice every round uses.")
    eval_slice: str = Field(description="Which held-out slice the score was measured on, so the Search compares like with like.")
    next_step: str = Field(description="What the worker would change in the next round, and why, or 'stop' with a reason.")

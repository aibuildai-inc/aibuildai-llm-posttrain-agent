"""Typed Input and Output of the producer and the evaluator."""

from __future__ import annotations

import dataclasses

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


@dataclasses.dataclass(frozen=True)
class ProducerInput(AgentInput):
    objective: str
    data_dir: str


class ProducerOutput(SuccessfulOutput):
    """One version of the candidate and the producer's own score for it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    output_dir: str = Field(description="Absolute artifacts directory holding this version.")
    score: float = Field(description="Validation score under the task metric.")
    changes: str = Field(description="What this version changed against the previous one, or 'initial'.")


@dataclasses.dataclass(frozen=True)
class EvaluatorInput(AgentInput):
    objective: str
    rubric: str
    output_dir: str
    score: float
    changes: str


class EvaluatorOutput(SuccessfulOutput):
    """The rubric verdict and, when rejected, feedback the producer can act on."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool = Field(description="True when every rubric item holds.")
    feedback: str = Field(description="Concrete defects to fix; empty when accepted.")

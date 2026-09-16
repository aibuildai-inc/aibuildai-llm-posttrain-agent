"""Input and Output records for the writer Agent family."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.metric_contract import MetricContractInput
from engine.work_unit.agent.base import AgentInput

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class WriterInput(AgentInput):
    """Input for one paper written, checked, compiled, and published by WRITER."""

    instruction: str
    metric_contract: MetricContractInput | None


class WriterOutput(SuccessfulOutput):
    model_config = ConfigDict(
        json_schema_extra={
            "description": inspect.cleandoc(
                """The machine-readable facts about the paper YOU wrote.

        The paper itself is not here. You write it as a complete LaTeX
        document under your own artifacts and compile it yourself, so the
        product needs nothing of the paper itself -- only notes for your
        reviewer."""
            )
        }
    )
    notes: str = Field(
        default="",
        description=(
            "Optional author notes for the writer_review verifier: which artifacts "
            "each metric was read from, "
            "and any caveats. Not part of the paper."
        ),
    )


@dataclass(frozen=True)
class WriterReviewInput(AgentInput):
    """The stable side of one paper review: where the paper is."""

    paper_dir: str

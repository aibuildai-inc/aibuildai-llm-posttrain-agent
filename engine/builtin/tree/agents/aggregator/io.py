"""Input and Output records for the aggregator Agent family."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import Field

from engine.execution_output import SuccessfulOutput
from engine.metric_contract import MetricContractInput
from engine.work_unit.agent.base import AgentInput

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class AggregatorInput(AgentInput):
    mem_cap: str
    # Only the members this ensemble was told to combine. Its own upstream IS
    # those members, so nothing here carries the rest of the population.
    member_uids: tuple[str, ...]
    # The exact directory each member's own score was measured on, so the
    # Aggregator reads the scored output instead of walking a Composite
    # tree to guess which directory holds it.
    member_output_dirs: tuple[str, ...]
    member_metrics: tuple[float | None, ...]
    metric_contract: MetricContractInput | None


class AggregatorOutput(SuccessfulOutput):
    feedback: str = Field(
        default="",
        description=(
            "Ensemble decision summary: which method was chosen and why "
            "(probability averaging / weighted blend / rank averaging / hard "
            "voting / etc.), which designs participated and which "
            "were excluded with reasons, key blending weights, "
            "and any caveats about the "
            "output it wrote (calibration applied, members it had to skip)."
        ),
    )
    soft_blended_member_uids: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Composite UIDs whose continuous outputs your ensemble SOFT-AVERAGES into "
            "its own output (probability / logit / rank blending). Record it so a "
            "reader of the run can see which designs the blend was built from. Leave "
            "EMPTY when the output is not a soft average of member outputs: a "
            "single-model pick, hard voting, or a trained stacker / meta-learner."
        ),
    )

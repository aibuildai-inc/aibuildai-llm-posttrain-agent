"""Input of the budget-bounded improvement Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BudgetBoundedImprovementInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every round serves.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    round_wall_clock_seconds: int = Field(
        default=1800,
        ge=60,
        description="The local wall clock one improvement round declares; a round starts only while the exploration remainder pays for it.",
    )
    round_cost_usd: float = Field(
        default=2.0,
        gt=0,
        description="The cost one round is expected to spend; a round starts only while the cost remainder pays for it, when a cost limit exists.",
    )

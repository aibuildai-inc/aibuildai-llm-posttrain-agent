"""Input of the Monte Carlo Tree Search Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class MCTSSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every role serves.")
    data_dir: str = Field(description="Read-only directory holding the task training data.")
    source_dir: str = Field(description="Read-only directory holding the fixed train.py.")
    metric_name: str = Field(description="Key of metrics.json an evaluate action reports; higher is better.")
    max_simulations: int = Field(ge=1, le=200, description="Hard cap on MCTS iterations; the Search stops here.")
    max_depth: int = Field(ge=1, le=8, description="Maximum decisions on one path from the root.")
    branching_factor: int = Field(ge=1, le=4, description="Maximum untried actions the expander may propose per node.")
    exploration_constant: float = Field(gt=0.0, description="UCT exploration weight; higher favors under-visited actions.")
    discount: float = Field(default=1.0, ge=0.0, le=1.0, description="Per-ancestor decay applied to a backed-up reward.")
    expander_wall_clock_seconds: int = Field(default=300, ge=30)
    rollout_wall_clock_seconds: int = Field(default=300, ge=30)
    trial_wall_clock_seconds: int = Field(default=1800, ge=60, description="Wall clock of one trial role: one train.py run and its metric.")

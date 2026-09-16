"""Input of the heuristic best-first Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class BestFirstSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every role serves.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    source_dir: str = Field(description="Read-only directory holding the fixed train.py.")
    python_path: str = Field(description="Absolute python interpreter train.py runs under.")
    initial_config_json: str = Field(description="The baseline JSON document train.py reads as --config.")
    metric_name: str = Field(description="Key of metrics.json every trial reports.")
    target_metric: float = Field(description="The metric value a complete pipeline must reach.")
    metric_lower_is_better: bool = Field(description="True when a smaller metric is better.")
    branching_factor: int = Field(default=3, ge=1, le=8, description="Maximum children proposed per expansion.")
    max_expansions: int = Field(default=10, ge=1, le=200, description="Hard budget of frontier expansions.")
    max_frontier_size: int = Field(
        default=15, ge=1, le=500, description="Trim the frontier to this many entries after each expansion."
    )
    expander_wall_clock_seconds: int = Field(default=600, ge=60)
    trial_wall_clock_seconds: int = Field(default=1800, ge=60)
    heuristic_wall_clock_seconds: int = Field(default=300, ge=60)

"""Input of the evolutionary Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class EvolutionarySearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement every role serves.")
    data_dir: str = Field(description="Read-only directory holding the task data.")
    metric_name: str = Field(description="Key of metrics.json every run reports; higher is better.")
    population_size: int = Field(default=4, ge=2, le=8, description="Individuals kept after each generation.")
    max_generations: int = Field(default=3, ge=1, le=6, description="Hard cap on generations, including the first.")
    elite_count: int = Field(default=1, ge=1, le=4, description="Best individuals copied unchanged into the next population.")
    parent_count: int = Field(default=2, ge=1, le=4, description="Individuals selected as mutation parents each generation.")
    min_feasible: int = Field(default=1, ge=1, description="Fewer individuals reaching a metric than this is a Failure.")
    initializer_wall_clock_seconds: int = Field(default=1200, ge=60)
    mutation_wall_clock_seconds: int = Field(default=900, ge=60)
    evaluation_wall_clock_seconds: int = Field(default=1800, ge=60)

"""Input of the worker-iterator Search."""

from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

WorkKind = Literal["inspect_config", "inspect_logs", "inspect_checkpoint", "run_probe_script"]


class WorkerIteratorSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim diagnostic objective the loop investigates.")
    data_dir: str = Field(description="Read-only directory holding the run's config, logs, and checkpoints.")
    available_work_kinds: tuple[WorkKind, ...] = Field(
        description="The closed set of diagnostic actions the iterator may choose from."
    )
    max_iterations: int = Field(default=5, ge=1, le=12, description="Hard budget of worker-iterator rounds.")
    worker_wall_clock_seconds: int = Field(default=600, ge=60)
    iterator_wall_clock_seconds: int = Field(default=300, ge=60)

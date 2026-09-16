"""Typed Input and Output of the initializer and the mutation role, and the shared Individual."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


class Individual(BaseModel):
    """One frozen configuration; nothing in it changes once it is created."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    individual_id: str = Field(description="Stable identifier, unique within the population.")
    generation: int = Field(description="Generation this individual was created in; 0 for the initial population.")
    parent_id: str | None = Field(description="individual_id of the mutated parent, or None for the initial population.")
    config_json: str = Field(description="The complete JSON document train.py reads as --config.")


@dataclasses.dataclass(frozen=True)
class InitializerInput(AgentInput):
    objective: str
    data_dir: str
    population_size: int
    metric_name: str


class InitializerOutput(SuccessfulOutput):
    """The fixed source, entry contract, and the diverse initial population."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    source_dir: str = Field(description="Absolute directory holding train.py; never edited after this.")
    python_path: str = Field(description="Absolute python interpreter train.py runs under.")
    individuals: tuple[Individual, ...] = Field(min_length=2, description="The initial population; every genome meaningfully different.")


@dataclasses.dataclass(frozen=True)
class MutationInput(AgentInput):
    objective: str
    parent: Individual
    fitness_score: float
    generation: int


class MutationOutput(SuccessfulOutput):
    """One or more offspring derived from the parent, and what changed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    offspring: tuple[Individual, ...] = Field(min_length=1, description="New individuals derived from the parent.")
    mutation_summary: str = Field(description="What changed from the parent, in one sentence.")

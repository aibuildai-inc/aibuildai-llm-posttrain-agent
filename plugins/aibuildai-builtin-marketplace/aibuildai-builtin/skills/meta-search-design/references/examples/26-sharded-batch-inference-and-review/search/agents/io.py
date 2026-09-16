"""Typed Input and Output of the strategy and review roles, plus their shared records."""

from __future__ import annotations

import dataclasses

from pydantic import BaseModel, ConfigDict, Field

from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent import AgentInput


class InferencePolicy(BaseModel):
    """One frozen inference policy; nothing in it changes once a round's shards start."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy_id: str = Field(description="Stable identifier, unique across rounds.")
    model_id: str = Field(description="Must be one of the offered model IDs.")
    prompt_profile: str = Field(description="Must be one of the offered prompt profiles.")
    decoding_profile: str = Field(description="Must be one of the offered decoding profiles.")
    max_input_chars: int = Field(ge=1, le=4000, description="Records longer than this are a known failure.")


class ShardSpec(BaseModel):
    """One stable, contiguous shard of the manifest."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    shard_id: str
    first_record_index: int = Field(ge=0)
    record_count: int = Field(ge=1)


class ShardRecord(BaseModel):
    """One shard that produced output, as the review sees it."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    shard_id: str
    output_path: str
    completed_records: int
    oversized_records: int = Field(description="Records over the policy's max_input_chars; a known failure.")


class ShardFailureRecord(BaseModel):
    """One shard whose Program itself failed; it produced no output."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    shard_id: str
    reason: str


class PolicyReviewRecord(BaseModel):
    """One completed round; kept so the next strategy round can read what already failed."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    round_number: int
    policy: InferencePolicy
    accepted: bool
    score: float
    reason: str


@dataclasses.dataclass(frozen=True)
class StrategyInput(AgentInput):
    objective: str
    offered_models: tuple[str, ...]
    offered_prompt_profiles: tuple[str, ...]
    offered_decoding_profiles: tuple[str, ...]
    prior_reviews: tuple[PolicyReviewRecord, ...]


class StrategyOutput(SuccessfulOutput):
    """One bounded policy the parent freezes before any shard starts."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    policy: InferencePolicy
    rationale: str = Field(description="Why this policy fits the objective, in one paragraph.")


@dataclasses.dataclass(frozen=True)
class ReviewInput(AgentInput):
    objective: str
    policy: InferencePolicy
    shard_records: tuple[ShardRecord, ...]
    shard_failures: tuple[ShardFailureRecord, ...]
    rounds_remaining: int


class ReviewOutput(SuccessfulOutput):
    """The round's verdict over the merged shard evidence."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    accepted: bool
    score: float = Field(description="Aggregate quality proxy for this round, higher is better.")
    reason: str = Field(description="Which coverage, error pattern, or confidence fact drove the verdict.")

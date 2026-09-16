"""Input of the sharded-inference Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class ShardedInferenceSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim inference goal every role serves.")
    input_manifest_path: str = Field(description="Read-only JSONL file; one record object per line.")
    total_record_count: int = Field(ge=1, description="Exact number of records in the manifest, in order.")
    shard_count: int = Field(ge=1, le=8, description="Number of disjoint, contiguous shards.")
    offered_models: tuple[str, ...] = Field(min_length=1, description="Closed set of model IDs to choose from.")
    offered_prompt_profiles: tuple[str, ...] = Field(min_length=1)
    offered_decoding_profiles: tuple[str, ...] = Field(min_length=1)
    allowed_failed_records: int = Field(ge=0, description="Coverage floor: at most this many failed records total.")
    max_rounds: int = Field(default=2, ge=1, le=4)
    strategy_wall_clock_seconds: int = Field(default=600, ge=60)
    shard_wall_clock_seconds: int = Field(default=1800, ge=60)
    review_wall_clock_seconds: int = Field(default=600, ge=60)

"""Apply one frozen policy to one immutable shard; decide nothing."""

from __future__ import annotations

from engine.durable_execution import action

import itertools
import json
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pydantic import ConfigDict

from engine.execution_output import SuccessfulOutput
from engine.failure import Failure, FailureKind
from engine.work_unit.program import Policy, Program


@dataclass(frozen=True)
class ShardInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    policy_id: str
    model_id: str
    prompt_profile: str
    decoding_profile: str
    max_input_chars: int
    shard_id: str
    first_record_index: int
    record_count: int
    input_manifest_path: str
    artifact_dir: str


class ShardOutput(SuccessfulOutput):
    shard_id: str
    output_path: str
    completed_records: int
    oversized_records: int


class InferenceShardProgram(Program[ShardInput]):
    """Read one manifest slice, apply the frozen policy, write one isolated file."""

    name: ClassVar[str] = "inference_shard"
    policy = Policy(stop_grace_seconds=10.0)

    @action
    def run(self) -> ShardOutput | Failure:
        first = self.input.first_record_index
        last = first + self.input.record_count
        with open(self.input.input_manifest_path, encoding="utf-8") as manifest:
            lines = list(itertools.islice(manifest, first, last))
        if len(lines) != self.input.record_count:
            return Failure(
                kind=FailureKind.ARTIFACT_MISSING,
                reason=f"{self.input.shard_id}: manifest had {len(lines)} records; expected {self.input.record_count}",
            )

        output_dir = Path(self.input.artifact_dir)
        output_dir.mkdir(parents=True, exist_ok=True)
        output_path = output_dir / f"{self.input.shard_id}.jsonl"

        completed = 0
        oversized = 0
        with open(output_path, "w", encoding="utf-8") as sink:
            for line in lines:
                if self.stop_requested:
                    return Failure(
                        kind=FailureKind.TIMEOUT,
                        reason=f"{self.input.shard_id} was stopped at its wall-clock boundary",
                    )
                record = json.loads(line)
                text = record["text"]
                if len(text) > self.input.max_input_chars:
                    oversized += 1
                    continue
                sink.write(
                    json.dumps(
                        {
                            "record_id": record["record_id"],
                            "policy_id": self.input.policy_id,
                            "model_id": self.input.model_id,
                            "decoding_profile": self.input.decoding_profile,
                            "output_length": len(text),
                        }
                    )
                    + "\n"
                )
                completed += 1

        return ShardOutput(
            shard_id=self.input.shard_id,
            output_path=str(output_path),
            completed_records=completed,
            oversized_records=oversized,
        )

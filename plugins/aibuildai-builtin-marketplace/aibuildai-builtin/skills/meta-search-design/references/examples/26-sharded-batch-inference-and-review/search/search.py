"""Freeze one policy, run it over every shard in parallel, validate coverage, review."""

from __future__ import annotations

from engine.durable_execution import Handle
from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import (
    InferencePolicy,
    PolicyReviewRecord,
    ReviewInput,
    ShardFailureRecord,
    ShardRecord,
    ShardSpec,
    StrategyInput,
)
from .agents.review import InferenceReviewAgent
from .agents.strategy import InferenceStrategyAgent
from .io import ShardedInferenceSearchInput
from .programs.shard import InferenceShardProgram, ShardInput


def create_stable_shards(total_record_count: int, shard_count: int) -> tuple[ShardSpec, ...]:
    base, remainder = divmod(total_record_count, shard_count)
    shards = []
    start = 0
    for index in range(shard_count):
        count = base + (1 if index < remainder else 0)
        if count == 0:
            continue
        shards.append(ShardSpec(shard_id=f"shard-{index:03d}", first_record_index=start, record_count=count))
        start += count
    return tuple(shards)


def validate_policy(policy: InferencePolicy, search_input: ShardedInferenceSearchInput) -> tuple[str, ...]:
    problems = []
    if policy.model_id not in search_input.offered_models:
        problems.append(f"policy names unoffered model {policy.model_id}")
    if policy.prompt_profile not in search_input.offered_prompt_profiles:
        problems.append(f"policy names unoffered prompt profile {policy.prompt_profile}")
    if policy.decoding_profile not in search_input.offered_decoding_profiles:
        problems.append(f"policy names unoffered decoding profile {policy.decoding_profile}")
    return tuple(problems)


def validate_coverage(
    total_record_count: int, shard_records: tuple[ShardRecord, ...], shard_failures: tuple[ShardFailureRecord, ...], allowed_failed_records: int
) -> tuple[str, ...]:
    problems = []
    if shard_failures:
        problems.append(f"{len(shard_failures)} shard(s) failed: {shard_failures[0].reason}")
    covered = sum(record.completed_records + record.oversized_records for record in shard_records)
    if covered != total_record_count:
        problems.append(f"covered {covered} records; expected {total_record_count}")
    oversized = sum(record.oversized_records for record in shard_records)
    if oversized > allowed_failed_records:
        problems.append(f"{oversized} oversized records exceed the floor of {allowed_failed_records}")
    return tuple(problems)


class ShardedInferenceSearch(Search[ShardedInferenceSearchInput, SearchOutput]):
    """One policy is frozen per round; every Program owns exactly one stable shard."""

    async def explore(self) -> SearchOutput | Failure:
        prior_reviews: list[PolicyReviewRecord] = []
        reviewed_by: tuple[Handle, ...] = ()

        for round_number in range(1, self.input.max_rounds + 1):
            strategy_agent = InferenceStrategyAgent(
                input=StrategyInput(
                    objective=self.input.objective,
                    offered_models=self.input.offered_models,
                    offered_prompt_profiles=self.input.offered_prompt_profiles,
                    offered_decoding_profiles=self.input.offered_decoding_profiles,
                    prior_reviews=tuple(prior_reviews),
                ),
            )
            strategy_handle = await self.ctx.spawn(
                strategy_agent.run,
                "Start the assigned work.",
                upstream=reviewed_by,
                read=(task_data(), *(h.files() for h in reviewed_by),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.strategy_wall_clock_seconds,
                    gpus=0,
                ),
            )
            strategy = await strategy_handle.result()

            problems = validate_policy(strategy.policy, self.input)
            if problems:
                return Failure(kind=FailureKind.PERMANENT, reason="; ".join(problems))

            shards = create_stable_shards(self.input.total_record_count, self.input.shard_count)
            round_dir = f"{self.artifacts_dir}/round_{round_number}"

            programs = {
                shard.shard_id: InferenceShardProgram(
                    input=ShardInput(
                        policy_id=strategy.policy.policy_id,
                        model_id=strategy.policy.model_id,
                        prompt_profile=strategy.policy.prompt_profile,
                        decoding_profile=strategy.policy.decoding_profile,
                        max_input_chars=strategy.policy.max_input_chars,
                        shard_id=shard.shard_id,
                        first_record_index=shard.first_record_index,
                        record_count=shard.record_count,
                        input_manifest_path=self.input.input_manifest_path,
                        artifact_dir=round_dir,
                    ),
                )
                for shard in shards
            }
            handles = {
                shard_id: await self.ctx.spawn(
                    program.run,
                    upstream=(strategy_handle,),
                    capture_failure=True,
                    read=(task_data(), strategy_handle.files(),),
                    capability=ExecutionCapability(
                        wall_clock_seconds=self.input.shard_wall_clock_seconds,
                        gpus=1,
                        retry=1,
                    ),
                )
                for shard_id, program in programs.items()
            }
            await self.ctx.wait(set(handles.values()))

            shard_records = []
            shard_failures = []
            for shard in shards:
                outcome = await handles[shard.shard_id].result()
                if isinstance(outcome, Failure):
                    shard_failures.append(ShardFailureRecord(shard_id=shard.shard_id, reason=outcome.reason))
                    continue
                shard_records.append(
                    ShardRecord(
                        shard_id=outcome.shard_id,
                        output_path=outcome.output_path,
                        completed_records=outcome.completed_records,
                        oversized_records=outcome.oversized_records,
                    )
                )

            problems = validate_coverage(
                self.input.total_record_count, tuple(shard_records), tuple(shard_failures), self.input.allowed_failed_records
            )
            if problems:
                return Failure(kind=FailureKind.NO_OUTPUT, reason="; ".join(problems))

            review_agent = InferenceReviewAgent(
                input=ReviewInput(
                    objective=self.input.objective,
                    policy=strategy.policy,
                    shard_records=tuple(shard_records),
                    shard_failures=tuple(shard_failures),
                    rounds_remaining=self.input.max_rounds - round_number,
                ),
            )
            review_handle = await self.ctx.spawn(
                review_agent.run,
                "Start the assigned work.",
                upstream=(strategy_handle, *handles.values()),
                read=(
task_data(),
strategy_handle.files(),
*(h.files() for h in handles.values()),
),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.review_wall_clock_seconds, gpus=0
                ),
            )
            review = await review_handle.result()
            reviewed_by = (review_handle,)

            prior_reviews.append(
                PolicyReviewRecord(
                    round_number=round_number,
                    policy=strategy.policy,
                    accepted=review.accepted,
                    score=review.score,
                    reason=review.reason,
                )
            )
            if review.accepted:
                oversized_total = sum(record.oversized_records for record in shard_records)
                return SearchOutput(
                    output_dir=round_dir,
                    score=review.score,
                    components=(("oversized_records", float(oversized_total)),),
                )

        return Failure(
            kind=FailureKind.NO_OUTPUT,
            reason="inference policy rounds were exhausted without an accepted round",
        )

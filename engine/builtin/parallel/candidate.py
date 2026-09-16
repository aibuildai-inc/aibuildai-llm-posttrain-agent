"""The linear package's own Composites.

One candidate is Worker -> Score: a single session writes the code, runs it,
reads its own errors, fixes them, and exports the result, and the product
Score Program measures the directory it filled. The Ensemble is Aggregator ->
Score. Everything a Composite here needs arrives on its own frozen Input: it
reads no configuration, resolves no capability by role name, and asks the run
nothing about work it did not start itself.

A failed Worker Action stays failed: this package never scores or salvages
whatever files a Worker that did not finish left behind."""

from __future__ import annotations

from engine.builtin.aibuildai.programs.score import ScoreOutput
from engine.builtin.parallel.agents.aggregator.agent import AggregatorAgent
from engine.builtin.parallel.agents.aggregator.io import AggregatorInput
from engine.builtin.parallel.agents.worker.agent import WorkerAgent
from engine.builtin.parallel.agents.worker.io import WorkerInput
from engine.builtin.parallel.io import (
    EnsembleInput,
    WorkerCandidateInput,
    WorkerCandidateOutput,
)
from engine.composite import Composite
from engine.builtin.aibuildai.io import task_data
from engine.durable_execution import action
from engine.failure import Failure

class WorkerCandidate(Composite[WorkerCandidateInput]):
    """One complete Worker attempt of one chain, scored after it finishes."""

    @action
    async def run(self) -> "WorkerCandidateOutput | Failure":
        payload = self.input
        grant = payload.grant
        gpu_count = payload.requested_gpus
        worker = WorkerAgent(
            input=WorkerInput(
                plan=payload.plan,
                revision=payload.revision,
                parent=payload.parent,
                metric_contract=payload.metric_contract,
                task_name=grant.task_name,
                min_revisions=grant.min_revisions,
                num_revisions=grant.num_revisions,
                gpu_count=gpu_count,
                progress_enabled=grant.progress_enabled,
                external_scores=payload.external_scores,
                reviewer_capability=grant.worker_reviewer,
            ),
        )
        handle = await self.ctx.spawn(
            worker.run,
            "Start the assigned work.",
            read=(
                task_data(),
                *(() if payload.parent_files is None else (payload.parent_files,)),
            ),
            capture_failure=True,
            capability=grant.worker.model_copy(update={"gpus": gpu_count}),
        )
        worker_output = await handle.result()
        # A failed Worker stays failed: this candidate is never scored from
        # whatever files a Worker that did not finish left behind.
        if worker_output.failed is True:
            return worker_output
        # The producer owns its output: the attempt this candidate scores is
        # the one its Worker wrote under that Worker's own artifacts.
        scored_handle = await grant.score.measure(
            self.ctx,
            handle,
            output_dir=f"{worker.artifacts_dir}/full",
            attempt=worker.files(),
        )
        scored = await scored_handle.result()
        if scored.failed is True:
            return scored
        return WorkerCandidateOutput(
            score=scored.score,
            output_dir=scored.output_dir,
            components=scored.components,
            source_dir=worker.scratch_dir,
            plan=payload.plan,
            revisions=worker_output.revisions,
            feedback=worker_output.feedback,
        )


class Ensemble(Composite[EnsembleInput]):
    """One bounded combination of every chain's already scored candidates."""

    @action
    async def run(self) -> "ScoreOutput | Failure":
        payload = self.input
        aggregator = AggregatorAgent(
            input=AggregatorInput(
                mem_cap=payload.memory_cap,
                member_uids=tuple(member.uid for member in payload.members),
                member_output_dirs=tuple(
                    member.output_dir for member in payload.members
                ),
                member_metrics=tuple(member.score for member in payload.members),
                metric_contract=payload.metric_contract,
            ),
        )
        # The members are this Composite's own upstream; the Aggregator reads
        # them through its Input.
        handle = await self.ctx.spawn(
            aggregator.run,
            "Start the assigned work.",
            # Derived from the member collection: N members are N references,
            # never N hand-written paths.
            read=(task_data(), *(member.files for member in payload.members)),
            capture_failure=True,
            capability=payload.aggregator,
        )
        output = await handle.result()
        if output.failed is True:
            return output
        scored_handle = await payload.score.measure(
            self.ctx,
            handle,
            output_dir=f"{aggregator.artifacts_dir}/full",
            attempt=aggregator.files(),
        )
        return await scored_handle.result()

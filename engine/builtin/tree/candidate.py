"""The tree package's own Composites.

One candidate is Coder -> Training -> Score. The Ensemble is Aggregator ->
Score. Everything a Composite here needs arrives on its own frozen Input: it
reads no configuration, resolves no capability by role name, and asks the run
nothing about work it did not start itself. A fresh design and a revision are
one operation, told apart by the parent on that Input."""

from __future__ import annotations

from engine.builtin.aibuildai.programs.score import ScoreOutput
from engine.builtin.tree.agents.aggregator.agent import AggregatorAgent
from engine.builtin.tree.agents.aggregator.io import AggregatorInput
from engine.builtin.tree.agents.coder.agent import CoderAgent, coder_source_dir
from engine.builtin.tree.agents.coder.io import CoderInput
from engine.builtin.tree.agents.reviser.io import ScoredCandidateOutput
from engine.builtin.tree.io import (
    CoderCandidateInput,
    EnsembleInput,
)
from engine.builtin.tree.programs import TrainingInput, TrainingProgram
from engine.composite import Composite
from engine.builtin.aibuildai.io import task_data
from engine.durable_execution import action
from engine.failure import Failure

class CoderCandidate(Composite[CoderCandidateInput]):
    """One selected proposal, implemented, trained and scored."""

    @action
    async def run(self) -> "ScoredCandidateOutput | Failure":
        payload = self.input
        grant = payload.grant
        parent = payload.parent
        gpu_count = payload.requested_gpus
        # The Coder identity is kept beside its running Action because the
        # source tree it owns is the framework's own fact, addressed from that
        # identity, and Training needs it after the Action returns.
        coder = CoderAgent(
            input=CoderInput(
                proposal=payload.proposal,
                inherited_source_dir="" if parent is None else parent.source_dir,
                judge_dimensions=payload.verdict.dimensions,
                judge_rationale=payload.verdict.rationale,
                selector_feedback=payload.selector_feedback,
                parent_uid=None if parent is None else parent.uid,
                parent_metric=None if parent is None else parent.score,
                parent_metric_components=() if parent is None else parent.components,
                parent_feedback="" if parent is None else parent.coder_feedback,
                parent_output_dir=None if parent is None else parent.output_dir,
                task_name=grant.task_name,
                train_file_name=grant.train_file_name,
                cpu_cores=grant.training_cpu_cores,
                gpu_count=gpu_count,
                coder_budget_minutes=grant.coder_budget_minutes,
                training_budget_minutes=grant.training_budget_minutes,
                progress_enabled=grant.progress_enabled,
                reviewer_capability=grant.coder_review,
            ),
        )
        coder_handle = await self.ctx.spawn(
            coder.run,
            "Start the assigned work.",
            read=(
                task_data(),
                *(() if parent is None else (parent.files,)),
            ),
            capture_failure=True,
            capability=grant.coder.model_copy(update={"gpus": gpu_count}),
        )
        coder_output = await coder_handle.result()
        if coder_output.failed is True:
            return coder_output
        source_dir = coder_source_dir(coder)
        training = TrainingProgram(
            input=TrainingInput(
                expected_full_training_minutes=coder_output.expected_full_training_minutes,
                source_dir=source_dir,
                data_dir=task_data().path(),
            ),
            # The one dynamic capability in this package: the wall clock comes
            # from the immutable budget rule applied to the Coder's own typed
            # estimate, with no configuration read at the spawn site.
        )
        training_handle = await self.ctx.spawn(
            training.run,
            upstream=(coder_handle,),
            # It copies the Coder's source into its own scratch and trains
            # there; the attempt it fills is its own artifacts. It writes
            # nothing outside itself, so it is granted nothing writable.
            read=(task_data(), coder.files()),
            capture_failure=True,
            capability=grant.training.capability(
                float(coder_output.expected_full_training_minutes), gpus=gpu_count
            ),
        )
        training_output = await training_handle.result()
        if training_output.failed is True:
            return training_output
        scored_handle = await grant.score.measure(
            self.ctx,
            training_handle,
            output_dir=training_output.output_dir,
            attempt=self.files(),
        )
        scored = await scored_handle.result()
        if scored.failed is True:
            return scored
        return ScoredCandidateOutput(
            score=scored.score,
            output_dir=scored.output_dir,
            components=scored.components,
            source_dir=source_dir,
            training_dir=training.artifacts_dir,
            coder_feedback=coder_output.feedback,
        )


class Ensemble(Composite[EnsembleInput]):
    """One bounded combination of already scored candidates."""

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

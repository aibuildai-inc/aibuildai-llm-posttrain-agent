"""Grow one corpus round by round with one worker; diagnose between rounds; keep the best checkpoint."""

from __future__ import annotations

from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.failure import Failure, FailureKind
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.diagnosis import DiagnosisAgent
from .agents.io import DiagnosisInput, WorkerInput
from .agents.worker import DistillWorkerAgent
from .io import TeacherDistillSearchInput


class TeacherDistillSearch(Search[TeacherDistillSearchInput, SearchOutput]):
    """One worker identity carries the corpus and the scripts in its own conversation across rounds."""

    async def explore(self) -> SearchOutput | Failure:
        worker = DistillWorkerAgent(
            input=WorkerInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                rows_per_round=self.input.rows_per_round,
            ),
        )

        previous: tuple[Handle, ...] = ()
        seed_prompts = self.input.seed_prompts
        best_score = -float("inf")
        best_checkpoint_dir = ""

        for round_index in range(1, self.input.max_rounds + 1):
            budget = await self.budget.snapshot()
            if budget.wall_clock_remaining_s < self.input.min_round_seconds:
                break

            seeds = "\n".join(f"- {seed}" for seed in seed_prompts)
            round_handle = await self.ctx.spawn(
                worker.run, f"Run round {round_index}. Seed prompts:\n{seeds}", upstream=previous,
                capture_failure=True, read=(task_data(), *(h.files() for h in previous),),
                capability=ExecutionCapability(wall_clock_seconds=self.input.worker_seconds, gpus=1),
            )
            outcome = await round_handle.result()
            previous = (round_handle,)
            if isinstance(outcome, Failure):
                if not best_checkpoint_dir:
                    return outcome
                break

            if outcome.overall_score > best_score:
                best_score = outcome.overall_score
                best_checkpoint_dir = outcome.checkpoint_dir

            budget = await self.budget.snapshot()
            if budget.wall_clock_remaining_s < self.input.min_round_seconds:
                break

            diagnosis = DiagnosisAgent(
                input=DiagnosisInput(
                    objective=self.input.objective,
                    data_dir=self.input.data_dir,
                    round_index=round_index,
                    checkpoint_dir=outcome.checkpoint_dir,
                    overall_score=outcome.overall_score,
                    per_category=outcome.per_category,
                    best_overall_score=best_score,
                    remaining_seconds=budget.wall_clock_remaining_s,
                ),
            )
            diagnosis_handle = await self.ctx.spawn(
                diagnosis.run, "Start the assigned work.", upstream=(round_handle,),
                read=(task_data(), round_handle.files(),),
                capability=ExecutionCapability(wall_clock_seconds=self.input.diagnosis_seconds, gpus=0),
            )
            diagnosis_out = await diagnosis_handle.result()
            previous = (diagnosis_handle,)
            if diagnosis_out.finalize:
                break
            seed_prompts = diagnosis_out.seed_prompts

        if not best_checkpoint_dir:
            return Failure(kind=FailureKind.NO_OUTPUT, reason="no round produced an evaluated checkpoint")
        return SearchOutput(
            output_dir=best_checkpoint_dir,
            score=best_score,
            components=(("overall", best_score),),
        )

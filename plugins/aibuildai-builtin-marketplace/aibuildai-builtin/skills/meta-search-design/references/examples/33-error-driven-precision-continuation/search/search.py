"""Train once, then loop fixer -> continuation -> evaluate while the budget and the score allow."""

from __future__ import annotations

from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.failure import Failure
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.fixer import FixerAgent
from .agents.io import FixerInput, WorkerInput
from .agents.worker import PrecisionWorkerAgent
from .io import PrecisionSearchInput


class PrecisionSearch(Search[PrecisionSearchInput, SearchOutput]):
    """One worker identity trains every checkpoint; the fixer only ever sees its own eval failures."""

    async def explore(self) -> SearchOutput | Failure:
        worker = PrecisionWorkerAgent(
            input=WorkerInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                continuation_learning_rate=self.input.continuation_learning_rate,
            ),
        )
        # Every round of this identity gets the same card and the same clock,
        # written once here and declared on each call.
        round_capability = ExecutionCapability(
            wall_clock_seconds=self.input.worker_wall_clock_seconds, gpus=1
        )
        initial_handle = await self.ctx.spawn(
            worker.run, "Run round initial.", capture_failure=True, read=(task_data(),),
            capability=round_capability,
        )
        initial = await initial_handle.result()
        if isinstance(initial, Failure):
            return initial

        best_score = initial.score
        best_checkpoint = initial.checkpoint_dir
        latest_failures_path = initial.failures_path
        previous_handle: Handle = initial_handle
        improved = True
        fix_round = 0

        while (
            improved
            and fix_round < self.input.max_fix_rounds
            and best_score < self.input.target_score
        ):
            budget = await self.budget.snapshot()
            if budget.wall_clock_remaining_s < self.input.fix_round_seconds:
                break

            fix_round += 1
            round_name = f"fix_r{fix_round}"

            fixer = FixerAgent(
                input=FixerInput(
                    objective=self.input.objective,
                    failures_path=latest_failures_path,
                    conventions_path=initial.conventions_path,
                    replay_data_dir=initial.replay_data_dir,
                    replay_fraction=self.input.replay_fraction,
                    current_score=best_score,
                ),
            )
            fixer_handle = await self.ctx.spawn(
                fixer.run,
                "Start the assigned work.",
                upstream=(previous_handle,),
                # The conventions and the replay slice were written once, by
                # the initial run, and every fix round reads them. The round
                # it waits for is whichever ran last, which after round 1 is
                # not that producer any more.
                read=(task_data(), initial_handle.files(), previous_handle.files()),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.fixer_wall_clock_seconds, gpus=0
                ),
            )
            fix_out = await fixer_handle.result()
            if not fix_out.fixable_errors:
                break

            round_handle = await self.ctx.spawn(
                worker.run,
                f"Run fix round {round_name}: continue from {best_checkpoint} on {fix_out.fix_data_dir}.",
                upstream=(fixer_handle,), capture_failure=True, read=(task_data(), fixer_handle.files(),),
                capability=round_capability,
            )
            round_out = await round_handle.result()

            if isinstance(round_out, Failure):
                improved = False
            elif round_out.score > best_score:
                best_score = round_out.score
                best_checkpoint = round_out.checkpoint_dir
                latest_failures_path = round_out.failures_path
                previous_handle = round_handle
            else:
                improved = False

        return SearchOutput(output_dir=best_checkpoint, score=best_score)

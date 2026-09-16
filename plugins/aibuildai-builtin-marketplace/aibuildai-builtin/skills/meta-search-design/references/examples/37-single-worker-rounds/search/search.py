"""One worker, called once per round while the budget pays; the Search keeps the best checkpoint."""

from __future__ import annotations

from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.failure import Failure, FailureKind
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import WorkerInput
from .agents.worker import WorkerAgent
from .io import SingleWorkerRoundsSearchInput


class SingleWorkerRoundsSearch(Search[SingleWorkerRoundsSearchInput, SearchOutput]):
    """The worker does the work; the Search only loops on the budget and keeps what scored best."""

    async def explore(self) -> SearchOutput | Failure:
        worker = WorkerAgent(
            input=WorkerInput(objective=self.input.objective, data_dir=self.input.data_dir),
        )

        best_score = -float("inf")
        best_dir = ""
        eval_slice = ""
        upstream: tuple[Handle, ...] = ()
        message = "Run round 1."
        history: list[str] = []

        for round_idx in range(1, self.input.max_rounds + 1):
            budget = await self.budget.snapshot()
            if budget.wall_clock_remaining_s < self.input.min_round_seconds:
                history.append(f"stopped before round {round_idx}: budget")
                break

            round_handle = await self.ctx.spawn(
                worker.run,
                message,
                upstream=upstream,
                capture_failure=True,
                read=(task_data(),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.worker_wall_clock_seconds, gpus=1
                ),
            )
            outcome = await round_handle.result()
            upstream = (round_handle,)
            if isinstance(outcome, Failure):
                history.append(f"round {round_idx} failed: {outcome.reason}")
                if not best_dir:
                    return outcome
                break

            # Scores compare only on the same slice; the worker names it, and the
            # Search refuses a round that switched slices instead of guessing.
            if eval_slice and outcome.eval_slice != eval_slice:
                history.append(f"round {round_idx} scored on {outcome.eval_slice}, not {eval_slice}; ignored")
            elif outcome.score > best_score:
                eval_slice = outcome.eval_slice
                best_score, best_dir = outcome.score, outcome.checkpoint_dir
                history.append(f"round {round_idx}: {outcome.score:.4f}, new best")
            else:
                history.append(f"round {round_idx}: {outcome.score:.4f}")

            if outcome.next_step.strip().lower().startswith("stop"):
                history.append(f"worker stopped: {outcome.next_step}")
                break
            message = (
                f"Run round {round_idx + 1}. Continue from the best checkpoint so far, {best_dir} "
                f"({best_score:.4f} on {eval_slice}). Your own proposal: {outcome.next_step}"
            )

        if not best_dir:
            return Failure(kind=FailureKind.NO_OUTPUT, reason="; ".join(history) or "no round ran")
        return SearchOutput(output_dir=best_dir, score=best_score)

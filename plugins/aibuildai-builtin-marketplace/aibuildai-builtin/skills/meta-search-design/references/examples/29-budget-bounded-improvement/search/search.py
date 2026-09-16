"""Improve one candidate in rounds while the exploration budget pays for another round."""

from __future__ import annotations

from engine.durable_execution import Handle
from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.improver import ImproverAgent
from .agents.io import ImproverInput
from .io import BudgetBoundedImprovementInput


class BudgetBoundedImprovementSearch(
    Search[BudgetBoundedImprovementInput, SearchOutput]
):
    """One lineage, one round per budget check; the snapshot decides each repeat."""

    async def explore(self) -> SearchOutput | Failure:
        previous: Handle | None = None
        version = None
        round_index = 0

        while True:
            budget = await self.budget.snapshot()
            if budget.wall_clock_remaining_s < self.input.round_wall_clock_seconds:
                break
            if (
                budget.cost_remaining_usd is not None
                and budget.cost_remaining_usd < self.input.round_cost_usd
            ):
                break

            round_index += 1
            improver = ImproverAgent(
                input=ImproverInput(
                    objective=self.input.objective,
                    data_dir=self.input.data_dir,
                    round_index=round_index,
                    previous_output_dir=(
                        None if version is None else version.output_dir
                    ),
                ),
            )
            improver_handle = await self.ctx.spawn(
                improver.run,
                "Start the assigned work.",
                upstream=() if previous is None else (previous,),
                read=(
                    task_data(),
                    *(() if previous is None else (previous.files(),)),
                ),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.round_wall_clock_seconds,
                    gpus=0,
                ),
            )
            version = await improver_handle.result()
            previous = improver_handle

            # What this round actually used: the same accounting the global
            # snapshot reads, asked about the exact Action this round ran.
            # A round that used its whole local wall clock says another round
            # of the same size would be cut off too.
            spent = await self.budget.snapshot(improver_handle)
            if spent.wall_clock_local_remaining_s == 0.0:
                break

        if version is None:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason="the exploration budget could not pay for even one round",
            )
        return SearchOutput(
            output_dir=version.output_dir,
            score=version.score,
        )

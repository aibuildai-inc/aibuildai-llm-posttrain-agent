"""Spawn N candidates at once, wait for all, keep the highest score."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.candidate import CandidateAgent
from .agents.io import CandidateInput, CandidateOutput
from .io import BestOfNSearchInput


class BestOfNSearch(Search[BestOfNSearchInput, SearchOutput]):
    """Every candidate is a substitute; one survives."""

    async def explore(self) -> SearchOutput | Failure:
        handles = [
            await self.ctx.spawn(
                CandidateAgent(
                    input=CandidateInput(
                        objective=self.input.objective,
                        data_dir=self.input.data_dir,
                        approach=approach,
                    ),
                ).run,
                "Start the assigned work.",
                capture_failure=True, read=(task_data(),),
                capability=ExecutionCapability(
                        wall_clock_seconds=self.input.candidate_wall_clock_seconds,
                        gpus=0,
                    ),
            )
            for approach in self.input.approaches
        ]
        await self.ctx.wait(handles)

        successes: list[tuple[str, CandidateOutput]] = []
        for approach, handle in zip(self.input.approaches, handles, strict=True):
            outcome = await handle.result()
            if not isinstance(outcome, Failure):
                successes.append((approach, outcome))

        if len(successes) < self.input.min_successful:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=(
                    f"{len(successes)} of {len(handles)} candidates succeeded; "
                    f"at least {self.input.min_successful} required"
                ),
            )
        approach, best = max(successes, key=lambda item: item[1].score)
        return SearchOutput(
            output_dir=best.output_dir,
            score=best.score,
        )

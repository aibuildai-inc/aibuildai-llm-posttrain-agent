"""Spawn heterogeneous members at once, wait for all, let one Aggregator pick the winner."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.aggregator import AggregatorAgent
from .agents.io import AggregatorInput, CandidateSummary, MemberInput
from .agents.member import MemberAgent
from .io import MixtureSearchInput


class MixtureOfAgentsSearch(Search[MixtureSearchInput, SearchOutput]):
    """Layer 1 proposes in parallel; the Aggregator reads the whole slate and decides."""

    async def explore(self) -> SearchOutput | Failure:
        members = [
            MemberAgent(
                input=MemberInput(
                    objective=self.input.objective,
                    data_dir=self.input.data_dir,
                    role=spec.role,
                    emphasis=spec.emphasis,
                ),
            )
            for spec in self.input.members
        ]
        handles = [
            await self.ctx.spawn(
                member.run,
                "Produce your candidate.",
                capture_failure=True,
                read=(task_data(),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.member_wall_clock_seconds,
                    gpus=0,
                ),
            )
            for member in members
        ]
        await self.ctx.wait(handles)

        candidates: list[CandidateSummary] = []
        proposed_by: list[Handle] = []
        for spec, handle in zip(self.input.members, handles, strict=True):
            outcome = await handle.result()
            if isinstance(outcome, Failure):
                continue
            candidates.append(
                CandidateSummary(
                    member_id=spec.member_id,
                    role=spec.role,
                    output_dir=outcome.output_dir,
                    score=outcome.score,
                    summary=outcome.summary,
                )
            )
            proposed_by.append(handle)

        if len(candidates) < self.input.min_successful_members:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=(
                    f"{len(candidates)} of {len(members)} members produced a candidate; "
                    f"at least {self.input.min_successful_members} required"
                ),
            )

        aggregator = AggregatorAgent(
            input=AggregatorInput(
                objective=self.input.objective,
                rubric=self.input.rubric,
                candidates=tuple(candidates),
            ),
        )
        final_handle = await self.ctx.spawn(
            aggregator.run,
            "Choose the winning candidate.",
            upstream=tuple(proposed_by),
            read=(task_data(), *(h.files() for h in proposed_by),),
            capability=ExecutionCapability(
                wall_clock_seconds=self.input.aggregator_wall_clock_seconds,
                gpus=0,
            ),
        )
        final = await final_handle.result()

        return SearchOutput(
            output_dir=final.output_dir,
            score=final.score,
        )

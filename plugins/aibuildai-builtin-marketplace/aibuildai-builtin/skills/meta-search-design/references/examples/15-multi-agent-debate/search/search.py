"""Run a debate: each participant is one identity across its turns; the judge is fresh."""

from __future__ import annotations

from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.failure import Failure, FailureKind
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.debater import DebaterAgent
from .agents.io import (
    Critique,
    DebateTurnOutput,
    DebaterInput,
    JudgeInput,
    Position,
)
from .agents.judge import JudgeAgent
from .io import DebateSearchInput


def _slate(positions: tuple[Position, ...]) -> str:
    return "\n".join(
        f"- {item.participant_id}: {item.claim} ({item.proposal_dir})"
        for item in positions
    )


class MultiAgentDebateSearch(Search[DebateSearchInput, SearchOutput]):
    """Keep one identity per debater and use one independent judge."""

    async def explore(self) -> SearchOutput | Failure:
        debaters = {
            participant.participant_id: DebaterAgent(
                input=DebaterInput(
                    objective=self.input.objective,
                    data_dir=self.input.data_dir,
                    participant=participant,
                ),
            )
            for participant in self.input.participants
        }
        return await self._debate(debaters)

    async def _debate(
        self, debaters: dict[str, DebaterAgent]
    ) -> SearchOutput | Failure:
        # A debater speaks three times, and every turn is its own Action, so
        # each turn declares what it may spend. They happen to be equal here.
        turn = ExecutionCapability(
            wall_clock_seconds=self.input.debater_wall_clock_seconds, gpus=0
        )
        positions: list[Position] = []
        # The opening Actions themselves, not the debater identities: each
        # debater speaks three times, so an identity would resolve later to
        # its critique or its rebuttal instead of the opening being consumed.
        opening: dict[str, Handle[DebateTurnOutput | Failure]] = {}
        for participant_id, debater in debaters.items():
            spoke = await self.ctx.spawn(
                debater.run, "Write your opening position before you see the other positions.",
                capture_failure=True, read=(task_data(),), capability=turn,
            )
            output = await spoke.result()
            if not isinstance(output, Failure) and output.position is not None:
                positions.append(output.position)
                opening[participant_id] = spoke
        if len(positions) < self.input.minimum_positions:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=f"only {len(positions)} opening positions succeeded",
            )

        opened = tuple(positions)
        # Each phase consumes the phase before it, so each phase names the
        # exact Actions that produced what it reads: the critique reads the
        # openings, the rebuttal reads the critiques, and the Judge reads the
        # critiques and rebuttals its Input was built from.
        slate = tuple(opening[item.participant_id] for item in opened)
        critiques: list[Critique] = []
        critiqued: list[Handle[DebateTurnOutput | Failure]] = []
        for debater in debaters.values():
            spoke = await self.ctx.spawn(
                debater.run, f"Critique one public position that is not yours.\n\n{_slate(opened)}",
                upstream=slate, capture_failure=True, read=(task_data(),), capability=turn,
            )
            output = await spoke.result()
            critiqued.append(spoke)
            if not isinstance(output, Failure):
                critiques.extend(output.critiques)

        final_positions: list[Position] = []
        rebutted: list[Handle[DebateTurnOutput | Failure]] = []
        for participant_id, debater in debaters.items():
            if participant_id not in {item.participant_id for item in opened}:
                continue
            received = (
                "\n".join(
                    f"- [{item.severity}] {item.issue}"
                    for item in critiques
                    if item.target_id == participant_id
                )
                or "No participant critiqued your position."
            )
            spoke = await self.ctx.spawn(
                debater.run, f"Answer the critiques and publish your final claim.\n\n{received}",
                upstream=tuple(critiqued), capture_failure=True, read=(task_data(),), capability=turn,
            )
            output = await spoke.result()
            rebutted.append(spoke)
            if not isinstance(output, Failure) and output.position is not None:
                final_positions.append(output.position)
        if not final_positions:
            return Failure(kind=FailureKind.NO_OUTPUT, reason="no rebuttal succeeded")

        judge_handle = await self.ctx.spawn(
            JudgeAgent(
                input=JudgeInput(
                    objective=self.input.objective,
                    rubric=self.input.rubric,
                    positions=tuple(final_positions),
                    critiques=tuple(critiques),
                ),
            ).run,
            "Judge the debate and pick a winner.",
            upstream=(*critiqued, *rebutted), read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.judge_wall_clock_seconds, gpus=0),
        )
        judgment = await judge_handle.result()
        final_by_id = {item.participant_id: item for item in final_positions}
        if (
            judgment.winner_id not in final_by_id
            or judgment.winner_id not in judgment.ranking
        ):
            return Failure(
                kind=FailureKind.NO_OUTPUT, reason="judge selected no valid winner"
            )
        winner = final_by_id[judgment.winner_id]
        return SearchOutput(
            output_dir=winner.proposal_dir,
            score=float(
                    len(judgment.ranking) - judgment.ranking.index(judgment.winner_id)
                ),
        )

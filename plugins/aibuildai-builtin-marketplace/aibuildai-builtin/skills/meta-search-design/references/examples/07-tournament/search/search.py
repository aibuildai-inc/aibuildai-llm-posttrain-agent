"""Single-elimination bracket: pair, match, advance winners, repeat."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import JudgeInput, JudgeOutput
from .agents.judge import JudgeAgent
from .io import Contestant, TournamentSearchInput


def _pair_round(
    active: list[Contestant],
) -> tuple[list[tuple[Contestant, Contestant]], Contestant | None]:
    """Stable pairing; the first contestant receives the bye on an odd count."""

    bye = active[0] if len(active) % 2 == 1 else None
    remaining = active[1:] if bye is not None else active
    pairs = [
        (remaining[i], remaining[i + 1]) for i in range(0, len(remaining), 2)
    ]
    return pairs, bye


class TournamentSearch(Search[TournamentSearchInput, SearchOutput]):
    """Winners advance; a tie or an unknown winner ends the tournament."""

    async def explore(self) -> SearchOutput | Failure:
        active: list[Contestant] = list(self.input.contestants)
        producer_by_id: dict[str, Handle[JudgeOutput] | None] = {
            c.candidate_id: None for c in active
        }
        round_index = 0

        while len(active) > 1:
            round_index += 1
            pairs, bye = _pair_round(active)

            handles = []
            for match_index, (left, right) in enumerate(pairs):
                judge = JudgeAgent(
                    input=JudgeInput(
                        objective=self.input.objective,
                        rubric=self.input.rubric,
                        round_index=round_index,
                        match_index=match_index,
                        left=left,
                        right=right,
                    ),
                )
                handles.append(
                    await self.ctx.spawn(
                        judge.run,
                        "Judge this match and name the winner.",
                        upstream=tuple(
                            (
                                producer
                                for producer in (
                                    producer_by_id[left.candidate_id],
                                    producer_by_id[right.candidate_id],
                                )
                                if producer is not None
                            )
                        ), read=(task_data(),),
                        capability=ExecutionCapability(
                        wall_clock_seconds=self.input.match_wall_clock_seconds,
                        gpus=0,
                    ),
                    )
                )
            await self.ctx.wait(set(handles))

            next_round: list[Contestant] = [bye] if bye is not None else []
            for (left, right), handle in zip(pairs, handles, strict=True):
                verdict = await handle.result()
                if verdict.tied:
                    return Failure(
                        kind=FailureKind.UNEXPECTED,
                        reason=(
                            f"round {round_index} match between "
                            f"{left.candidate_id} and {right.candidate_id} tied; "
                            "no tie-break policy is declared"
                        ),
                    )
                valid_ids = {left.candidate_id, right.candidate_id}
                if verdict.winner_id not in valid_ids:
                    return Failure(
                        kind=FailureKind.UNEXPECTED,
                        reason="a match selected a contestant outside its own pairing",
                    )
                winner = left if verdict.winner_id == left.candidate_id else right
                producer_by_id[winner.candidate_id] = handle
                next_round.append(winner)

            active = next_round

        champion = active[0]
        return SearchOutput(
            output_dir=champion.output_dir,
            score=champion.seed_score,
        )

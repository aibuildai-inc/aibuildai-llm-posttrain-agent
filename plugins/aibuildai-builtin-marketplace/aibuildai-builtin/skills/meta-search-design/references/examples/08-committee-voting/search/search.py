"""Spawn every member independently, validate ballots, tally deterministically."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import VoterInput
from .agents.voter import VoterAgent
from .io import CommitteeMemberSpec, CommitteeSearchInput


def _tally(
    ballots: tuple[tuple[str, str], ...],
    weight_by_member: dict[str, float],
) -> dict[str, float]:
    """Sum each option's counted weight; the tally never sees who abstained or failed."""

    weight_by_option: dict[str, float] = {}
    for member_id, choice_id in ballots:
        weight_by_option[choice_id] = weight_by_option.get(choice_id, 0.0) + weight_by_member[member_id]
    return weight_by_option


class CommitteeVotingSearch(Search[CommitteeSearchInput, SearchOutput]):
    """Every member votes without seeing another ballot; the tally decides alone."""

    async def explore(self) -> SearchOutput | Failure:
        options = {option.option_id: option for option in self.input.options}
        members: tuple[CommitteeMemberSpec, ...] = self.input.members
        if len({member.member_id for member in members}) != len(members):
            return Failure(
                kind=FailureKind.PERMANENT,
                reason="Committee members must have distinct member_id values.",
            )

        handles = {
            member.member_id: await self.ctx.spawn(
                VoterAgent(
                    input=VoterInput(
                        objective=self.input.objective,
                        rubric=self.input.rubric,
                        options=self.input.options,
                        member=member,
                    ),
                ).run,
                "Cast your ballot.",
                capture_failure=True, read=(task_data(),),
                capability=ExecutionCapability(
                        wall_clock_seconds=self.input.member_wall_clock_seconds,
                        gpus=0,
                    ),
            )
            for member in members
        }
        await self.ctx.wait(set(handles.values()))

        ballots: list[tuple[str, str]] = []
        for member in members:
            outcome = await handles[member.member_id].result()
            if isinstance(outcome, Failure):
                continue
            if outcome.member_id != member.member_id:
                return Failure(
                    kind=FailureKind.UNEXPECTED,
                    reason="A ballot used the wrong member_id.",
                )
            if outcome.abstained:
                continue
            choice_id = outcome.choice_id
            if choice_id is None or choice_id not in options:
                return Failure(
                    kind=FailureKind.UNEXPECTED,
                    reason="A ballot selected an option outside the declared slate.",
                )
            ballots.append((member.member_id, choice_id))

        counted_ids = {member_id for member_id, _ in ballots}
        missing_required = [
            member.member_id for member in members if member.required and member.member_id not in counted_ids
        ]
        if missing_required:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=f"required member(s) did not cast a counted ballot: {missing_required}",
            )
        if len(ballots) < self.input.quorum:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=f"{len(ballots)} counted ballot(s) below quorum {self.input.quorum}",
            )

        weight_by_member = {member.member_id: member.weight for member in members}
        weight_by_option = _tally(tuple(ballots), weight_by_member)
        total_weight = sum(weight_by_member[member_id] for member_id, _ in ballots)
        winners = [
            option_id for option_id, weight in weight_by_option.items() if weight > total_weight / 2
        ]
        if len(winners) != 1:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=f"no option reached a strict majority of counted weight: {weight_by_option}",
            )

        winner = options[winners[0]]
        return SearchOutput(
            output_dir=winner.output_dir,
            score=weight_by_option[winners[0]],
        )

"""One Selector picks a speaker each turn; the speaker appends one public message."""

from __future__ import annotations

from typing import cast

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.critic import CriticAgent
from .agents.engineer import EngineerAgent
from .agents.finalizer import FinalizerAgent
from .agents.io import (
    FinalizerInput,
    ParticipantId,
    ParticipantInput,
    ParticipantOutput,
    PublicMessage,
    SelectorInput,
    SelectorOutput,
)
from .agents.researcher import ResearcherAgent
from .agents.selector import SelectorAgent
from .io import SelectorGroupChatSearchInput

ParticipantAgent = ResearcherAgent | EngineerAgent | CriticAgent

PARTICIPANT_IDS: tuple[ParticipantId, ...] = ("researcher", "engineer", "critic")
PARTICIPANT_AGENTS: dict[ParticipantId, type[ParticipantAgent]] = {
    "researcher": ResearcherAgent,
    "engineer": EngineerAgent,
    "critic": CriticAgent,
}


class SelectorGroupChatSearch(Search[SelectorGroupChatSearchInput, SearchOutput]):
    """A closed set of three specialists speak in the order one Selector picks."""

    async def _finalize(
        self,
        public_messages: tuple[PublicMessage, ...],
        brief: str,
        upstream: tuple[Handle[ParticipantOutput] | Handle[SelectorOutput], ...],
    ) -> SearchOutput:
        finalizer = FinalizerAgent(
            input=FinalizerInput(
                objective=self.input.objective,
                brief=brief,
                public_messages=public_messages,
            ),
        )
        final_handle = await self.ctx.spawn(
            finalizer.run,
            "Write the final fix plan.",
            upstream=upstream,
            read=(task_data(),),
            capability=ExecutionCapability(
                wall_clock_seconds=self.input.finalizer_wall_clock_seconds, gpus=0
            ),
        )
        final = await final_handle.result()
        return SearchOutput(
            output_dir=final.output_dir,
            score=final.score,
        )

    async def explore(self) -> SearchOutput | Failure:
        selector = SelectorAgent(
            input=SelectorInput(
                objective=self.input.objective,
                max_turns_per_participant=self.input.max_turns_per_participant,
            ),
        )
        participants: dict[ParticipantId, ParticipantAgent] = {}

        def speaker(pid: ParticipantId) -> ParticipantAgent:
            if pid not in participants:
                participants[pid] = PARTICIPANT_AGENTS[pid](
                    input=ParticipantInput(
                        objective=self.input.objective,
                        data_dir=self.input.data_dir,
                    ),
                )
            return participants[pid]

        public_messages: tuple[PublicMessage, ...] = ()
        turn_counts: dict[ParticipantId, int] = dict.fromkeys(PARTICIPANT_IDS, 0)
        # Each turn's exact contribution, not the speaker identity: a
        # participant that speaks twice contributed twice, and an identity
        # would resolve both to the same latest Action.
        spoke_last: tuple[Handle[ParticipantOutput], ...] = ()
        spoken_by: list[Handle[ParticipantOutput]] = []

        for turn_index in range(1, self.input.max_turns + 1):
            selector_handle = await self.ctx.spawn(
                selector.run,
                f"Turn {turn_index}; {self.input.max_turns - turn_index + 1} turn(s) remain. Public transcript:\n\n"
      + "\n".join(
          (
              f"{m.turn_index}. {m.participant_id} [{m.kind}]: {m.content}"
              for m in public_messages
          )
      ),
                upstream=spoke_last,
                read=(task_data(),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.selector_wall_clock_seconds,
                    gpus=0,
                ),
            )
            directive = await selector_handle.result()

            if directive.directive == "fail":
                return Failure(kind=FailureKind.NO_OUTPUT, reason=directive.reason)

            if directive.directive == "finalize":
                return await self._finalize(
                    public_messages, directive.reason, upstream=(*spoken_by, selector_handle)
                )

            requested_id = directive.participant_id
            if requested_id not in PARTICIPANT_IDS:
                return Failure(
                    kind=FailureKind.UNEXPECTED,
                    reason=f"selector chose an unknown participant: {requested_id}",
                )
            participant_id = cast(ParticipantId, requested_id)
            if turn_counts[participant_id] >= self.input.max_turns_per_participant:
                return Failure(
                    kind=FailureKind.UNEXPECTED,
                    reason=f"selector exceeded {participant_id}'s declared turn bound",
                )

            participant = speaker(participant_id)
            spoke = await self.ctx.spawn(
                participant.run, f"Turn {turn_index}. {directive.instruction}",
                upstream=(selector_handle,), read=(task_data(),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.participant_wall_clock_seconds,
                    gpus=0,
                ),
            )
            contribution = await spoke.result()
            public_messages = (
                *public_messages,
                PublicMessage(
                    turn_index=turn_index,
                    participant_id=participant_id,
                    kind=contribution.kind,
                    content=contribution.content,
                ),
            )
            turn_counts[participant_id] += 1
            spoke_last = (spoke,)
            spoken_by.append(spoke)

        return await self._finalize(
            public_messages, "turn budget exhausted", upstream=tuple(spoken_by)
        )

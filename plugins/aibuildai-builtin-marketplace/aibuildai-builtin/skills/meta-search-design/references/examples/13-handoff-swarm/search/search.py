"""Each active specialist picks the next specialist; no central Selector."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.finalizer import FinalizerAgent
from .agents.implementation import ImplementationAgent
from .agents.io import ArtifactRef, FinalizerInput, HandoffMessage, ProposedResult, SpecialistId, SpecialistInput
from .agents.research import ResearchAgent
from .agents.review import ReviewAgent
from .agents.triage import TriageAgent
from .io import HandoffSwarmSearchInput

SpecialistAgent = TriageAgent | ResearchAgent | ImplementationAgent | ReviewAgent

STARTING_ROLE: SpecialistId = "triage"
FINISH_ROLE: SpecialistId = "review"

ALLOWED_HANDOFFS: dict[SpecialistId, tuple[SpecialistId, ...]] = {
    "triage": ("research", "implementation", "review"),
    "research": ("triage", "implementation"),
    "implementation": ("review", "research"),
    "review": ("implementation",),
}

SPECIALIST_AGENTS: dict[SpecialistId, type[SpecialistAgent]] = {
    "triage": TriageAgent,
    "research": ResearchAgent,
    "implementation": ImplementationAgent,
    "review": ReviewAgent,
}


class HandoffSwarmSearch(Search[HandoffSwarmSearchInput, SearchOutput]):
    """Four peers hand off control to each other until one finishes or fails."""

    async def _finalize(
        self,
        candidate: ProposedResult,
        public_messages: tuple[HandoffMessage, ...],
        artifacts: tuple[ArtifactRef, ...],
        wrote: tuple[Handle, ...],
        upstream: tuple[Handle, ...],
    ) -> SearchOutput:
        finalizer = FinalizerAgent(
            input=FinalizerInput(
                objective=self.input.objective,
                public_messages=public_messages,
                artifacts=artifacts,
                candidate=candidate,
            ),
        )
        final_handle = await self.ctx.spawn(
            finalizer.run,
            "Write the final result.",
            upstream=upstream,
            # Its prompt names the candidate directory and every artifact the
            # swarm left, and its job is to read them. It waits only for the
            # last turn, and waiting exposes no file, so every peer that
            # wrote something is granted here by name.
            read=(task_data(), *(handle.files() for handle in wrote)),
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
        peers: dict[SpecialistId, SpecialistAgent] = {}

        def peer(role: SpecialistId) -> SpecialistAgent:
            if role not in peers:
                peers[role] = SPECIALIST_AGENTS[role](
                    input=SpecialistInput(
                        objective=self.input.objective,
                        data_dir=self.input.data_dir,
                        allowed_handoffs=ALLOWED_HANDOFFS[role],
                    ),
                )
            return peers[role]

        active_role: SpecialistId = STARTING_ROLE
        public_messages: tuple[HandoffMessage, ...] = ()
        artifacts: tuple[ArtifactRef, ...] = ()
        # The turns that left files behind. Control moves on every turn; the
        # files stay where the peer that wrote them put them.
        wrote: tuple[Handle, ...] = ()
        role_visits: dict[SpecialistId, int] = dict.fromkeys(ALLOWED_HANDOFFS, 0)
        active_by: tuple[Handle, ...] = ()

        for turn_index in range(1, self.input.max_turns + 1):
            role_visits[active_role] += 1
            if role_visits[active_role] > self.input.max_visits_per_role:
                return Failure(
                    kind=FailureKind.UNEXPECTED,
                    reason=f"{active_role} exceeded its declared visit bound of {self.input.max_visits_per_role}",
                )

            specialist = peer(active_role)
            outcome_handle = await self.ctx.spawn(
                specialist.run, f"Turn {turn_index}; {self.input.max_turns - turn_index + 1} "
                    "turn(s) remain. Public conversation:\n"
                    + "\n".join(
                        f"{m.turn_index}. {m.role}: {m.content}"
                        for m in public_messages
                    ),
                upstream=active_by, read=(task_data(),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.specialist_wall_clock_seconds,
                    gpus=0,
                ),
            )
            outcome = await outcome_handle.result()

            public_messages = (
                *public_messages,
                HandoffMessage(turn_index=turn_index, role=active_role, content=outcome.content),
            )
            artifacts = (*artifacts, *outcome.artifacts)
            active_by = (outcome_handle,)
            if outcome.artifacts or outcome.proposed_result is not None:
                wrote = (*wrote, outcome_handle)

            decision = outcome.decision
            if decision.action == "fail":
                return Failure(kind=FailureKind.NO_OUTPUT, reason=decision.reason)

            if decision.action == "finish":
                if active_role != FINISH_ROLE:
                    return Failure(
                        kind=FailureKind.UNEXPECTED,
                        reason=f"{active_role} requested finish; only {FINISH_ROLE} may finish",
                    )
                if outcome.proposed_result is None:
                    return Failure(
                        kind=FailureKind.NO_OUTPUT,
                        reason=f"{active_role} requested finish without a candidate result",
                    )
                return await self._finalize(
                    outcome.proposed_result,
                    public_messages,
                    artifacts,
                    wrote,
                    upstream=active_by,
                )

            next_role = decision.next_role
            if next_role is None or next_role not in ALLOWED_HANDOFFS[active_role]:
                return Failure(
                    kind=FailureKind.UNEXPECTED,
                    reason=f"{active_role} named an illegal handoff target: {next_role}",
                )
            active_role = next_role

        return Failure(
            kind=FailureKind.NO_OUTPUT,
            reason="handoff swarm exhausted its maximum number of turns",
        )

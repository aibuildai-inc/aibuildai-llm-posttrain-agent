"""DesignerAgent — the role that proposes each chain's starting design.

The Designer produces ``DesignerOutput`` (a list of fresh DesignPlans). Every
later link of a chain is proposed by the Worker that just ran, not by a
separate role.
"""

from __future__ import annotations

from typing import Any, ClassVar

from engine.durable_execution import ActionInvocation
from engine.execution_output import VerifierOutput
from engine.work_unit.agent.base import Agent, AgentInput, review_request
from engine.work_unit.agent.policy import (
    ALL_BUILTIN_TOOLS,
    JUDGE_TOOLS_WITH_SCRATCH,
    RolePolicy,
)
from engine.builtin.parallel.agents.designer.io import (
    DesignerInput,
    DesignerOutput,
)

# The DESIGNER's sandbox writes only its own scratch: its designs travel as the
# typed terminal DesignerOutput (journaled), so the agent itself needs no
# run-dir write at all.
DESIGNER_POLICY = RolePolicy(
    tools=tuple(ALL_BUILTIN_TOOLS),
)


class DesignerAgent(Agent[DesignerInput, DesignerOutput]):
    name: ClassVar[str] = "designer"
    prompt_template = "agent/designer.j2"
    policy = DESIGNER_POLICY

    @classmethod
    def semantic_reviewer(cls) -> "type[Agent[Any, VerifierOutput]] | None":
        return DesignerVerifierAgent

    async def verifier_invocations(
        self, candidate: DesignerOutput
    ) -> tuple[ActionInvocation[Any, VerifierOutput], ...]:
        """The reviewer that reads the plans, when the operator asked for one.

        Whether a plan set fits the run at all is settled by ``DesignerOutput``
        before anything is spawned, so what is left here is the judgement no
        schema can make: whether the plans are worth building."""
        reviewer = type(self).semantic_reviewer()
        if reviewer is None or self.reviewer_capability is None:
            return ()
        return (
            (
                reviewer(
                    input=AgentInput(),
                ).run,
                self.review_capability,
                review_request(candidate),
            ),
        )


# The DESIGNER review checks one set of plans before coding. The submitted
# Output is part of its Input, and the Designer's execution record lives in the
# run workspace tree (RUN_WORKSPACE), which also opens the ancestor designs'
# outputs a plan's lineage claims are judged against. Its central mandate —
# fail an invented source — is what the citation-verification tools are for,
# so it carries them. Its verdict
# reason is shown verbatim to the Designer, a design role, so TASK_FOLDER,
# and PRIVATE stay unbound. The Run database is also outside the
# sandbox, so SETUP's answer-laden history stays hidden. Write/Edit
# reach only SCRATCH, so no plan
# file can be edited into agreement with its verdict.
DESIGNER_REVIEW_POLICY = RolePolicy(
    tools=(
        *JUDGE_TOOLS_WITH_SCRATCH,
        "BashOutput",
        "KillBash",
        "StructuredOutput",
    ),
)


class DesignerVerifierAgent(Agent[AgentInput, VerifierOutput]):
    """The Designer's semantic verifier, included when the operator asks for it."""

    policy = DESIGNER_REVIEW_POLICY
    name: ClassVar[str] = "designer_review"
    prompt_template = "agent/designer_review.j2"


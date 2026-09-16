"""The Agent that implements and tunes one design."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from engine.durable_execution import ActionInvocation
from engine.execution_output import VerifierOutput
from engine.work_unit.agent.base import Agent, AgentInput, review_request
from engine.work_unit.agent.policy import (
    ALL_BUILTIN_TOOLS,
    JUDGE_TOOLS_WITH_SCRATCH,
    RolePolicy,
    SystemDir,
)
from engine.builtin.tree.agents.coder.io import CoderInput, CoderOutput

if TYPE_CHECKING:
    pass


CODER_POLICY = RolePolicy(
    tools=tuple(ALL_BUILTIN_TOOLS),
    blocks_long_sleep=True,
    task_environment=True,
    system_read=(SystemDir.CONDA_PACKAGES,),
)


def coder_source_dir(coder: "CoderAgent") -> str:
    """The source tree one Coder run owns, under its own artifacts.

    The framework prepares it, launches the session in it, and hands it to
    Training, so it is the framework's fact from end to end. Nobody asks the
    model to repeat its own cwd back, and nobody has to verify the echo."""
    return f"{coder.artifacts_dir}/source"


class CoderAgent(Agent[CoderInput, CoderOutput]):
    max_turns = 150
    name: ClassVar[str] = "coder"
    prompt_template = "agent/coder.j2"
    policy = CODER_POLICY

    def launch_directory(self) -> str:
        """The source tree this Coder owns; its own output gate demands it."""
        return coder_source_dir(self)

    @classmethod
    def semantic_reviewer(cls) -> "type[Agent[Any, VerifierOutput]] | None":
        return CoderVerifierAgent

    async def verifier_invocations(
        self, candidate: CoderOutput
    ) -> tuple[ActionInvocation[Any, VerifierOutput], ...]:
        reviewer = type(self).semantic_reviewer()
        if reviewer is None or self.reviewer_capability is None:
            return ()
        return (
            (
                reviewer(
                    input=AgentInput(),
                    # A reviewer with its own role budget, and its own cards:
                    # it re-runs inference in its own session, so it asks for
                    # the count the reviewed invocation was given.
                ).run,
                self.review_capability,
                review_request(candidate),
            ),
        )

    async def _prepare_conversation(self) -> None:
        """Prepare this identity's source once, before its first Action."""
        if self.ctx.ordinal > 1:
            return
        from infra.fs.artifact_dir import prepare_coder_workspace

        await self.ctx.step(
            prepare_coder_workspace,
            coder_source_dir(self),
            self.artifacts_dir,
            self.input.inherited_source_dir,
        )


# The CODER review audits one Coder submission in its design dir: RUN_WORKSPACE
# grants read over the run workspace tree, which covers the reviewed design, its
# parent (the copied-parent-output check), the execution.md trace, and every
# unit's trained attempt output.
# CONDA_ROOT/CONDA_PACKAGES let it re-run inference in-session on the run's
# visible GPU set — the capability the retired shared-policy comment claimed
# without granting the environment for it. The
# citation-verification tools serve its false-metrics and
# can-this-library-do-that checks. Its
# verdict reason is shown verbatim to the Coder, a design role, so
# TASK_FOLDER and PRIVATE stay unbound. The Run database is also
# outside the sandbox, so SETUP's answer-laden history stays hidden.
# Write/Edit reach only SCRATCH,
# so the reviewed worktree stays immutable to it.
CODER_REVIEW_POLICY = RolePolicy(
    tools=(
        *JUDGE_TOOLS_WITH_SCRATCH,
        "BashOutput",
        "KillBash",
        "StructuredOutput",
    ),
    system_read=(SystemDir.CONDA_ROOT, SystemDir.CONDA_PACKAGES),
    task_environment=True,
)


class CoderVerifierAgent(Agent[AgentInput, VerifierOutput]):
    """The Coder's semantic reviewer, included when the operator asks for it.

    It carries no business Input: what it judges is one submission, which is
    the request of the review Action itself, and everything else it reads is
    at a runtime path its launch already names."""

    policy = CODER_REVIEW_POLICY
    name: ClassVar[str] = "coder_review"
    prompt_template = "agent/coder_review.j2"


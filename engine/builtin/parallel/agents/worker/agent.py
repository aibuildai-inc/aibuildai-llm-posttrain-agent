"""WorkerAgent -- the complete execution role for one link of a parallel chain."""

from __future__ import annotations

from typing import Any, ClassVar

from engine.builtin.parallel.agents.worker.io import WorkerInput, WorkerOutput
from engine.capability import ExecutionCapability
from engine.durable_execution import ActionInvocation
from engine.execution_output import VerifierOutput
from engine.work_unit.agent.base import Agent, AgentInput, review_request
from engine.work_unit.agent.policy import (
    ALL_BUILTIN_TOOLS,
    JUDGE_TOOLS_WITH_SCRATCH,
    RolePolicy,
    SystemDir,
)
from engine.work_unit.program.attempt_verifier import (
    AttemptVerifierInput,
    AttemptVerifierProgram,
)

WORKER_POLICY = RolePolicy(
    tools=tuple(ALL_BUILTIN_TOOLS),
    blocks_long_sleep=True,
    task_environment=True,
)


class WorkerAgent(Agent[WorkerInput, WorkerOutput]):
    """Writes the code, runs it, reads the errors, fixes them, and exports the result."""

    max_turns = 150
    name: ClassVar[str] = "worker"
    prompt_template = "agent/worker.j2"
    policy = WORKER_POLICY

    async def _prepare_conversation(self) -> None:
        """Create this Worker run's own attempt directory before the session."""
        from infra.fs.artifact_dir import prepare_dir

        await self.ctx.step(prepare_dir, f"{self.artifacts_dir}/full")

    @classmethod
    def semantic_reviewer(cls) -> "type[Agent[Any, VerifierOutput]] | None":
        return WorkerVerifierAgent

    async def verifier_invocations(
        self, candidate: WorkerOutput
    ) -> tuple[ActionInvocation[Any, VerifierOutput], ...]:
        # The attempt directory is checked first, because that is mechanical;
        # the reviewer that has to read the code and the run to have an
        # opinion sees only a submission that produced something at all.
        chain: list[ActionInvocation[Any, VerifierOutput]] = [
            (
                AttemptVerifierProgram(
                    input=AttemptVerifierInput(
                        attempt_dir=f"{self.artifacts_dir}/full",
                    ),
                ).run,
                ExecutionCapability(
                    wall_clock_seconds=self._local_wall_clock, gpus=0
                ),
            )
        ]
        reviewer = type(self).semantic_reviewer()
        if reviewer is not None and self.reviewer_capability is not None:
            chain.append(
                (
                    reviewer(
                        input=AgentInput(),
                        # A reviewer with its own role budget, and its own
                        # cards: it re-runs inference in its own session, so it
                        # asks for the count the reviewed invocation was given.
                    ).run,
                    self.review_capability,
                    review_request(candidate),
                )
            )
        return tuple(chain)


# The WORKER review audits one complete attempt in its own directory:
# RUN_WORKSPACE grants read over the run workspace tree, which covers the
# reviewed attempt, its parent, the execution trace, and the output that was
# scored. CONDA_ROOT/CONDA_PACKAGES let it re-run inference in-session on the
# run's visible GPU set. Its verdict reason is shown verbatim to the Worker, a
# design role, so TASK_FOLDER and PRIVATE stay unbound; the Run database is
# also outside the sandbox, so SETUP's answer-laden history stays hidden
#. Write/Edit reach only SCRATCH, so the reviewed source stays
# immutable to it.
WORKER_REVIEW_POLICY = RolePolicy(
    tools=(
        *JUDGE_TOOLS_WITH_SCRATCH,
        "BashOutput",
        "KillBash",
        "StructuredOutput",
    ),
    system_read=(SystemDir.CONDA_ROOT, SystemDir.CONDA_PACKAGES),
    task_environment=True,
)


class WorkerVerifierAgent(Agent[AgentInput, VerifierOutput]):
    """The Worker's semantic reviewer, included when the operator asks for it.

    It carries no business Input: what it judges is one submission, which is
    the request of the review Action itself, and everything else it reads is
    at a runtime path its launch already names."""

    policy = WORKER_REVIEW_POLICY
    name: ClassVar[str] = "worker_review"
    prompt_template = "agent/worker_review.j2"

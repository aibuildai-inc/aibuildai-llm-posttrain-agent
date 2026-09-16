"""AggregatorAgent — builds one more design by ensembling the graded ones.

Run-level Agent owned by the ensemble design. It writes its ensemble into its own run's attempt dir under its artifacts, and the run then scores that dir with the task folder's own score program, exactly as it scores every other design. The ensemble therefore competes for the run's winner on its measured score: it wins when it is actually better, and loses when it is not.
"""

from __future__ import annotations

from typing import Any, ClassVar


from engine.execution_output import VerifierOutput
from engine.capability import ExecutionCapability
from engine.durable_execution import ActionInvocation
from engine.work_unit.agent.base import Agent
from engine.work_unit.agent.policy import ALL_BUILTIN_TOOLS, RolePolicy
from engine.work_unit.program.attempt_verifier import (
    AttemptVerifierInput,
    AttemptVerifierProgram,
)
from engine.builtin.parallel.agents.aggregator.io import AggregatorInput, AggregatorOutput

AGGREGATOR_POLICY = RolePolicy(
    tools=tuple(ALL_BUILTIN_TOOLS),
    task_environment=True,
)


class AggregatorAgent(Agent[AggregatorInput, AggregatorOutput]):
    name: ClassVar[str] = "aggregator"
    prompt_template = "agent/aggregator.j2"
    policy = AGGREGATOR_POLICY

    async def verifier_invocations(
        self, candidate: AggregatorOutput
    ) -> tuple[ActionInvocation[Any, VerifierOutput], ...]:
        # The ensemble owes an attempt directory that is not empty, and no
        # particular shape: what a good ensemble output looks like is the score
        # program's business, so a malformed one loses on its measured score
        # instead of being sent back. That answer is on disk, not in the
        # candidate.
        del candidate
        return (
            (
                AttemptVerifierProgram(
                    input=AttemptVerifierInput(
                        attempt_dir=f"{self.artifacts_dir}/full",
                    ),
                ).run,
                ExecutionCapability(
                    wall_clock_seconds=self._local_wall_clock,
                    gpus=0
                ),
            ),
        )

    async def _prepare_conversation(self) -> None:
        """Create this Aggregator run's own attempt directory before the session."""
        from infra.fs.artifact_dir import prepare_dir

        await self.ctx.step(prepare_dir, f"{self.artifacts_dir}/full")

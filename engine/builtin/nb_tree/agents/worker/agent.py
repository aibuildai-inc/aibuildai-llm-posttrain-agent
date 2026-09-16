"""WorkerAgent -- the complete execution role for one nb_tree design."""

from __future__ import annotations

from typing import Any, ClassVar

from engine.execution_output import VerifierOutput
from engine.capability import ExecutionCapability
from engine.durable_execution import ActionInvocation
from engine.work_unit.agent.base import Agent
from engine.work_unit.agent.policy import ALL_BUILTIN_TOOLS, RolePolicy
from engine.builtin.nb_tree.agents.worker.io import WorkerInput, WorkerOutput
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
    max_turns = 150
    name: ClassVar[str] = "worker"
    prompt_template = "agent/worker.j2"
    policy = WORKER_POLICY

    async def _prepare_conversation(self) -> None:
        """Create this Worker run's own attempt directory before the session."""
        from infra.fs.artifact_dir import prepare_dir

        await self.ctx.step(prepare_dir, f"{self.artifacts_dir}/full")

    async def verifier_invocations(
        self, candidate: WorkerOutput
    ) -> tuple[ActionInvocation[Any, VerifierOutput], ...]:
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

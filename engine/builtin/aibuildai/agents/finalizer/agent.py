"""FinalizerAgent — the run's tail role: hand the result back to the user.

Every search has the same head and tail. Setup establishes what the run means and how it is scored; the finalizer delivers the result. In between, each search method runs whatever exploration it likes.

The framework has already selected the result by the journaled metric contract's direction and written its own record of that choice, so this role never re-selects. It reads the selected result's output and the run's README (its system prompt carries the frozen README, exactly like every other role) and writes the deliverable in the shape that README asks for, into the run's deliverable dir.

Delivery happens once per run and only after the real result is known, so it is live agent work rather than a frozen program: the finalizer sees the actual output it must convert, and never has to guess it in advance.

``FinalizerVerifierProgram`` is the gate: an empty deliverable dir means the run handed the user nothing, so it is rejected in-session and the same finalizer fixes it.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any, ClassVar

from pydantic import ConfigDict

from engine.capability import ExecutionCapability
from engine.durable_execution import ActionInvocation, action
from engine.execution_output import VerifierOutput
from engine.failure import Failure
from engine.work_unit.agent.base import Agent
from engine.builtin.aibuildai.agents.finalizer.io import FinalizerInput, FinalizerOutput
from engine.work_unit.agent.policy import ALL_BUILTIN_TOOLS, RolePolicy
from engine.work_unit.program.base import Program


@dataclass(frozen=True)
class FinalizerVerifierInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    deliverable_dir: str


class FinalizerVerifierProgram(Program[FinalizerVerifierInput]):
    """The deliverable floor, asked entirely of the files the finalizer DECLARES: every name must be a real, non-empty file in the deliverable dir. That at least one is named, and that none of them climbs out of the directory, needs no disk and is settled by ``DeliveryRecord`` before this runs.

    The declared set is the whole subject on purpose. An earlier form asked separately whether any non-empty file sat in the dir, which a stray note or a leftover log answered just as well as a real result -- so a zero-byte submission.csv passed beside it. Nothing incidental can stand in for the delivery when the delivery is the only thing being looked at."""

    name: ClassVar[str] = "finalizer_verifier"

    @action
    def run(self, candidate: FinalizerOutput) -> VerifierOutput | Failure:
        deliverable = Path(self.input.deliverable_dir)
        declared = tuple(candidate.delivery.relative_paths)
        missing = [name for name in declared if not (deliverable / name).is_file()]
        if missing:
            return VerifierOutput(
                passed=False,
                reason=(
                    f"delivery.relative_paths names files that do not exist under "
                    f"{deliverable}: {missing}. List the real files you wrote "
                    f"there, relative to that directory. Write the missing files "
                    "or correct delivery.relative_paths and call StructuredOutput "
                    "again."
                ),
            )
        empty = [name for name in declared if (deliverable / name).stat().st_size == 0]
        if empty:
            return VerifierOutput(
                passed=False,
                reason=(
                    f"delivery.relative_paths names files that are empty: {empty}. "
                    "An empty file hands the user nothing. Read the selected "
                    "result's own output, write the real result the run's README "
                    "asks for, then submit again."
                ),
            )
        return VerifierOutput(passed=True, reason="")


# The FINALIZER reads the winner's output where its producers wrote it -- the
# run workspace tree (each unit's actions/action_*/attempt_*/artifacts) -- and writes the
# deliverable; the run root itself holds only framework state it has no
# business reading.
FINALIZER_POLICY = RolePolicy(
    tools=tuple(ALL_BUILTIN_TOOLS),
    task_environment=True,
)


class FinalizerAgent(Agent[FinalizerInput, FinalizerOutput]):
    name: ClassVar[str] = "finalizer"
    prompt_template = "agent/finalizer.j2"
    policy = FINALIZER_POLICY

    async def verifier_invocations(
        self, candidate: FinalizerOutput
    ) -> tuple[ActionInvocation[Any, VerifierOutput], ...]:
        """The deliverable floor, built here and nowhere else.

        The delivery recheck a resumed run performs asks this same method for
        the same invocation, so there is one place that knows what checking a
        delivery means."""
        return (
            (
                FinalizerVerifierProgram(
                    input=FinalizerVerifierInput(
                        deliverable_dir=self.input.deliverable_dir,
                    ),
                ).run,
                ExecutionCapability(
                    wall_clock_seconds=self._local_wall_clock,
                    gpus=0
                ),
                candidate,
            ),
        )

"""Two stages in a fixed order; stage B's Input is adapted from stage A's Output."""

from __future__ import annotations

from engine.failure import Failure
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.implementer import ImplementerAgent
from .agents.io import ImplementerInput, PlannerInput
from .agents.planner import PlannerAgent
from .io import SequentialChainSearchInput


class SequentialChainSearch(Search[SequentialChainSearchInput, SearchOutput]):
    """Plan, then implement; no stage revisits an earlier one."""

    async def explore(self) -> SearchOutput | Failure:
        planner = PlannerAgent(
            input=PlannerInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
            ),
        )
        planner_handle = await self.ctx.spawn(
            planner.run, "Start the assigned work.", read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.planner_wall_clock_seconds, gpus=0),
        )
        plan = await planner_handle.result()

        implementer = ImplementerAgent(
            input=ImplementerInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                plan=plan.plan,
                expected_pitfalls=plan.expected_pitfalls,
            ),
        )
        implementer_handle = await self.ctx.spawn(
            implementer.run,
            "Start the assigned work.",
            upstream=(planner_handle,),
            read=(task_data(),),
            capability=ExecutionCapability(
                wall_clock_seconds=self.input.implementer_wall_clock_seconds,
                gpus=0,
            ),
        )
        result = await implementer_handle.result()

        return SearchOutput(
            output_dir=result.output_dir,
            score=result.score,
        )

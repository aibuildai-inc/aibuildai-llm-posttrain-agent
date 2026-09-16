"""Classify once, then run exactly the routes the classification selected."""

from __future__ import annotations

from engine.durable_execution import Handle
from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search
from engine.work_unit.agent import Agent

from .agents.code_repair import CodeRepairAgent
from .agents.data_analysis import DataAnalysisAgent
from .agents.io import RouteLabel, RouterInput, SpecialistInput, SpecialistOutput, SynthesisInput
from .agents.literature_review import LiteratureReviewAgent
from .agents.router import RouterAgent
from .agents.synthesis import SynthesisAgent
from .io import RouterSpecialistsSearchInput

SPECIALIST_AGENTS: dict[RouteLabel, type[Agent[SpecialistInput, SpecialistOutput]]] = {
    "data_analysis": DataAnalysisAgent,
    "literature_review": LiteratureReviewAgent,
    "code_repair": CodeRepairAgent,
}


class RouterSpecialistsSearch(Search[RouterSpecialistsSearchInput, SearchOutput]):
    """One router decision selects one specialist or joins several with a synthesizer."""

    async def explore(self) -> SearchOutput | Failure:
        router = RouterAgent(
            input=RouterInput(objective=self.input.objective, data_dir=self.input.data_dir),
        )
        router_handle = await self.ctx.spawn(
            router.run, "Classify the objective.", read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.router_wall_clock_seconds, gpus=0),
        )
        decision = await router_handle.result()

        if len(decision.routes) == 1:
            route = decision.routes[0]
            output_handle = await self.ctx.spawn(
                self._specialist(route).run, "Start the assigned work.", upstream=(router_handle,),
                read=(task_data(),), capability=self._specialist_capability(),
            )
            output = await output_handle.result()
            return self._single_result(output)

        handles = [
            await self.ctx.spawn(
                self._specialist(route).run, "Start the assigned work.", upstream=(router_handle,),
                capture_failure=True, read=(task_data(),), capability=self._specialist_capability(),
            )
            for route in decision.routes
        ]
        await self.ctx.wait(handles)

        successes: list[tuple[RouteLabel, SpecialistOutput]] = []
        consumed: list[Handle[SpecialistOutput | Failure]] = []
        for route, handle in zip(decision.routes, handles, strict=True):
            outcome = await handle.result()
            if not isinstance(outcome, Failure):
                successes.append((route, outcome))
                consumed.append(handle)

        if len(successes) < self.input.min_successful_routes:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=(
                    f"{len(successes)} of {len(handles)} selected routes succeeded; "
                    f"at least {self.input.min_successful_routes} required"
                ),
            )
        if len(successes) == 1:
            output = successes[0][1]
            return self._single_result(output)

        synthesis = SynthesisAgent(
            input=SynthesisInput(
                objective=self.input.objective,
                reports=tuple((route, output.summary) for route, output in successes),
            ),
        )
        synthesis_handle = await self.ctx.spawn(
            synthesis.run,
            "Synthesize the successful reports.",
            upstream=tuple(consumed),
            read=(task_data(),),
            capability=ExecutionCapability(
                wall_clock_seconds=self.input.synthesis_wall_clock_seconds, gpus=0
            ),
        )
        combined = await synthesis_handle.result()
        return SearchOutput(
            output_dir=combined.output_dir,
            score=combined.score,
        )

    def _specialist(
        self, route: RouteLabel
    ) -> Agent[SpecialistInput, SpecialistOutput]:
        return SPECIALIST_AGENTS[route](
            input=SpecialistInput(objective=self.input.objective, data_dir=self.input.data_dir),
        )

    def _specialist_capability(self) -> ExecutionCapability:
        """What one specialist call may spend."""
        return ExecutionCapability(
            wall_clock_seconds=self.input.specialist_wall_clock_seconds, gpus=0
        )

    def _single_result(self, output: SpecialistOutput) -> SearchOutput:
        return SearchOutput(
            output_dir=output.output_dir,
            score=output.score,
        )

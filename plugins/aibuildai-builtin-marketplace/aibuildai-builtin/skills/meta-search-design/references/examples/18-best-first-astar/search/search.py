"""Pop the cheapest-so-far frontier state, expand it, bound by accumulated compute."""

from __future__ import annotations

import heapq

from pydantic import BaseModel, ConfigDict

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.expander import ExpanderAgent
from .agents.heuristic import HeuristicAgent
from .agents.io import Candidate, ExpanderInput, HeuristicInput
from .io import BestFirstSearchInput
from .programs.trial import TrialInput, TrialProgram


class FrontierEntry(BaseModel):
    """One immutable pipeline state; g and h are both compute seconds."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    state_id: str
    depth: int
    config_json: str
    path_cost: float
    heuristic_cost: float
    priority: float


def _reached_target(metric: float, target: float, lower_is_better: bool) -> bool:
    return metric <= target if lower_is_better else metric >= target


class HeuristicBestFirstSearch(Search[BestFirstSearchInput, SearchOutput]):
    """The frontier priority is compute already spent plus compute predicted remaining."""

    async def explore(self) -> SearchOutput | Failure:
        root = FrontierEntry(
            state_id="0", depth=0, config_json=self.input.initial_config_json,
            path_cost=0.0, heuristic_cost=0.0, priority=0.0,
        )
        frontier: list[tuple[float, str, FrontierEntry]] = [(root.priority, root.state_id, root)]
        producer_by_state: dict[str, tuple[Handle, ...]] = {root.state_id: ()}
        incumbent: FrontierEntry | None = None
        incumbent_output_dir = ""
        incumbent_metric = 0.0
        expansions = 0
        next_index = 0

        while frontier and expansions < self.input.max_expansions:
            _, _, current = heapq.heappop(frontier)
            if incumbent is not None and current.path_cost >= incumbent.path_cost:
                continue

            expander = ExpanderAgent(
                input=ExpanderInput(
                    objective=self.input.objective,
                    data_dir=self.input.data_dir,
                    source_dir=self.input.source_dir,
                    parent_config_json=current.config_json,
                    branching_factor=self.input.branching_factor,
                    remaining_expansions=self.input.max_expansions - expansions,
                ),
            )
            expander_handle = await self.ctx.spawn(
                expander.run,
                "Expand this state.",
                upstream=producer_by_state[current.state_id],
                capture_failure=True, read=(task_data(),),
                capability=ExecutionCapability(
                wall_clock_seconds=self.input.expander_wall_clock_seconds, gpus=0
                ),
            )
            proposal = await expander_handle.result()
            expansions += 1
            if isinstance(proposal, Failure):
                continue

            children: tuple[Candidate, ...] = proposal.children[: self.input.branching_factor]
            if not children:
                continue

            trial_handles = [
                await self.ctx.spawn(
                    TrialProgram(
                        input=TrialInput(
                            config_json=child.config_json,
                            source_dir=self.input.source_dir,
                            python_path=self.input.python_path,
                            data_dir=self.input.data_dir,
                            metric_name=self.input.metric_name,
                        ),
                    ).run,
                    upstream=(expander_handle,),
                    capture_failure=True, read=(task_data(),),
                    capability=ExecutionCapability(
                    wall_clock_seconds=self.input.trial_wall_clock_seconds, gpus=1
                        ),
                )
                for child in children
            ]
            await self.ctx.wait(trial_handles)

            pending: list[tuple[str, Candidate, float, float]] = []
            for child, handle in zip(children, trial_handles, strict=True):
                outcome = await handle.result()
                if isinstance(outcome, Failure):
                    continue
                next_index += 1
                state_id = f"{current.state_id}.{next_index}"
                path_cost = current.path_cost + outcome.compute_seconds
                if incumbent is not None and path_cost >= incumbent.path_cost:
                    continue
                if _reached_target(outcome.metric, self.input.target_metric, self.input.metric_lower_is_better):
                    completed = FrontierEntry(
                        state_id=state_id, depth=current.depth + 1, config_json=child.config_json,
                        path_cost=path_cost, heuristic_cost=0.0, priority=path_cost,
                    )
                    if incumbent is None or completed.path_cost < incumbent.path_cost:
                        incumbent = completed
                        incumbent_output_dir = outcome.output_dir
                        incumbent_metric = outcome.metric
                    continue
                producer_by_state[state_id] = (expander_handle,)
                pending.append((state_id, child, path_cost, outcome.metric))

            if not pending:
                continue

            heuristics = {
                state_id: HeuristicAgent(
                    input=HeuristicInput(
                        objective=self.input.objective,
                        metric_name=self.input.metric_name,
                        target_metric=self.input.target_metric,
                        metric_lower_is_better=self.input.metric_lower_is_better,
                        config_json=child.config_json,
                        observed_metric=metric,
                        compute_seconds_spent=path_cost,
                    ),
                )
                for state_id, child, path_cost, metric in pending
            }
            heuristic_handles = {
                state_id: await self.ctx.spawn(
                    heuristics[state_id].run,
                    "Estimate the remaining compute to target.",
                    upstream=(expander_handle,),
                    capture_failure=True, read=(task_data(),),
                    capability=ExecutionCapability(
                        wall_clock_seconds=self.input.heuristic_wall_clock_seconds,
                        gpus=0,
                    ),
                )
                for state_id in heuristics
            }
            await self.ctx.wait(set(heuristic_handles.values()))

            for state_id, child, path_cost, _metric in pending:
                judged = await heuristic_handles[state_id].result()
                if isinstance(judged, Failure):
                    continue
                if not judged.viable:
                    continue
                entry = FrontierEntry(
                    state_id=state_id, depth=current.depth + 1, config_json=child.config_json,
                    path_cost=path_cost, heuristic_cost=judged.remaining_compute_seconds,
                    priority=path_cost + judged.remaining_compute_seconds,
                )
                heapq.heappush(frontier, (entry.priority, entry.state_id, entry))

            if len(frontier) > self.input.max_frontier_size:
                frontier = heapq.nsmallest(self.input.max_frontier_size, frontier)
                heapq.heapify(frontier)

        if incumbent is None:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=(
                    f"exhausted {expansions} expansions without a pipeline reaching "
                    f"{self.input.metric_name} target {self.input.target_metric}"
                ),
            )

        return SearchOutput(
            output_dir=incumbent_output_dir,
            score=incumbent.path_cost,
            components=((self.input.metric_name, incumbent_metric),),
        )

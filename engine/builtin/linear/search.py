"""LinearSearch: the width-1 serial chain of complete Workers.

One explicit Designer starts the chain. Each link is one complete Worker: one
session that writes the code, runs it, reads its own errors, fixes them, and
exports the result, scored by the product Score Program once that session
ends. A finished Worker proposes the direction for the next link, so the chain
is a plain loop -- no Coder, no Training Program, no Reviser, and no queue,
pending map, or slot filling for a width of one. The Aggregator then ensembles
every link the chain actually scored.

This package has no Judge, no Selector, and no Router: a config that asks for
model routing under ``search.kind=linear`` is refused at startup by the shared
``AIBuildAISearch.prepare_router`` default, which this package does not
override."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from engine.builtin.aibuildai.io import SearchOutput, task_data
from engine.builtin.aibuildai.search import SEARCH_TEMPLATE, AIBuildAISearch
from engine.builtin.linear.agents.aggregator.agent import AggregatorAgent
from engine.builtin.linear.agents.designer.agent import DesignerAgent
from engine.builtin.linear.agents.designer.io import DesignerInput
from engine.builtin.linear.agents.worker.agent import WorkerAgent
from engine.builtin.linear.agents.worker.io import ParentAttempt
from engine.builtin.linear.candidate import Ensemble, WorkerCandidate
from engine.builtin.linear.io import (
    EnsembleInput,
    LinearSearchInput,
    LinearSearchParameters,
    WorkerCandidateInput,
    WorkerCandidateOutput,
    build_input,
)
from engine.durable_execution import run_host
from engine.durable_execution import Handle
from engine.execution_output import ExecutionOutput
from engine.failure import Failure, FailureKind
from engine.metric_contract import MetricContractInput
from engine.paths import RunPaths

if TYPE_CHECKING:
    from config import AgentConfig

_AnyHandle = Handle[ExecutionOutput[bool]]
_CandidateHandle = Handle[WorkerCandidateOutput | Failure]


@dataclass
class _Started:
    """One link this Search launched, and what it produced."""

    composite: WorkerCandidate
    handle: _CandidateHandle
    output: "WorkerCandidateOutput | Failure | None" = None


class LinearSearch(AIBuildAISearch[LinearSearchInput]):
    description = (
        "One explicit Designer -> serial chain of complete Workers -> "
        "Aggregator ensemble flow."
    )
    __doc__ = description

    prompt_template = SEARCH_TEMPLATE
    agent_types = (DesignerAgent, WorkerAgent, AggregatorAgent)
    parameters_type = LinearSearchParameters

    @classmethod
    def build_input(
        cls, configs: "AgentConfig", run_paths: RunPaths
    ) -> LinearSearchInput:
        """Validate the user's ``search.input`` and complete this package's Input."""
        return build_input(cls.parameters(configs), configs, run_paths)

    # --- What this Search asks its own children -----------------------------
    async def _link(
        self, candidate: WorkerCandidate, upstream: "tuple[_AnyHandle, ...]"
    ) -> _Started:
        """Launch one link and record it as this Search's own."""
        handle = cast(
            _CandidateHandle,
            await self.ctx.spawn(candidate.run, upstream=upstream, capture_failure=True),
        )
        started = _Started(composite=candidate, handle=handle)
        self._by_uid[candidate.uid] = started
        return started

    def _settle(
        self, started: _Started, output: "WorkerCandidateOutput | Failure"
    ) -> None:
        """Record one link's result in the order it actually settled.

        The same-failure streak counts settlements, not launches: a success
        clears it, and a failure of a different kind restarts it at one."""
        started.output = output
        if not isinstance(output, Failure):
            self._streak_kind, self._streak = None, 0
        elif output.kind is self._streak_kind:
            self._streak += 1
        else:
            self._streak_kind, self._streak = output.kind, 1

    # --- The algorithm ------------------------------------------------------
    async def explore(
        self, metric_contract: MetricContractInput
    ) -> "SearchOutput | Failure | None":
        self._by_uid: dict[str, _Started] = {}
        self._streak_kind: FailureKind | None = None
        self._streak = 0
        grant = self.input.grant
        designer_handle = await self.ctx.spawn(
            DesignerAgent(
                input=DesignerInput(
                    num_proposals=self.input.num_designs,
                    remaining_minutes=(await self.budget.snapshot()).wall_clock_remaining_s
                    / 60.0,
                    done_count=0,
                    gpu_ceiling=run_host().host_visible_gpu_count(),
                    reviewer_capability=self.input.designer_reviewer,
                ),
            ).run,
            "Start the assigned work.",
            upstream=(self.setup_handle,),
            read=(task_data(),),
            capability=self.input.designer,
        )
        designer_output = await designer_handle.result()

        chain: list[_Started] = []
        if designer_output.plans and not self._admission_closed():
            started = await self._link(
                WorkerCandidate(
                    input=WorkerCandidateInput(
                        plan=designer_output.plans[0],
                        revision=None,
                        parent=None,
                        parent_files=None,
                        metric_contract=metric_contract,
                        external_scores=self.external_scores(),
                        grant=grant,
                    )
                ),
                (designer_handle,),
            )
            # The width-1 chain: one link at a time, each started from the one
            # that settled before it, with no queue holding a second.
            while True:
                self._settle(started, await started.handle.result())
                scored = started.output
                if not isinstance(scored, WorkerCandidateOutput):
                    break
                chain.append(started)
                best = self.selected_output
                if best is None or (
                    scored.score < best.score
                    if metric_contract.metric_direction == "min"
                    else scored.score > best.score
                ):
                    self.select(SearchOutput(
                        output_dir=scored.output_dir, score=scored.score,
                        components=scored.components,
                    ))
                if not scored.revisions or self._admission_closed():
                    break
                started = await self._link(
                    WorkerCandidate(
                        input=WorkerCandidateInput(
                            plan=scored.plan,
                            revision=scored.revisions[0],
                            parent=ParentAttempt(
                                path=started.composite.path,
                                source_dir=scored.source_dir,
                                output_dir=scored.output_dir,
                                score=scored.score,
                                components=scored.components,
                                feedback=scored.feedback,
                            ),
                            parent_files=started.composite.files(),
                            metric_contract=metric_contract,
                            external_scores=self.external_scores(),
                            grant=grant,
                        )
                    ),
                    (started.handle,),
                )

        scored = [
            (started, started.output)
            for started in chain
            if isinstance(started.output, WorkerCandidateOutput)
        ]
        if not self.worth_ensembling(
            tuple(output.score for _, output in scored), metric_contract
        ):
            return self.selected_output
        ensemble = Ensemble(
            input=EnsembleInput.over(
                tuple(
                    (started.composite.uid, started.composite.files(), output)
                    for started, output in scored
                ),
                metric_contract=metric_contract,
                aggregator=self.input.aggregator,
                score=self.input.grant.score,
            )
        )
        handle = await self.ctx.spawn(
            ensemble.run,
            upstream=tuple(started.handle for started, _ in scored),
            capture_failure=True,
        )
        output = await handle.result()
        if output.failed is True:
            return self.selected_output
        return SearchOutput(
            score=output.score,
            output_dir=output.output_dir,
            components=output.components,
        )

    # --- This Search's own admission and stopping policy ---------------------
    def _admission_closed(self) -> bool:
        """Whether this Search has stopped launching evaluations.

        Its own counts, from the links it started; nothing is rediscovered
        from the run."""
        cap = self.input.max_evaluations
        if cap is not None and len(self._by_uid) >= cap:
            return True
        streak = self.input.early_stopping
        return streak > 0 and self._streak >= streak

SEARCH_TYPE = LinearSearch

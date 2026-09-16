"""ParallelSearch: ``linear``'s chain, widened to N concurrent chains.

One explicit Designer proposes the starting designs. Each chain link is one
complete Worker -- one session that writes the code, runs it, reads its own
errors, fixes them, and exports the result -- scored by the product Score
Program once that session ends, and a finished Worker proposes the direction
for its own chain's next link. At most ``parallel`` chains are active at once:
a chain that continues keeps its own slot, and only a chain that ENDS hands
its slot to a starting design that has not begun yet. The Aggregator then
ensembles every link any chain actually scored.

This package has no Judge, no Selector, and no Router: a config that asks for
model routing under ``search.kind=parallel`` is refused at startup by the
shared ``AIBuildAISearch.prepare_router`` default, which this package does not
override."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from engine.builtin.aibuildai.io import SearchOutput, task_data
from engine.builtin.aibuildai.search import SEARCH_TEMPLATE, AIBuildAISearch
from engine.builtin.parallel.agents.aggregator.agent import AggregatorAgent
from engine.builtin.parallel.agents.designer.agent import DesignerAgent
from engine.builtin.parallel.agents.designer.io import DesignerInput
from engine.builtin.parallel.agents.worker.agent import WorkerAgent
from engine.builtin.parallel.agents.worker.io import ParentAttempt
from engine.builtin.parallel.candidate import Ensemble, WorkerCandidate
from engine.builtin.parallel.io import (
    EnsembleInput,
    ParallelSearchInput,
    ParallelSearchParameters,
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
    """One chain link this Search launched, and what it produced."""

    composite: WorkerCandidate
    handle: _CandidateHandle
    output: "WorkerCandidateOutput | Failure | None" = None


class ParallelSearch(AIBuildAISearch[ParallelSearchInput]):
    description = (
        "One explicit Designer -> up to `parallel` concurrent chains of "
        "complete Workers -> Aggregator ensemble flow."
    )
    __doc__ = description

    prompt_template = SEARCH_TEMPLATE
    agent_types = (DesignerAgent, WorkerAgent, AggregatorAgent)
    parameters_type = ParallelSearchParameters

    @classmethod
    def build_input(
        cls, configs: "AgentConfig", run_paths: RunPaths
    ) -> ParallelSearchInput:
        """Validate the user's ``search.input`` and complete this package's Input."""
        return build_input(cls.parameters(configs), configs, run_paths)

    # --- What this Search asks its own children -----------------------------
    async def _link(
        self, candidate: WorkerCandidate, upstream: "tuple[_AnyHandle, ...]"
    ) -> _Started:
        """Launch one chain link and record it as this Search's own."""
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

        The same-failure streak counts settlements, not launches: with several
        chains running, the order links finish in is not the order they were
        started in. A success clears the streak, and a failure of a different
        kind restarts it at one."""
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

        # Starting designs this Search has not begun a chain for yet, oldest
        # first. A design the Designer proposed but ``parallel`` had no free
        # slot for still gets its own chain once one opens up.
        unstarted = list(designer_output.plans)
        pending: dict[_CandidateHandle, _Started] = {}

        async def open_chains() -> None:
            """Start a fresh chain for every free slot this Search may fill."""
            while (
                unstarted
                and len(pending) < self.input.parallel
                and not self._admission_closed()
            ):
                started = await self._link(
                    WorkerCandidate(
                        input=WorkerCandidateInput(
                            plan=unstarted.pop(0),
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
                pending[started.handle] = started

        await open_chains()
        while pending:
            handle = cast(
                _CandidateHandle,
                (await self.ctx.wait(set(pending), min_completed=1)).completed[0],
            )
            started = pending.pop(handle)
            self._settle(started, await handle.result())
            scored = started.output
            best = self.selected_output
            if isinstance(scored, WorkerCandidateOutput) and (
                best is None
                or (
                    scored.score < best.score
                    if metric_contract.metric_direction == "min"
                    else scored.score > best.score
                )
            ):
                self.select(SearchOutput(
                    output_dir=scored.output_dir, score=scored.score,
                    components=scored.components,
                ))
            if (
                isinstance(scored, WorkerCandidateOutput)
                and scored.revisions
                and not self._admission_closed()
            ):
                # This chain continues in its own slot: the capacity freed by
                # this link finishing is spent on its own next link, never on
                # a starting design that has not begun.
                next_link = await self._link(
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
                pending[next_link.handle] = next_link
                continue
            # This chain ended here (failed, exhausted, or admission closed):
            # its slot is genuinely free, so hand it to the next starting design.
            await open_chains()

        scored = [
            (started, started.output)
            for started in self._by_uid.values()
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

SEARCH_TYPE = ParallelSearch

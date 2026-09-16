"""NBsearch: one complete Worker per candidate, scored after it finishes.

A WorkerDesignerAgent proposes one design plan, and that plan becomes one
Worker that implements AND runs the attempt itself inside its own session.
There is no Coder, no Training Program, no Reviser, no Judge, no Selector, no
Aggregator, and no Router: the whole candidate is one Worker, scored by the
product Score Program once the Worker finishes. A completed Worker proposes
its own revision, which becomes the next Worker in the same single chain, so
the chain is a plain loop with no queue, pending map, or slot filling for a
width of one.

A failed Worker Action stays failed: this package never scores or salvages
whatever files a failed Worker left behind."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from engine.builtin.aibuildai.io import SearchOutput, task_data
from engine.builtin.aibuildai.search import SEARCH_TEMPLATE, AIBuildAISearch
from engine.builtin.nb.agents.designer.agent import WorkerDesignerAgent
from engine.builtin.nb.agents.designer.io import DesignerInput
from engine.builtin.nb.agents.worker.agent import WorkerAgent
from engine.builtin.nb.agents.worker.io import ParentAttempt
from engine.builtin.nb.candidate import CompleteWorker
from engine.builtin.nb.io import (
    NBSearchInput,
    NBSearchParameters,
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


@dataclass(frozen=True)
class _Started:
    """One Worker candidate this Search launched, and what it produced."""

    composite: CompleteWorker
    handle: _CandidateHandle


class NBSearch(AIBuildAISearch[NBSearchInput]):
    description = (
        "One complete Worker per evaluation in a single chain, scored after it finishes."
    )
    __doc__ = description

    prompt_template = SEARCH_TEMPLATE
    agent_types = (WorkerDesignerAgent, WorkerAgent)
    parameters_type = NBSearchParameters

    @classmethod
    def build_input(cls, configs: "AgentConfig", run_paths: RunPaths) -> NBSearchInput:
        """Validate the user's ``search.input`` and complete this package's Input."""
        return build_input(cls.parameters(configs), configs, run_paths)

    # --- What this Search asks its own children -----------------------------
    async def _link(
        self, candidate: CompleteWorker, upstream: "tuple[_AnyHandle, ...]"
    ) -> _Started:
        """Launch one link and record it as this Search's own."""
        handle = cast(
            _CandidateHandle,
            await self.ctx.spawn(candidate.run, upstream=upstream, capture_failure=True),
        )
        started = _Started(composite=candidate, handle=handle)
        self._by_uid[candidate.uid] = started
        return started

    def _settle(self, output: "WorkerCandidateOutput | Failure") -> None:
        """Record one link's result in the order it actually settled.

        The same-failure streak counts settlements, not launches: a success
        clears it, and a failure of a different kind restarts it at one."""
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
            WorkerDesignerAgent(
                input=DesignerInput(
                    num_proposals=self.input.num_designs,
                    remaining_minutes=(await self.budget.snapshot()).wall_clock_remaining_s
                    / 60.0,
                    done_count=0,
                    gpu_ceiling=run_host().host_visible_gpu_count(),
                ),
            ).run,
            "Start the assigned work.",
            upstream=(self.setup_handle,),
            read=(task_data(),),
            capability=self.input.designer,
        )
        designer_output = await designer_handle.result()

        if designer_output.plans and not self._admission_closed():
            started = await self._link(
                CompleteWorker(
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
            # The single chain: one link at a time, each started from the one
            # that settled before it, with no queue holding a second.
            while True:
                scored = await started.handle.result()
                self._settle(scored)
                if not isinstance(scored, WorkerCandidateOutput):
                    break
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
                    CompleteWorker(
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

        return self.selected_output

    # --- This Search's own stopping and selection policy --------------------
    def _admission_closed(self) -> bool:
        """Whether this Search has stopped launching evaluations.

        Its own counts, from the candidates it started; nothing is
        rediscovered from the run."""
        cap = self.input.max_evaluations
        if cap is not None and len(self._by_uid) >= cap:
            return True
        streak = self.input.early_stopping
        return streak > 0 and self._streak >= streak

SEARCH_TYPE = NBSearch

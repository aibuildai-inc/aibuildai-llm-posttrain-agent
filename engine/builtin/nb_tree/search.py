"""NBTreeSearch: the complete-Worker evaluation expanded into a tree.

Each Worker owns implementation and execution for its own evaluation and
leaves the finished output in that evaluation's output directory: no Coder,
Reviser, Training Program, Judge, Selector, or Aggregator. The Designer
creates several starting plans, each Worker may emit several revision children
(or none, which deliberately ends that branch), and every admitted child runs
directly. The framework scores each finished evaluation with the task folder's
own score program.

The frontier below is the real one: proposals this Search has accepted and has
not started yet, each beside the completed Action that wrote it. It holds one
record per proposal -- never the same proposal tracked in parallel maps."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, cast

from engine.builtin.aibuildai.io import SearchOutput, task_data
from engine.builtin.aibuildai.search import SEARCH_TEMPLATE, AIBuildAISearch
from engine.builtin.nb_tree.agents.designer.agent import WorkerDesignerAgent
from engine.builtin.nb_tree.agents.designer.io import DesignerInput
from engine.builtin.nb_tree.agents.worker.agent import WorkerAgent
from engine.builtin.nb_tree.agents.worker.io import ParentAttempt
from engine.builtin.nb_tree.candidate import CompleteWorker
from engine.builtin.nb_tree.io import (
    NBTreeSearchInput,
    NBTreeSearchParameters,
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
class _Proposal:
    """One evaluation this tree has accepted and not started yet.

    It carries the complete Input the candidate will run with, and the
    completed Action that proposed it -- the Designer for a starting plan, the
    parent's own candidate Action for a revision child."""

    payload: WorkerCandidateInput
    producer: _AnyHandle


@dataclass(frozen=True)
class _Started:
    """One candidate this Search launched, and what it produced."""

    composite: CompleteWorker
    handle: _CandidateHandle


class NBTreeSearch(AIBuildAISearch[NBTreeSearchInput]):
    description = """A complete-Worker evaluation lifecycle expanded into a tree: each Worker owns
    implementation and run.py execution for its evaluation and leaves the finished
    output in that evaluation's output directory (no Coder / Reviser / Training Program /
    Judge / Selector / Aggregator); the Designer creates multiple starting plans,
    each Worker can emit multiple revision children, and every child runs
    directly. The framework scores each finished evaluation with the task folder's
    own score program."""
    __doc__ = description

    prompt_template = SEARCH_TEMPLATE
    agent_types = (WorkerDesignerAgent, WorkerAgent)
    parameters_type = NBTreeSearchParameters

    @classmethod
    def build_input(
        cls, configs: "AgentConfig", run_paths: RunPaths
    ) -> NBTreeSearchInput:
        """Validate the user's ``search.input`` and complete this package's Input."""
        return build_input(cls.parameters(configs), configs, run_paths)

    # --- What this Search asks its own children -----------------------------
    def _settle(self, output: "WorkerCandidateOutput | Failure") -> None:
        """Record one candidate's result in the order it actually settled.

        The same-failure streak counts settlements, not launches: with several
        branches running, the order they finish in is not the order they were
        started in. A success clears it; a different failure kind restarts it
        at one."""
        if not isinstance(output, Failure):
            self._streak_kind, self._streak = None, 0
        elif output.kind is self._streak_kind:
            self._streak += 1
        else:
            self._streak_kind, self._streak = output.kind, 1

    def _children(
        self,
        started: _Started,
        scored: WorkerCandidateOutput,
        *,
        metric_contract: MetricContractInput,
    ) -> "list[_Proposal]":
        """The revision children one finished Worker proposed, ready to start.

        Each child is handed its parent's own accepted facts -- the formal
        score its Worker never saw, that score's parts, what that Worker
        reported, and the two directories to read -- so nothing walks the run
        workspace to recover them."""
        parent = ParentAttempt(
            path=started.composite.path,
            source_dir=scored.source_dir,
            output_dir=scored.output_dir,
            score=scored.score,
            components=scored.components,
            feedback=scored.feedback,
        )
        return [
            _Proposal(
                payload=WorkerCandidateInput(
                    plan=scored.plan,
                    revision=revision,
                    parent=parent,
                    parent_files=started.composite.files(),
                    metric_contract=metric_contract,
                    external_scores=self.external_scores(),
                    grant=self.input.grant,
                ),
                producer=started.handle,
            )
            for revision in scored.revisions
        ]

    # --- The algorithm ------------------------------------------------------
    async def explore(
        self, metric_contract: MetricContractInput
    ) -> "SearchOutput | Failure | None":
        self._by_uid: dict[str, _Started] = {}
        self._streak_kind: FailureKind | None = None
        self._streak = 0
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

        frontier: list[_Proposal] = [
            _Proposal(
                payload=WorkerCandidateInput(
                    plan=plan,
                    revision=None,
                    parent=None,
                    parent_files=None,
                    metric_contract=metric_contract,
                    external_scores=self.external_scores(),
                    grant=self.input.grant,
                ),
                producer=designer_handle,
            )
            for plan in designer_output.plans
        ]
        pending: dict[_CandidateHandle, _Started] = {}

        while True:
            while (
                frontier
                and len(pending) < self.input.parallel
                and not self._admission_closed()
            ):
                proposal = frontier.pop(0)
                candidate = CompleteWorker(input=proposal.payload)
                handle = cast(
                    _CandidateHandle,
                    await self.ctx.spawn(
                        candidate.run,
                        "Start the assigned work.",
                        upstream=(proposal.producer,),
                        capture_failure=True,
                    ),
                )
                started = _Started(composite=candidate, handle=handle)
                self._by_uid[candidate.uid] = started
                pending[handle] = started
            if pending:
                handle = cast(
                    _CandidateHandle,
                    (await self.ctx.wait(set(pending), min_completed=1)).completed[0],
                )
                started = pending.pop(handle)
                scored = await handle.result()
                self._settle(scored)
                if isinstance(scored, WorkerCandidateOutput):
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
                    frontier.extend(
                        self._children(
                            started, scored, metric_contract=metric_contract
                        )
                    )
                continue
            if not frontier or self._admission_closed():
                break

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

SEARCH_TYPE = NBTreeSearch

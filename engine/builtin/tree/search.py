"""TreeSearch: branch, judge, select, revise, and ensemble.

The Judge and the Selector are two roles here, deliberately: one says how good
each proposal is, the other decides which of the judged proposals is worth an
execution slot. This package is also the one that routes models per candidate.

One local record per unstarted proposal holds everything already known about
it -- the proposal, its parent, the Action that actually WROTE it, and the
Judge verdict it already carries -- so a proposal is never tracked in parallel
maps that have to be kept in step, and a proposal that waited for a free slot
is judged once."""

from __future__ import annotations

from collections.abc import Sequence
from typing import TYPE_CHECKING, cast

from engine.builtin.aibuildai.io import SearchOutput, task_data
from engine.builtin.aibuildai.search import SEARCH_TEMPLATE, AIBuildAISearch
from engine.builtin.tree.agents.aggregator.agent import AggregatorAgent
from engine.builtin.tree.agents.coder.agent import CoderAgent
from engine.builtin.tree.agents.designer.agent import DesignerAgent
from engine.builtin.tree.agents.designer.io import DesignerInput
from engine.builtin.tree.agents.judge.agent import JudgeAgent
from engine.builtin.tree.agents.judge.io import IntegrityViolationInput, JudgeInput
from engine.builtin.tree.agents.reviser.agent import ReviserAgent
from engine.builtin.tree.agents.reviser.io import (
    RevisionProposal,
    ScoredCandidateOutput,
)
from engine.builtin.tree.agents.router.agent import RouterAgent
from engine.builtin.tree.agents.router.routing import route_candidate
from engine.builtin.tree.agents.selector.agent import SelectorAgent
from engine.builtin.tree.agents.selector.io import SelectorInput
from engine.builtin.tree.candidate import CoderCandidate, Ensemble
from engine.builtin.tree.io import (
    AnyHandle,
    CandidateHandle,
    EnsembleInput,
    Proposal,
    Started,
    TreeSearchInput,
    TreeSearchParameters,
    build_input,
    router_capability,
)
from engine.builtin.tree.programs import TrainingProgram
from engine.durable_execution import run_host
from engine.durable_execution import FileRef
from engine.failure import Failure, FailureKind
from engine.metric_contract import MetricContractInput
from engine.paths import RunPaths
from engine.work_unit.agent.prompt import INTEGRITY_VIOLATIONS

if TYPE_CHECKING:
    from config import AgentConfig
    from engine.work_unit.agent.plugins import EnableConfig
    from engine.work_unit.agent.policy import ModelRouter

def _parent_files(proposals: "Sequence[Proposal]") -> tuple[FileRef, ...]:
    """The tree of every parent candidate these proposals continue, named once.

    A role judging or selecting revisions is told each parent's score and
    directories on its own Input; this is what lets it open them. A proposal
    with no parent adds nothing, so a first generation reads only task data."""
    return tuple(
        dict.fromkeys(
            item.parent.composite.files()
            for item in proposals
            if item.parent is not None
        )
    )


class TreeSearch(AIBuildAISearch[TreeSearchInput]):
    description = """The branch-and-select tree: designer fans out designs, judge scores,
    selector picks which ones to run, reviser revises completed ones, aggregator ensembles across leaves."""
    __doc__ = description

    prompt_template = SEARCH_TEMPLATE
    agent_types = (
        DesignerAgent,
        CoderAgent,
        ReviserAgent,
        JudgeAgent,
        SelectorAgent,
        AggregatorAgent,
        RouterAgent,
    )
    unit_types = (TrainingProgram,)
    parameters_type = TreeSearchParameters

    @classmethod
    def build_input(cls, configs: "AgentConfig", run_paths: RunPaths) -> TreeSearchInput:
        """Validate the user's ``search.input`` and complete this package's Input."""
        return build_input(cls.parameters(configs), configs, run_paths)

    @classmethod
    def prepare_router(
        cls, configs: "AgentConfig", *, enable: "EnableConfig"
    ) -> "ModelRouter | None":
        from engine.builtin.tree.agents.router.routing import Router

        # The Router is one of this package's own children, so this package
        # resolves what it may spend, here, and the Router is handed the
        # answer instead of asking for it.
        return Router.prepare(
            configs, enable=enable, router_capability=router_capability(configs)
        )

    # --- What this Search asks its own children ----------------------------
    async def _judge(self, proposals: "list[Proposal]") -> "Failure | None":
        """Record each proposal verdict, or return the Judge failure."""
        request = JudgeInput(
            proposals=tuple(proposal.judged() for proposal in proposals),
            violations=tuple(
                IntegrityViolationInput(**violation)
                for violation in INTEGRITY_VIOLATIONS
            ),
        )
        handle = await self.ctx.spawn(
            JudgeAgent(input=request, ).run,
            "Start the assigned work.",
            upstream=tuple(dict.fromkeys(item.producer for item in proposals)),
            read=(task_data(), *_parent_files(proposals)),
            capture_failure=True,
            capability=self.input.judge,
        )
        output = await handle.result()
        if output.failed is True:
            return cast(Failure, output)
        verdicts = {score.uid: score for score in output.scores}
        for proposal in proposals:
            proposal.verdict = verdicts[proposal.uid]
            proposal.judge = handle
            proposal.judge_feedback = output.feedback
        return None

    async def _select(
        self, ready: "list[Proposal]"
    ) -> "tuple[tuple[str, ...], str, AnyHandle]":
        """Ask the Selector which judged proposals are worth an execution slot."""
        handle = await self.ctx.spawn(
            SelectorAgent(
                input=SelectorInput(
                    ready_proposals=tuple(item.ready() for item in ready),
                    judge_feedback=tuple(
                        dict.fromkeys(
                            item.judge_feedback for item in ready if item.judge_feedback
                        )
                    ),
                    attempted_count=len(self._by_uid),
                    remaining_minutes=await self._remaining_minutes(),
                    max_evaluations=self.input.max_evaluations,
                ),
            ).run,
            "Start the assigned work.",
            upstream=tuple(
                dict.fromkeys(
                    item.judge for item in ready if item.judge is not None
                )
            ),
            read=(task_data(), *_parent_files(ready)),
            capability=self.input.selector,
        )
        selection = await handle.result()
        return selection.selected_uids, selection.feedback, handle

    async def _revise(
        self, started: Started, *, metric_contract: MetricContractInput
    ) -> "tuple[tuple[RevisionProposal, ...], str, AnyHandle] | None":
        """Ask the Reviser what to try next after one candidate finished.

        It is handed the design this line started from and the revisions
        actually carried out along it, so it diagnoses from the lineage the
        Search kept rather than from a walk over the run's executions."""
        scored = started.output
        if not isinstance(scored, ScoredCandidateOutput):
            return None
        handle = await self.ctx.spawn(
            ReviserAgent(
                input=started.reviser_request(
                    num_proposals=self.input.num_revisions,
                    metric_contract=metric_contract,
                    remaining_minutes=await self._remaining_minutes(),
                    done_count=self._done_count(),
                    external_scores=self.external_scores(),
                    reviewer_capability=self.input.reviser_review,
                ),
            ).run,
            "Start the assigned work.",
            upstream=(started.handle,),
            read=(task_data(), started.composite.files()),
            capture_failure=True,
            capability=self.input.reviser,
        )
        output = await handle.result()
        if output.failed is True:
            return None
        # This Reviser Action IS the producer of the proposals below: naming
        # the candidate it read would credit the wrong Action.
        return output.proposals, output.feedback, handle

    async def _start(
        self, proposal: Proposal, selector_feedback: str, selector: AnyHandle
    ) -> Started:
        """Build, route, and launch the candidate one selected proposal runs."""
        parent = proposal.parent
        candidate = CoderCandidate(
            input=proposal.candidate_input(selector_feedback, self.input.grant)
        )
        await route_candidate(
            self,
            candidate,
            parents=() if parent is None else (parent.composite.path,),
            prior=tuple(self._by_uid.values()),
        )
        upstream: tuple[AnyHandle, ...] = (proposal.producer, selector)
        if proposal.judge is not None:
            upstream = (*upstream, proposal.judge)
        if parent is not None:
            upstream = (parent.handle, *upstream)
        handle = cast(
            CandidateHandle,
            await self.ctx.spawn(candidate.run, upstream=upstream, capture_failure=True),
        )
        started = Started(composite=candidate, handle=handle, proposal=proposal)
        self._by_uid[candidate.uid] = started
        return started

    # --- The algorithm -----------------------------------------------------
    async def explore(
        self, metric_contract: MetricContractInput
    ) -> "SearchOutput | Failure | None":
        self._by_uid: dict[str, Started] = {}
        self._streak_kind: FailureKind | None = None
        self._streak = 0
        designer_handle = await self.ctx.spawn(
            DesignerAgent(
                input=DesignerInput(
                    num_proposals=self.input.num_designs,
                    remaining_minutes=await self._remaining_minutes(),
                    done_count=0,
                    gpu_ceiling=run_host().host_visible_gpu_count(),
                    reviewer_capability=self.input.designer_review,
                ),
            ).run,
            "Start the assigned work.",
            upstream=(self.setup_handle,),
            read=(task_data(),),
            capability=self.input.designer,
        )
        designed = await designer_handle.result()

        count = 0
        proposals: dict[str, Proposal] = {}
        for plan in designed.plans:
            count += 1
            proposals[f"proposal_{count}"] = Proposal(
                uid=f"proposal_{count}",
                payload=plan,
                parent=None,
                producer=designer_handle,
                producer_feedback=designed.feedback,
            )

        pending: dict[CandidateHandle, Started] = {}
        leaves: set[str] = set()
        selector_ended = False
        while True:
            ready = [
                item
                for item in proposals.values()
                if item.parent is None or item.parent.output is not None
            ]
            # A Selector call with no free slot to fill would be answered and
            # then thrown away, so it is not made.
            if (
                ready
                and len(pending) < self.input.parallel
                and not selector_ended
                and not self._admission_closed()
            ):
                unjudged = [item for item in ready if item.verdict is None]
                if unjudged and (refused := await self._judge(unjudged)) is not None:
                    return refused
                selection, feedback, selector = await self._select(ready)
                # An empty selection is the Selector ending this search; its
                # own Output refuses that verdict without a reason.
                selector_ended = not selection
                for uid in selection:
                    if self._admission_closed() or len(pending) >= self.input.parallel:
                        break
                    started = await self._start(
                        proposals.pop(uid), feedback, selector
                    )
                    pending[started.handle] = started
                    if started.proposal.parent is not None:
                        leaves.discard(started.proposal.parent.composite.uid)
                    leaves.add(started.composite.uid)
            if pending:
                handle = cast(
                    CandidateHandle,
                    (await self.ctx.wait(set(pending), min_completed=1)).completed[0],
                )
                started = pending.pop(handle)
                self._settle(started, await handle.result())
                candidate_output = started.output
                best = self.selected_output
                if isinstance(candidate_output, ScoredCandidateOutput) and (
                    best is None
                    or (
                        candidate_output.score < best.score
                        if metric_contract.metric_direction == "min"
                        else candidate_output.score > best.score
                    )
                ):
                    self.select(SearchOutput(
                        output_dir=candidate_output.output_dir, score=candidate_output.score,
                        components=candidate_output.components,
                    ))
                # A revision is only worth paying for while this Search can
                # still admit a proposal. Once admission has closed or the
                # Selector has ended the search, nothing the Reviser writes
                # could ever be launched, so it is not called at all.
                if not selector_ended and not self._admission_closed():
                    revised = await self._revise(
                        started, metric_contract=metric_contract
                    )
                    if revised is not None:
                        batch, feedback, producer = revised
                        for payload in batch:
                            count += 1
                            proposals[f"proposal_{count}"] = Proposal(
                                uid=f"proposal_{count}",
                                payload=payload,
                                parent=started,
                                producer=producer,
                                producer_feedback=feedback,
                            )
                continue
            if selector_ended or not ready or self._admission_closed():
                break

        scored = [
            (started, started.output)
            for uid in leaves if uid in self._by_uid
            for started in (self._by_uid[uid],)
            if isinstance(started.output, ScoredCandidateOutput)
        ]
        if not self.worth_ensembling(
            tuple(output.score for _, output in scored), metric_contract
        ):
            return self.selected_output
        ensemble = Ensemble(
            input=EnsembleInput.over(
                tuple(
                    (started.composite.uid, started.composite.files(), out)
                    for started, out in scored
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

    # --- This Search's own admission and stopping policy --------------------
    async def _remaining_minutes(self) -> float:
        return (await self.budget.snapshot()).wall_clock_remaining_s / 60.0

    def _done_count(self) -> int:
        return sum(
            1
            for started in self._by_uid.values()
            if isinstance(started.output, ScoredCandidateOutput)
        )

    def _settle(
        self, started: Started, output: "ScoredCandidateOutput | Failure"
    ) -> None:
        """Record one candidate's result in the order it actually settled.

        The same-failure streak counts settlements, not launches: with several
        candidates running, the order they finish in is not the order they
        were started in. A success clears it; a different failure kind
        restarts it at one."""
        started.output = output
        if not isinstance(output, Failure):
            self._streak_kind, self._streak = None, 0
        elif output.kind is self._streak_kind:
            self._streak += 1
        else:
            self._streak_kind, self._streak = output.kind, 1

    def _admission_closed(self) -> bool:
        """Whether this Search has stopped launching evaluations."""
        cap = self.input.max_evaluations
        if cap is not None and len(self._by_uid) >= cap:
            return True
        streak = self.input.early_stopping
        return streak > 0 and self._streak >= streak

SEARCH_TYPE = TreeSearch

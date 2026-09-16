"""The tree package's own records, and how a run's configuration becomes them.

Every record here is local to this package. Another package may spell a field
the same way with its own meaning, or declare none of them. ``build_input``
is this package's one construction entry: it resolves the user's settings into
the immutable values every child is handed, once, at startup.

Two kinds of record live here. The frozen models above ``Proposal`` are the
ones that TRAVEL: they are serialized into a child's Input and read back on
resume. ``Proposal`` and ``Started`` at the end are the Search's own local
bookkeeping, which is why they may hold a live ``Handle`` -- an orchestration
value that must never reach a serializable Input or Output -- and why they
build the typed requests their facts fill in rather than handing those facts
out one at a time."""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING, Any

from pydantic import ConfigDict, Field, JsonValue

from engine.base import WorkflowBaseModel
from engine.builtin.aibuildai.agents.submitter.io import ExternalScore
from engine.builtin.aibuildai.capability import DurationBudgetRule, score_launch
from engine.builtin.aibuildai.programs.score import ScoreLaunch, ScoreOutput
from engine.builtin.tree.agents.aggregator.agent import AggregatorAgent
from engine.builtin.tree.agents.coder.agent import CoderAgent
from engine.builtin.tree.agents.designer.agent import DesignerAgent
from engine.builtin.tree.agents.designer.io import DesignPlan
from engine.builtin.tree.agents.judge.agent import JudgeAgent
from engine.builtin.tree.agents.judge.io import JudgeProposalInput, ProposalScore
from engine.builtin.tree.agents.reviser.agent import ReviserAgent
from engine.builtin.tree.agents.reviser.io import (
    ReviserInput,
    RevisionProposal,
    ScoredCandidateOutput,
)
from engine.builtin.tree.agents.selector.agent import SelectorAgent
from engine.builtin.tree.agents.selector.io import SelectorReadyInput
from engine.builtin.tree.programs import TrainingProgram
from engine.capability import ExecutionCapability
from engine.composite import Composite
from engine.durable_execution import FileRef, Handle
from engine.execution_output import ExecutionOutput
from engine.failure import Failure
from engine.metric_contract import MetricContractInput
from engine.paths import RunPaths

if TYPE_CHECKING:
    from config import AgentConfig
    from engine.work_unit.agent.base import Agent
    from engine.work_unit.base import WorkUnit


# --- What this package's own children may spend --------------------------
# This package resolves its own roles, from the run's configuration, once at
# startup. It is deliberately a copy of what every other package does: no
# shared layer answers for an arbitrary role name, so one package's children
# can never be named, reached, or re-priced from another. The question is
# always asked of a Definition class this package owns, never of a string.
def _capability(
    configs: "AgentConfig",
    unit_type: "type[WorkUnit]",
    *,
    expected_minutes: float | None = None,
) -> ExecutionCapability:
    """What one child of this package may spend."""
    kind, name = unit_type.kind, unit_type.name
    if name is None:
        raise AssertionError(f"{unit_type.__name__} declares no configured name")
    time_config = configs.work_unit_time_for(kind, name)
    limit = configs.resources.work_unit.limit_for(kind, name)
    return ExecutionCapability(
        wall_clock_seconds=time_config.cap_seconds(
            expected_minutes=expected_minutes,
            pipeline_minutes=configs.run.budget.wall_clock_minutes,
        ),
        cpu_max_cores=limit.cpu_max_cores,
        memory_max_gb=float(limit.memory_max_gb),
        gpus=0,
        # File isolation is physical, so it is declared by the unit that does
        # the work, not by a container above it. None where the run did not ask
        # for it, so a unit never argues with an owner that did.
        retry=time_config.retry,
    )


def router_capability(configs: "AgentConfig") -> ExecutionCapability:
    """What this package's own Router may spend."""
    from engine.builtin.tree.agents.router.agent import RouterAgent

    return _capability(configs, RouterAgent)


def _reviewer(
    configs: "AgentConfig", producer: "type[Agent]"
) -> ExecutionCapability | None:
    """What this role's own semantic review may spend, when this run composes one.

    None when the role composes no reviewer for this configuration. That None
    is the whole answer: it travels to the role as its frozen
    ``reviewer_capability``, so nothing asks configuration again at run time."""
    reviewer = producer.semantic_reviewer()
    if reviewer is None or not producer.composes_reviewer(configs):
        return None
    return _capability(configs, reviewer)


def _duration_rule(
    configs: "AgentConfig", unit_type: "type[WorkUnit]"
) -> DurationBudgetRule:
    """Freeze this child's duration rule for this package's own Input."""
    name = unit_type.name
    if name is None:
        raise AssertionError(f"{unit_type.__name__} declares no configured name")
    time_config = configs.work_unit_time_for(unit_type.kind, name)
    budget = time_config.budget
    expected = budget.kind == "expected"
    return DurationBudgetRule(
        base=_capability(
            configs, unit_type, expected_minutes=1.0 if expected else None
        ),
        slack_fraction=getattr(budget, "slack_fraction", None) if expected else None,
        minimum_minutes=getattr(budget, "min_minutes", 0.0) if expected else 0.0,
        maximum_minutes=getattr(budget, "max_minutes", 0.0) if expected else 0.0,
    )


def _budget_minutes(capability: ExecutionCapability) -> float:
    """One resolved child's own settled wall clock, in minutes."""
    seconds = capability.wall_clock_seconds
    if seconds is None:
        raise AssertionError("a resolved child capability states its wall clock")
    return seconds / 60.0


class TreeSearchParameters(WorkflowBaseModel):
    """TreeSearch's own business parameters: the half a user writes.

    Local to this kind: another Search may spell a field the same way with its
    own meaning, or not declare it at all."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    parallel: int = Field(
        gt=0,
        description="max concurrent evaluations. This is the concurrency "
        "ceiling, NOT the search method. Required; never derived from host "
        "hardware.",
        json_schema_extra={"placeholder": 4},
    )
    num_designs: int | None = Field(
        default=None,
        ge=1,
        description="initial designs the designer proposes (the "
        "fan-out the judge/selector rank). null = the designer decides the "
        "count from task complexity; an int forces exactly that many.",
    )
    num_revisions: int | None = Field(
        default=None,
        ge=1,
        description="revision proposals a completed evaluation fans out "
        "(children proposed per evaluation). null = the Reviser decides the "
        "count; an int forces exactly that many. 0 proposals stays legal and "
        "deliberately ends that line.",
    )
    max_evaluations: int | None = Field(
        default=None,
        ge=0,
        description="attempted-evaluation ceiling (failures occupy a slot); 0 "
        "runs no evaluations at all. null = no count cap: the selector stops "
        "the search when further exploration is no longer worthwhile, backed "
        "by the run-level wall-clock + cost budget.",
    )
    early_stopping: int = Field(
        default=3,
        ge=0,
        description="stop the search once this many completed evaluations in a "
        "row have all failed the same way (the same failure.kind). 0 turns "
        "this off.",
    )


class CandidateGrant(WorkflowBaseModel):
    """What one tree candidate needs to build its own children.

    Frozen at the spawn site out of the Search's own immutable Input, so a
    candidate never reads configuration, never asks a resolver for a
    capability, and never queries the run to rediscover a fact its owner
    already had."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    task_name: str
    train_file_name: str
    training_cpu_cores: float
    coder_budget_minutes: float
    training_budget_minutes: float | None
    progress_enabled: bool
    coder: ExecutionCapability
    coder_review: ExecutionCapability | None
    training: DurationBudgetRule
    score: ScoreLaunch


class TreeSearchInput(TreeSearchParameters):
    """TreeSearch's complete immutable Input.

    The business parameters above are the user's. Everything below is resolved
    once by ``build_input``, so nothing this Search creates ever reads
    configuration or asks for a capability by role name."""

    designer: ExecutionCapability
    designer_review: ExecutionCapability | None
    reviser: ExecutionCapability
    reviser_review: ExecutionCapability | None
    judge: ExecutionCapability
    selector: ExecutionCapability
    aggregator: ExecutionCapability
    grant: CandidateGrant


def build_input(
    parameters: dict[str, Any], configs: "AgentConfig", run_paths: RunPaths
) -> TreeSearchInput:
    """Complete this package's Input from the user's validated parameters."""
    coder = _capability(configs, CoderAgent)
    training_time = configs.work_unit_time_for("program", TrainingProgram.name)
    return TreeSearchInput(
        **parameters,
        designer=_capability(configs, DesignerAgent),
        designer_review=_reviewer(configs, DesignerAgent),
        reviser=_capability(configs, ReviserAgent),
        reviser_review=_reviewer(configs, ReviserAgent),
        judge=_capability(configs, JudgeAgent),
        selector=_capability(configs, SelectorAgent),
        aggregator=_capability(configs, AggregatorAgent),
        grant=CandidateGrant(
            task_name=configs.run.task_name,
            train_file_name=configs.train_file_name,
            training_cpu_cores=configs.resources.work_unit.limit_for(
                "program", TrainingProgram.name
            ).cpu_max_cores,
            coder_budget_minutes=_budget_minutes(coder),
            training_budget_minutes=(
                None
                if training_time.uses_expected_duration
                else _budget_minutes(_capability(configs, TrainingProgram))
            ),
            progress_enabled=configs.progress.enable,
            coder=coder,
            coder_review=_reviewer(configs, CoderAgent),
            training=_duration_rule(configs, TrainingProgram),
            score=score_launch(configs, run_paths),
        ),
    )


class ParentCandidate(WorkflowBaseModel):
    """The accepted facts of the candidate a revision continues.

    Every field here is something the Search already held when it started the
    revision: what that candidate ran, the formal score the run's own score
    program measured for it, what its own Coder reported before Training, and
    the exact directories to read. Nothing here is recovered by walking the
    run workspace."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uid: str
    # The accepted source tree this revision starts from, and the attempt
    # directory the score below was measured on.
    source_dir: str
    output_dir: str
    score: float
    components: tuple[tuple[str, float], ...]
    coder_feedback: str
    # The parent candidate's own tree, so the revision that continues it is
    # granted exactly that candidate's files. The two directories above say
    # WHICH files to read; this says the revision may read them at all.
    files: FileRef


class CoderCandidateInput(WorkflowBaseModel):
    """One selected proposal evaluated by Coder, Training and Score.

    A fresh design and a revision are the SAME operation here, told apart by
    the business facts below rather than by two Composite classes forwarding
    to one body: a fresh design states no parent, and a revision states the
    candidate it continues."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    proposal: DesignPlan | RevisionProposal
    # The Judge's own accepted verdict on this proposal and the Selector's own
    # reason for running it: the two judgements that admitted this candidate,
    # carried to the Coder that acts on them rather than stored unread.
    verdict: ProposalScore
    selector_feedback: str
    parent: ParentCandidate | None
    grant: CandidateGrant

    @property
    def requested_gpus(self) -> int:
        return self.proposal.requested_gpus


class EnsembleMember(WorkflowBaseModel):
    """One scored candidate an ensemble was told to combine."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    uid: str
    # This member's own tree, so an ensemble over N members is granted the N
    # members the Search chose and nothing else.
    files: FileRef
    # The exact directory this member's own score was measured on, so the
    # Aggregator opens the scored output instead of guessing which directory
    # under a Composite holds it.
    output_dir: str
    score: float


class EnsembleInput(WorkflowBaseModel):
    """One bounded combination of already scored candidates.

    Its members are also its direct ``upstream``; their facts are here because
    the Search that selected them had them."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    members: tuple[EnsembleMember, ...] = Field(min_length=1)
    metric_contract: MetricContractInput
    memory_cap: str
    aggregator: ExecutionCapability
    score: ScoreLaunch

    @classmethod
    def over(
        cls,
        members: "tuple[tuple[str, FileRef, ScoreOutput], ...]",
        *,
        metric_contract: MetricContractInput,
        aggregator: ExecutionCapability,
        score: ScoreLaunch,
    ) -> "EnsembleInput":
        """The combination of the members the Search chose, as its own record.

        Each member arrives as its uid and its own tree beside the Output it
        settled with, so every fact here is one the Search already held; the
        Aggregator is never asked to find a member's scored directory for
        itself, and the grants it runs under are derived from this collection
        rather than written out one path at a time."""
        chosen = tuple(
            EnsembleMember(
                uid=uid, files=files, output_dir=output.output_dir, score=output.score
            )
            for uid, files, output in members
        )
        return cls(
            members=chosen,
            metric_contract=metric_contract,
            memory_cap=f"{int(aggregator.memory_max_gb or 0)}G",
            aggregator=aggregator,
            score=score,
        )


AnyHandle = Handle[ExecutionOutput[bool]]
CandidateHandle = Handle["ScoredCandidateOutput | Failure"]


@dataclass
class Proposal:
    """One proposal this tree may still launch, and all it already knows.

    ``producer`` is the Action that really WROTE this proposal -- the Designer
    for a fresh plan, the Reviser call for a revision -- never the completed
    candidate that Reviser read. Once a Judge has scored it, that verdict and
    the Judge Action that made it stay here."""

    uid: str
    payload: "DesignPlan | RevisionProposal"
    parent: "Started | None"
    producer: AnyHandle
    producer_feedback: str
    verdict: ProposalScore | None = None
    judge: AnyHandle | None = None
    judge_feedback: str = ""

    @property
    def parent_output(self) -> "ScoredCandidateOutput | None":
        """What the candidate this proposal revises actually scored, if any."""
        if self.parent is None or not isinstance(
            self.parent.output, ScoredCandidateOutput
        ):
            return None
        return self.parent.output

    @property
    def scored(self) -> ProposalScore:
        if self.verdict is None:
            raise AssertionError(f"{self.uid} was never judged")
        return self.verdict

    def judged(self) -> JudgeProposalInput:
        """This proposal as the Judge's own request item."""
        parent, output = self.parent, self.parent_output
        return JudgeProposalInput(
            uid=self.uid,
            proposal=self.payload,
            producer_feedback=self.producer_feedback,
            parent_uid=None if parent is None else parent.proposal.uid,
            parent_proposal=None if parent is None else parent.proposal.payload,
            parent_metric=None if output is None else output.score,
            parent_directory=None if output is None else output.source_dir,
            parent_output_dir=None if output is None else output.output_dir,
        )

    def candidate_input(
        self, selector_feedback: str, grant: CandidateGrant
    ) -> CoderCandidateInput:
        """The Input the candidate this proposal runs is started with."""
        parent, output = self.parent, self.parent_output
        if isinstance(self.payload, RevisionProposal) and output is None:
            raise AssertionError(
                f"{self.uid} revises a candidate that produced no score"
            )
        return CoderCandidateInput(
            proposal=self.payload,
            verdict=self.scored,
            selector_feedback=selector_feedback,
            parent=None
            if parent is None or output is None
            else ParentCandidate(
                uid=parent.proposal.uid,
                files=parent.composite.files(),
                source_dir=output.source_dir,
                output_dir=output.output_dir,
                score=output.score,
                components=output.components,
                coder_feedback=output.coder_feedback,
            ),
            grant=grant,
        )

    def ready(self) -> SelectorReadyInput:
        """This judged proposal as the Selector's own request item."""
        verdict, output = self.scored, self.parent_output
        return SelectorReadyInput(
            uid=self.uid,
            proposal=self.payload,
            parent_uid=None if self.parent is None else self.parent.proposal.uid,
            parent_metric=None if output is None else output.score,
            parent_directory=None if output is None else output.source_dir,
            score_dimensions=verdict.dimensions,
            score_rationale=verdict.rationale,
            score_violations=verdict.violations,
        )


@dataclass
class Started:
    """One candidate this Search launched, and what it produced."""

    composite: Composite
    handle: CandidateHandle
    proposal: Proposal
    output: "ScoredCandidateOutput | Failure | None" = None

    @property
    def parents(self) -> tuple[str, ...]:
        """The durable path of the candidate this one revises, if any."""
        parent = self.proposal.parent
        return () if parent is None else (parent.composite.path,)

    @property
    def metric(self) -> float | None:
        """The formal score this candidate reached, or None until it has one."""
        return (
            self.output.score
            if isinstance(self.output, ScoredCandidateOutput)
            else None
        )

    @property
    def reviser_output(self) -> "JsonValue | None":
        """The revision proposal that produced this candidate, if one did."""
        payload = self.proposal.payload
        return (
            payload.model_dump(mode="json")
            if isinstance(payload, RevisionProposal)
            else None
        )

    def reviser_request(
        self,
        *,
        num_proposals: int | None,
        metric_contract: MetricContractInput,
        remaining_minutes: float,
        done_count: int,
        external_scores: tuple[ExternalScore, ...],
        reviewer_capability: ExecutionCapability | None,
    ) -> ReviserInput:
        """This candidate's own facts and its lineage, as the Reviser's request."""
        scored = self.output
        if not isinstance(scored, ScoredCandidateOutput):
            raise AssertionError(f"{self.composite.path} produced no score to revise")
        origin, executed = self.lineage()
        return ReviserInput(
            num_proposals=num_proposals,
            target_uid=self.proposal.uid,
            target_path=self.composite.path,
            target=scored,
            origin_plan=origin,
            executed_revisions=executed,
            metric_contract=metric_contract,
            remaining_minutes=remaining_minutes,
            done_count=done_count,
            external_scores=external_scores,
            reviewer_capability=reviewer_capability,
        )

    def lineage(self) -> "tuple[DesignPlan, tuple[RevisionProposal, ...]]":
        """The design this line started from, and what was executed on it."""
        payloads: list[DesignPlan | RevisionProposal] = []
        node: "Started | None" = self
        while node is not None:
            payloads.append(node.proposal.payload)
            node = node.proposal.parent
        origin = payloads[-1]
        if not isinstance(origin, DesignPlan):
            raise AssertionError(f"{self.composite.path} has no root design")
        return origin, tuple(
            payload
            for payload in reversed(payloads[:-1])
            if isinstance(payload, RevisionProposal)
        )

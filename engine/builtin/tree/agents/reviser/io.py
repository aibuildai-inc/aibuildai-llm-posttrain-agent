"""Input and Output records for the reviser Agent family."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from typing import TYPE_CHECKING

from pydantic import ConfigDict, Field, PositiveInt, ValidationInfo, model_validator

from engine.base import WorkflowBaseModel
from engine.builtin.aibuildai.programs.score import ScoreOutput
from engine.execution_output import SuccessfulOutput
from engine.metric_contract import MetricContractInput
from engine.work_unit.agent.base import AgentInput
from engine.builtin.tree.agents.designer.io import Citation, DesignPlan
from engine.builtin.aibuildai.agents.submitter.io import ExternalScore

if TYPE_CHECKING:
    pass


class RecipeChange(WorkflowBaseModel):
    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "description": "A narrative edit to one component of the revised "
            "design's design plan."
        },
    )
    target: str = Field(
        description="Which area is being changed. Use one of: 'architecture', "
        "'data_pipeline', 'loss_function', 'regularization', 'training_config', "
        "'validation_strategy'."
    )
    from_state: str = Field(
        description="One sentence describing the revised design's current state for "
        "this target (your understanding of what's there now)."
    )
    to_state: str = Field(
        description="One sentence describing the new state — concrete and "
        "actionable, the recipient uses this as-is."
    )
    rationale: str = Field(
        description="Why this change addresses the diagnosis. Must tie to evidence "
        "in `diagnosis`."
    )


class CodePatch(WorkflowBaseModel):
    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "description": inspect.cleandoc(
                """An exact find/replace patch applied to inherited design source.

        Use this when the failure is code-shaped (NaN logits, single-class
        collapse, off-by-one indexing, AMP misuse) rather than recipe-shaped.
        The recipient applies these verbatim BEFORE applying recipe_changes."""
            )
        },
    )
    file: str = Field(
        description="Path relative to the recipient's `owner_dir` (e.g. 'run.py')."
    )
    find: str = Field(
        description="EXACT substring to locate in the file. Multi-line is fine; "
        "whitespace must match. If the recipient cannot find this string, the "
        "patch FAILS and execution stops — do not paraphrase or guess."
    )
    replace: str = Field(
        description="Replacement substring. Use empty string to delete the `find` "
        "block."
    )
    rationale: str = Field(
        description="One sentence: what bug this patch fixes and how you identified "
        "it (e.g. 'the revised design's GradScaler.step is called without unscale_, "
        "causing fp16 inf->NaN in 64.5K-class softmax; identified by loading "
        "best_model.pth and observing NaN logits on a sample batch')."
    )


class RevisionProposal(WorkflowBaseModel):
    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "description": inspect.cleandoc(
                """A focused diff against the revised design's effective design.

        Emitted by the Reviser instead of a fresh DesignPlan. The Coder
        inherits the revised design's source code and applies ``code_patches``
        first (verbatim find/replace) then ``recipe_changes`` (narrative
        component edits). Either list may be empty:

          - Both empty: "continue training with more budget" — the same design,
            with no code or recipe change.
          - Only ``recipe_changes``: hyperparameter / component sweep.
          - Only ``code_patches``: bug-fix only, recipe unchanged.
          - Both: combined recipe edit + bug fix."""
            )
        },
    )
    name: str = Field(
        description="Short descriptive name (e.g. 'C2R1_BiasInit_FixedLR')."
    )
    diagnosis: str = Field(
        description="2-4 sentences. What went wrong with the revised design and the "
        "EVIDENCE. Reference concrete signals: loss curve values from "
        "metrics.jsonl, errors and NaN flags from program.log, the "
        "prediction files it wrote into its attempt dir, results of any "
        "active probe you ran (forward pass on best_model.pth, etc.). 'metric=0' "
        "alone is not a diagnosis — what physical phenomenon caused it?"
    )
    revision_intent: str = Field(
        description="1-2 sentences. What this revision changes and what success "
        "looks like in one breath (e.g. 'Fix GradScaler NaN in fp16 path AND drop "
        "weighted-CE+sampler double-balancing; should restore loss decrease and "
        "recover the 0.37 baseline')."
    )
    recipe_changes: tuple[RecipeChange, ...] = Field(
        default_factory=tuple,
        description="Narrative edits to components of the revised design's effective "
        "design plan. Empty when the fix is purely code-level (use code_patches) "
        "or a continuation.",
    )
    code_patches: tuple[CodePatch, ...] = Field(
        default_factory=tuple,
        description="Exact find/replace patches applied to the revised design's "
        "training files before any recipe edits. Use when the failure is "
        "code-shaped — its checkpoint produces NaN logits, its script has a subtle "
        "bug. REQUIRED when diagnosis identifies a script-level defect; recipe "
        "edits alone will not fix a buggy script.",
    )
    expected_outcome: str = Field(
        description="1-2 sentences. What metric / qualitative signal you expect "
        "after applying this revision (e.g. 'loss curve should descend from 11.07 "
        "toward 8 within first 2000 steps; val macro-F1 should reach >= 0.30')."
    )
    requested_gpus: int = Field(
        default=0,
        ge=0,
        description="Number of GPUs this revision's training run requires. Zero "
        "runs on CPU. "
        "Restate it every revision (it is NOT inherited "
        "from the revised design) — re-pick by the revised model's scale within the "
        "0..M host range the prompt gives you. Zero uses no GPU.",
    )
    references: tuple[Citation, ...] = Field(
        default_factory=tuple,
        description="External published works adopted while diagnosing the revised "
        "design and forming this revision, each captured verbatim from an available "
        "sub-agent search result. Add one Citation per real, search-verified work "
        "this revision genuinely builds on; NEVER fabricate a reference. Empty "
        "when no external literature grounded this revision.",
    )

    @property
    def is_continuation(self) -> bool:
        """Return whether this proposal changes no recipe or code."""
        return not self.recipe_changes and not self.code_patches


# The scored candidate's own Output lives here rather than beside the Search
# because the Reviser is the one role that reads every field of it, and this
# is the lowest module the Judge, Selector, Coder and the Search all import.
class ScoredCandidateOutput(ScoreOutput):
    """The terminal success Output of one scored tree candidate.

    It IS the Score Program's own Output plus this package's extra business
    facts, so the score, the attempt directory and the components have one
    owner and are stored once. What a later Judge, Coder or Reviser needs
    about this candidate is here, which is why each of them is handed values
    rather than a directory to walk."""

    # The accepted source tree a revision of THIS candidate starts from.
    source_dir: str
    # The Training run's own directory, which holds that run's log beside the
    # attempt it produced: the address a Reviser reads to diagnose the run.
    training_dir: str
    # What this candidate's own Coder reported from its smoke run, before
    # Training: the one reading of this attempt that no file states.
    coder_feedback: str


@dataclass(frozen=True)
class ReviserInput(AgentInput):
    """What one Reviser call needs about the execution it is revising.

    The target's own facts, plus the lineage that produced it: the design this
    line started from and the revision intents actually carried out along it,
    oldest first. Every one of those is a fact the Search kept when it started
    that work, so this role is never asked to list the run's executions and
    read their workspaces to rebuild a history the Search already had."""

    # None means the user left the count to the Reviser.
    num_proposals: PositiveInt | None
    target_uid: str
    target_path: str
    # What that execution actually produced, exactly as it settled: its score
    # and the attempt directory the score was measured on, the source tree a
    # code patch must match, the Training run's own directory whose log sits
    # beside it, and what its Coder reported before Training. The Reviser reads
    # every one of them, so the accepted Output is nested rather than expanded
    # into a second projection of the same facts.
    target: ScoredCandidateOutput
    # The design this lineage started from, and the revisions that were
    # actually executed along it in order. A fresh root has an empty tuple.
    origin_plan: "DesignPlan"
    executed_revisions: tuple["RevisionProposal", ...]
    metric_contract: MetricContractInput | None
    remaining_minutes: float
    done_count: int
    # Advisory external results recorded so far, newest last. Same three-state
    # caveat as WorkerInput.external_scores: empty means sharing is off, nothing
    # was submitted yet, or nothing has returned -- never "it scored zero".
    external_scores: tuple[ExternalScore, ...]


class ReviserOutput(SuccessfulOutput):
    model_config = ConfigDict(
        json_schema_extra={
            "description": inspect.cleandoc(
                """Output from the Reviser: focused diffs for one completed design.

        Each proposal is a ``RevisionProposal``. The Coder applies the diff to inherited code."""
            )
        }
    )
    proposals: tuple[RevisionProposal, ...] = Field(
        description="List of revision proposals — each one a focused diff "
        "against the revised design's current source. The `num_proposals` "
        "instruction in your prompt says how many; submit an EMPTY list when the "
        "revised design's results have plateaued and its line is done."
    )
    feedback: str = Field(
        default="",
        description=(
            "Reviser-level rationale across the emitted proposals. Briefly "
            "state the saturation reading that drove this batch's "
            "directions, what axes you deliberately did NOT explore (so the "
            "next Reviser knows the unexplored space), and any cross-proposal "
            "trade-offs."
        ),
    )

    @model_validator(mode="after")
    def _proposals_are_usable(self, info: ValidationInfo) -> "ReviserOutput":
        """Refuse a proposal batch that is malformed on its own terms.

        A patch is an exact find/replace with no kind discriminator and no
        insert variant, so an empty ``find`` is an unusable patch rather than a
        patch of another kind. ``num_proposals`` forces exactly that
        many proposals per invocation, and 0 stays legal as the
        documented way to end this branch deliberately.

        Whether each ``find`` really occurs once in the revised source is a
        different question, because it is about files rather than about this
        answer; the Reviser's own verifier reads them.

        A persistence restore re-reads an Output the run already accepted and
        carries no input, so it stops at the context check."""
        empty = [
            patch.file
            for proposal in self.proposals
            for patch in proposal.code_patches
            if not patch.find
        ]
        if empty:
            raise ValueError(
                f"code_patch.find is empty for {empty}; a patch is an exact "
                "find/replace, so quote the EXACT non-empty substring the patch "
                "replaces"
            )
        if info.context is None:
            return self
        reviser_input = info.context.get("input")
        if not isinstance(reviser_input, ReviserInput):
            raise ValueError("the Reviser request is missing from validation context")
        wanted = reviser_input.num_proposals
        if wanted is not None and len(self.proposals) not in (0, wanted):
            raise ValueError(
                f"num_proposals={wanted} forces exactly {wanted} proposal(s) "
                f"per invocation (or 0 to deliberately end this branch); you "
                f"submitted {len(self.proposals)}."
            )
        return self


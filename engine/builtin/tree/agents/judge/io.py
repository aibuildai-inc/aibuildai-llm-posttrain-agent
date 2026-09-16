"""Input and Output records for the judge Agent family."""

from __future__ import annotations

import inspect
from dataclasses import dataclass
from pydantic import (
    ConfigDict,
    Field,
    ValidationInfo,
    field_serializer,
    field_validator,
    model_validator,
)

from engine.base import IGNORED_MODEL_ATTR_TYPES, WorkflowBaseModel
from engine.builtin.tree.agents.designer.io import DesignPlan
from engine.builtin.tree.agents.reviser.io import RevisionProposal
from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent.base import AgentInput
from engine.work_unit.agent.prompt import INTEGRITY_VIOLATIONS


@dataclass(frozen=True)
class IntegrityViolationInput:
    id: str
    rule: str


@dataclass(frozen=True)
class JudgeProposalInput:
    """One proposal put to the Judge, in the Judge's own request format.

    It carries the proposal itself, what the Action that WROTE it said about
    the batch it came in, and -- for a revision -- what its parent candidate
    actually ran and scored. Every one of those is a fact the Search already
    held when it built this request, so the Judge is never asked to open a
    directory to find out what it is judging against."""

    uid: str
    proposal: DesignPlan | RevisionProposal
    # The Designer's or Reviser's own words about the batch this proposal
    # arrived in: why these directions, and which axes were left alone.
    producer_feedback: str
    parent_uid: str | None
    # What the parent candidate itself ran, and the formal score the run's own
    # score program measured for it.
    parent_proposal: DesignPlan | RevisionProposal | None
    parent_metric: float | None
    # The parent's source tree and the attempt directory its score was
    # measured on: addresses for a Judge that wants to look, not the way it is
    # told what happened.
    parent_directory: str | None
    parent_output_dir: str | None


@dataclass(frozen=True)
class JudgeInput(AgentInput):
    proposals: tuple[JudgeProposalInput, ...]
    violations: tuple[IntegrityViolationInput, ...]


_REQUIRED_SCORE_DIMENSIONS = frozenset(
    {"expected_improvement", "change_rationale", "feasibility"}
)

# The one id vocabulary: derived from the live taxonomy, never hand-listed.
_INTEGRITY_VIOLATION_IDS = tuple(item["id"] for item in INTEGRITY_VIOLATIONS)


class ProposalScore(WorkflowBaseModel):
    model_config = ConfigDict(
        frozen=True,
        extra="forbid",
        ignored_types=IGNORED_MODEL_ATTR_TYPES,
        json_schema_extra={
            "description": inspect.cleandoc(
                """Judge's multi-dimensional score for a single proposed design.

                A design that triggered any integrity violation MUST be scored
                zero across every dimension, with the triggering canonical
                id(s) listed in `violations` and the reason recorded in
                `rationale`. The remaining dimension scores apply only when no
                violation fired."""
            )
        },
    )

    uid: str = Field(description="The proposed design being scored.")
    dimensions: tuple[tuple[str, int], ...] = Field(
        description=(
            "Integer scores (0-5 each): expected_improvement, "
            "change_rationale, feasibility. 0=none, 1=very low, 2=low, "
            "3=moderate, 4=high, 5=very high. All dimensions MUST be 0 "
            "when `violations` is non-empty."
        )
    )
    rationale: str = Field(
        description=(
            "Brief reasoning for the scores, grounded in evidence from the tree. "
            "When `violations` is non-empty, state the triggering violation "
            "reason here."
        )
    )
    violations: tuple[str, ...] = Field(
        default_factory=tuple,
        description=(
            "Canonical ids of any INTEGRITY_VIOLATIONS triggered by this "
            "proposal. Use exactly these ids: "
            + ", ".join(f"`{item}`" for item in _INTEGRITY_VIOLATION_IDS)
            + ". Empty list when no violation fired."
        ),
    )

    @field_validator("violations")
    @classmethod
    def _validate_violation_ids(cls, value: tuple[str, ...]) -> tuple[str, ...]:
        unknown = sorted(set(value) - set(_INTEGRITY_VIOLATION_IDS))
        if unknown:
            raise ValueError(
                f"unknown violation ids {unknown}; the canonical ids are "
                f"{sorted(_INTEGRITY_VIOLATION_IDS)}"
            )
        return value

    @field_validator(
        "dimensions",
        mode="before",
        json_schema_input_type=dict[str, int],
    )
    @classmethod
    def _validate_dimensions(cls, value: object) -> tuple[tuple[str, int], ...]:
        """Take the dimension scores exactly as answered, or refuse them.

        A judgement is what this role is FOR, so a score outside the stated
        scale, a fractional score, or a repeated dimension is refused back to
        the Judge instead of being rounded, clamped, or de-duplicated into a
        judgement the Judge never made."""
        entries = tuple(value.items()) if isinstance(value, dict) else tuple(value)  # type: ignore[arg-type]
        names = tuple(name for name, _ in entries)
        if len(names) != len(set(names)):
            raise ValueError("duplicate ProposalScore dimension names are not allowed")
        scored: list[tuple[str, int]] = []
        for name, score in entries:
            if isinstance(score, bool) or not isinstance(score, int):
                raise ValueError(
                    f"dimension {name!r} scored {score!r}; every dimension is a "
                    "whole number from 0 to 5"
                )
            if not 0 <= score <= 5:
                raise ValueError(
                    f"dimension {name!r} scored {score}, outside the 0-5 scale"
                )
            scored.append((name, score))
        return tuple(sorted(scored))

    @field_serializer("dimensions")
    def _serialize_dimensions(
        self, dimensions: tuple[tuple[str, int], ...]
    ) -> dict[str, int]:
        return dict(dimensions)

    @model_validator(mode="after")
    def _check_dimensions_contract(self) -> "ProposalScore":
        dimensions = dict(self.dimensions)
        keys = set(dimensions)
        if keys != _REQUIRED_SCORE_DIMENSIONS:
            missing = _REQUIRED_SCORE_DIMENSIONS - keys
            extra = keys - _REQUIRED_SCORE_DIMENSIONS
            raise ValueError(
                "ProposalScore.dimensions must have exactly the keys "
                f"{sorted(_REQUIRED_SCORE_DIMENSIONS)}; "
                f"missing={sorted(missing)}, extra={sorted(extra)}"
            )
        if self.violations and any(score != 0 for score in dimensions.values()):
            raise ValueError(
                f"ProposalScore for uid={self.uid} has violations="
                f"{self.violations} but non-zero dimensions={dimensions}; "
                "when violations is non-empty, every dimension must be 0"
            )
        return self


class JudgeOutput(SuccessfulOutput):
    model_config = ConfigDict(
        json_schema_extra={
            "description": "Output from the Judge agent — scores for a batch of proposed designs."
        }
    )
    scores: tuple[ProposalScore, ...] = Field(
        description="One score for each proposed design in the batch, and no others."
    )
    feedback: str = Field(
        default="",
        description=(
            "Per-batch summary across the scored designs: cross-design "
            "patterns (e.g. multiple proposals share the same suspect "
            "assumption), notable evidence sources used, and any concerns "
            "the Selector should weigh beyond the per-design `rationale` "
            "fields. Per-design rationale lives in `scores[].rationale`; "
            "this field is for batch-level observations only."
        ),
    )

    @model_validator(mode="after")
    def _scores_the_batch_it_was_asked_about(
        self, info: ValidationInfo
    ) -> "JudgeOutput":
        """Refuse a batch that is not a judgement of the proposals requested.

        A missing proposal, a repeated one, and a uid the request never
        mentioned are all the same defect: the answer is not a judgement of
        this batch. Each is failed back into the same Judge conversation,
        because a default score invented here would be recorded as a
        judgement the Judge never made -- and the Selector downstream cannot
        tell the two apart.

        A persistence restore re-reads an Output the run already accepted and
        carries no input, so it stops at the context check."""
        if info.context is None:
            return self
        judge_input = info.context.get("input")
        if not isinstance(judge_input, JudgeInput):
            raise ValueError("proposed designs missing from validation context")
        asked = [proposal.uid for proposal in judge_input.proposals]
        answered = [score.uid for score in self.scores]
        repeated = sorted({uid for uid in answered if answered.count(uid) > 1})
        unknown = sorted(set(answered) - set(asked))
        missing = sorted(set(asked) - set(answered))
        if repeated or unknown or missing:
            raise ValueError(
                "score exactly the proposed designs in this request, once "
                f"each: missing={missing}, repeated={repeated}, "
                f"unknown={unknown}. The proposed design UIDs are {asked}."
            )
        return self

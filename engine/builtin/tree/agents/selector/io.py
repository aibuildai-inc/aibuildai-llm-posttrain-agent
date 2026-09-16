"""Input and Output records for the selector Agent family."""

from __future__ import annotations

from dataclasses import dataclass
from pydantic import ConfigDict, Field, ValidationInfo, model_validator

from engine.builtin.tree.agents.designer.io import DesignPlan
from engine.builtin.tree.agents.reviser.io import RevisionProposal
from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent.base import AgentInput


@dataclass(frozen=True)
class SelectorReadyInput:
    """One judged proposal put to the Selector, in the Selector's own format.

    The proposal, the Judge's complete verdict on it, and where it came from.
    The Selector picks among the proposals in front of it; it is not handed
    the Search's executed tree."""

    uid: str
    proposal: DesignPlan | RevisionProposal
    parent_uid: str | None
    parent_metric: float | None
    parent_directory: str | None
    # The Judge's own accepted result for this proposal, unchanged: the three
    # dimensions it scored, why, and any integrity violation it found.
    score_dimensions: tuple[tuple[str, int], ...]
    score_rationale: str
    score_violations: tuple[str, ...]


@dataclass(frozen=True)
class SelectorInput(AgentInput):
    ready_proposals: tuple[SelectorReadyInput, ...]
    # What each Judge that scored these proposals said ABOUT the batch, in the
    # order those batches were judged. It is the Judge's cross-design reading,
    # which no single proposal's rationale carries, and it reaches the
    # Selector because the Search held it -- never by re-reading a transcript.
    judge_feedback: tuple[str, ...]
    # Composites this run has admitted, the measure ``max_evaluations`` caps: a
    # design counts from the moment its work launched, whether it ends DONE or
    # FAILED, so a failure occupies a slot the cap will not hand back.
    attempted_count: int
    remaining_minutes: float
    max_evaluations: int | None


class SelectorOutput(SuccessfulOutput):
    model_config = ConfigDict(
        json_schema_extra={
            "description": "Output from the Selector agent — which proposed designs to execute next."
        }
    )
    selected_uids: tuple[str, ...] = Field(
        description=(
            "Proposed design UIDs to execute next, in priority order. Rank every "
            "proposal worth executing, each at most once. Leave this empty only "
            "to end the search, when no proposal is worth executing."
        ),
    )
    feedback: str = Field(
        default="",
        description=(
            "Selection rationale: which proposed designs were picked and "
            "why, which were skipped and why (lower Judge score, "
            "redundant with peer, dead end, saturated direction). "
            "When the selection is empty, say why the search is over."
        ),
    )

    @model_validator(mode="after")
    def _check_answer(self, info: ValidationInfo) -> "SelectorOutput":
        """Refuse a selection this Search cannot execute as answered.

        A repeat is refused rather than de-duplicated: the answer is a
        priority ORDER over the proposals in front of the Selector, and one
        proposal named twice is an order the Selector did not mean. Silently
        keeping the first occurrence would hand the search a ranking nobody
        chose.

        A persistence restore (snapshot decode, per-event revalidation)
        re-reads an Output the run already accepted and has no input by
        construction, so it stops at the context check; every path that judges
        a fresh answer carries the input in its context."""
        if not self.selected_uids and not self.feedback.strip():
            raise ValueError(
                "ending the search requires feedback saying why no proposal is "
                "worth executing"
            )
        repeated = sorted(
            {uid for uid in self.selected_uids if self.selected_uids.count(uid) > 1}
        )
        if repeated:
            raise ValueError(
                f"selected_uids names {repeated} more than once; the answer is "
                "a priority order, so name each proposed design at most once."
            )
        if info.context is None:
            return self
        selector_input = info.context.get("input")
        if not isinstance(selector_input, SelectorInput):
            raise ValueError("proposed UIDs missing from validation context")
        ready_uids = {item.uid for item in selector_input.ready_proposals}
        invalid = sorted(uid for uid in self.selected_uids if uid not in ready_uids)
        if invalid:
            raise ValueError(
                f"selected_uids contains UIDs not in the request's "
                f"ready proposals: {invalid}. Valid design UIDs: "
                f"{sorted(ready_uids)}. Select only from the ready proposals."
            )
        return self

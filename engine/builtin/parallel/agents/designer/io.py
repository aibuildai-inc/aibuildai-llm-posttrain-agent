"""Input and Output records for the designer Agent family."""

from __future__ import annotations

import inspect
from dataclasses import dataclass

from pydantic import ConfigDict, Field, PositiveInt, ValidationInfo, model_validator

from engine.base import WorkflowBaseModel
from engine.execution_output import SuccessfulOutput
from engine.work_unit.agent.base import AgentInput


class Citation(WorkflowBaseModel):
    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "description": inspect.cleandoc(
                """One external published work adopted at DESIGN time, carried on the
        design payload as its bibliography.

        Captured when an Agent adopts a paper, model, or repository that an
        available sub-agent search returned and grounds
        a design choice on it. It travels with the design payload to the design
        paper's Related Work + bibliography and the run paper's references — the
        "search once at design time, cite forever" contract: the literature
        search done while designing IS the paper's bibliography, so a
        search-verified reference is never thrown away, re-searched, or
        hallucinated at write time. NEVER fabricate one: every field is copied
        verbatim from the sub-agent's real search result."""
            )
        },
    )
    title: str = Field(
        description="The work's exact title, copied verbatim from the sub-agent's "
        "search result (never paraphrased or guessed)."
    )
    authors: str = Field(
        description="Authors as the search returned them (e.g. 'Vaswani et al.' or "
        "the full list). Empty only if the source genuinely lists none."
    )
    year: str = Field(
        description="Publication year as text (e.g. '2017'). Text not int, so a "
        "preprint or a repository with no clean publication year still records "
        "exactly what the source gave."
    )
    identifier: str = Field(
        description="A stable id that resolves the work: an arXiv id (e.g. "
        "'1706.03762'), a DOI, a URL, an 'owner/repo', or a Hugging Face model id. "
        "Copied verbatim from the search result."
    )
    supports: str = Field(
        description="The specific model / technique / design choice in THIS design "
        "that this work grounds (e.g. 'the EfficientNet-B3 backbone', "
        "'the focal-loss class-imbalance handling')."
    )


class DesignPlan(WorkflowBaseModel):
    model_config = ConfigDict(
        frozen=True,
        json_schema_extra={
            "description": inspect.cleandoc(
                """A full, self-contained design plan. The Designer emits one per
        starting chain.

        A chain keeps running THIS plan: a later link states a
        ``WorkerRevision`` -- a direction against the design as it stands --
        rather than a fresh DesignPlan. See ``WorkerRevision`` in
        ``engine/builtin/parallel/agents/worker/io.py``."""
            )
        },
    )
    name: str = Field(description="Short descriptive name for this design.")
    architecture: str = Field(
        description="High-level blocks: backbone, neck, head, attention, temporal "
        "modeling, etc. Be specific and actionable (e.g. 'EfficientNet-B0', not "
        "'a CNN backbone')."
    )
    data_pipeline: str = Field(
        description="Preprocessing, augmentation, normalization, sampling strategy. "
        "Be specific and actionable (e.g. 'RandomCrop(224), HorizontalFlip, "
        "Normalize to ImageNet stats', not 'standard augmentations')."
    )
    loss_function: str = Field(
        description="What losses and why; class imbalance handling. Be specific "
        "and actionable (e.g. 'FocalLoss(alpha=0.25, gamma=2)', not 'a suitable "
        "loss')."
    )
    regularization: str = Field(
        description="Techniques (dropout, weight decay, label smoothing, etc.). Be "
        "specific and actionable (e.g. 'Dropout(0.3) after each block, "
        "weight_decay=1e-4', not 'some regularization')."
    )
    training_config: str = Field(
        description="Optimizer choice, LR schedule, batch sizing, mixed precision, "
        "gradient clipping, EMA, checkpointing. Be specific and actionable (e.g. "
        "'AdamW lr=3e-4, CosineAnnealing to 1e-6, batch=32, fp16', not 'standard "
        "training')."
    )
    validation_strategy: str = Field(
        description="How to read the task's held-out score: what the competition "
        "metric rewards, expected range, and error-analysis / ablation angles for "
        "the next link of the chain. Read it as guidance for the Worker, not "
        "as a prescription for an in-training validation loop: the design "
        "writes its output once and the task's own score program turns that "
        "output into the design's score afterwards."
    )
    method_steps: tuple[str, ...] = Field(
        description="Ordered list of high-level implementation steps."
    )
    requested_gpus: int = Field(
        default=0,
        ge=0,
        description="Number of GPUs the design's training work requires "
        "(the prompt tells you the host card count and the 0..M range). Pick by "
        "model scale: a small model wastes idle cards at a high count, a large "
        "model is starved at too low a count. Zero uses no GPU. The Worker that "
        "carries out this design claims exactly this count.",
    )
    design_notes: str = Field(
        default="",
        description="Design rationale: when to choose, risks, efficiency notes, "
        "expected performance, experiment plan.",
    )
    references: tuple[Citation, ...] = Field(
        default_factory=tuple,
        description="External published works adopted at design time that ground "
        "this plan's choices, each captured verbatim from an available sub-agent "
        "search result. Add one Citation per real, search-verified work this plan "
        "genuinely builds on; NEVER fabricate a reference. Empty when the design "
        "used no external literature. These become the paper's Related Work "
        "+ bibliography (search once at design time, cite forever).",
    )


@dataclass(frozen=True)
class DesignerInput(AgentInput):
    # None means the user left the count to the Designer.
    num_proposals: PositiveInt | None
    remaining_minutes: float
    done_count: int
    # The GPUs the owning Search may lend a design, which is the ceiling every
    # plan's request has to fit; None when the owning Search states no card
    # count, which bounds nothing.
    gpu_ceiling: int | None


class DesignerOutput(SuccessfulOutput):
    model_config = ConfigDict(
        json_schema_extra={
            "description": "Output from the Designer — a flat list of fresh design plans."
        }
    )
    plans: tuple[DesignPlan, ...] = Field(
        description="List of design plans. Each plan is a complete, "
        "self-contained DesignPlan with all fields filled in. Propose diverse "
        "approaches; the `num_proposals` instruction in your prompt says how "
        "many."
    )
    feedback: str = Field(
        default="",
        description=(
            "Design rationale across the proposed `plans`. Briefly state "
            "how each design differs from the others, what data/domain "
            "insight motivates each choice, and any axes you deliberately "
            "did NOT explore (so the next link of the chain knows the "
            "unexplored search space)."
        ),
    )

    @model_validator(mode="after")
    def _plans_fit_the_request(self, info: ValidationInfo) -> "DesignerOutput":
        """Refuse a plan set the owning Search cannot run.

        Both questions are about this answer against the request that asked
        for it, so both live here. ``num_proposals`` is documented to FORCE
        exactly that many plans, and the prompt only asks for the count; this
        is what makes the force real, so an off-count answer is failed back to
        the Designer rather than silently truncated or padded. A design that
        asks for more GPUs than the owning Search holds could never be
        launched.

        A persistence restore re-reads an Output the run already accepted and
        carries no input, so it stops at the context check."""
        if info.context is None:
            return self
        designer_input = info.context.get("input")
        if not isinstance(designer_input, DesignerInput):
            raise ValueError("the Designer request is missing from validation context")
        ceiling = designer_input.gpu_ceiling
        over = "; ".join(
            f"{plan.name!r} asked for {plan.requested_gpus}"
            for plan in self.plans
            if ceiling is not None and plan.requested_gpus > ceiling
        )
        if over:
            raise ValueError(
                f"The owning Search allows {ceiling} GPU(s), but a design "
                f"over-requests: {over}. requested_gpus must be an integer in "
                f"0..{ceiling}."
            )
        wanted = designer_input.num_proposals
        if wanted is not None and len(self.plans) != wanted:
            raise ValueError(
                f"num_proposals={wanted} forces exactly {wanted} design "
                f"plan(s); you submitted {len(self.plans)}."
            )
        return self

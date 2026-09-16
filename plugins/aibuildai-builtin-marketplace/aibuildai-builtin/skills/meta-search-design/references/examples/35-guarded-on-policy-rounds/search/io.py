"""Input of the guarded on-policy Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class GuardedOnPolicySearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement the seed and the rounds serve.")
    data_dir: str = Field(description="Read-only directory holding the raw task corpus.")
    eval_script: str = Field(description="Absolute path to the fixed, official evaluation script.")
    sample_script: str = Field(description="Absolute path to the script that samples the student and verifies each completion.")
    rft_module: str = Field(description="Module run as `python -m <rft_module>` to fine-tune on kept self-samples.")
    opd_module: str = Field(description="Module run as `python -m <opd_module>` to match the teacher's token distribution on self-samples.")
    teacher_model: str = Field(description="Absolute path or identifier of the teacher the distillation method scores against.")
    max_rounds: int = Field(default=4, ge=1, le=8, description="Hard ceiling on sample-train-guard rounds.")
    samples_per_prompt: int = Field(default=8, ge=1, le=64, description="Completions the student draws per prompt each round.")
    rft_min_yield: float = Field(
        default=0.15,
        gt=0.0,
        lt=1.0,
        description=(
            "Verified-sample fraction below which self-training has too little data to "
            "learn from, so the round distills against the teacher instead."
        ),
    )
    guard_margin: float = Field(
        default=0.005,
        ge=0.0,
        description=(
            "A round is accepted only when it beats the standing checkpoint by more than "
            "this much; size it above the evaluation's own noise."
        ),
    )
    max_rejected_rounds: int = Field(
        default=2,
        ge=1,
        le=4,
        description="Consecutive rejected rounds after which the loop stops instead of spending more budget.",
    )
    min_round_seconds: int = Field(
        default=2400,
        ge=60,
        description="A round starts only while the exploration remainder pays for one of this size.",
    )
    seed_wall_clock_seconds: int = Field(default=5400, ge=60)
    round_wall_clock_seconds: int = Field(
        default=3600,
        ge=60,
        description="Wall clock of one round role: its sampling, training, and evaluation together.",
    )

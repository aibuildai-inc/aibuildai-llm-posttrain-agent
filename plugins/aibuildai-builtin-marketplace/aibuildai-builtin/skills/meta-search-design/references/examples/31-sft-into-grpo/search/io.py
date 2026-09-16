"""Input of the SFT-into-GRPO Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class SFTIntoGRPOSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(
        description="The verbatim task statement, including the verifiable reward the scorer checks."
    )
    data_dir: str = Field(description="Read-only directory holding the task data and its scorer.")
    trainer_wall_clock_seconds: int = Field(
        default=21600,
        ge=60,
        description=(
            "One wall-clock grant for the trainer role, spent across BOTH of its calls: "
            "the sft call and the grpo call are two Actions of one identity, and an "
            "Action started after the grant is used up fails at once. Size it for both "
            "stages and their evaluations together; the exploration budget bounds it anyway."
        ),
    )
    grpo_round_seconds: int = Field(
        default=3600,
        ge=60,
        description=(
            "What the grpo call is expected to cost against the exploration's own remaining "
            "wall clock; it runs only while the live remainder from budget.snapshot() pays for it."
        ),
    )

"""Input of the checkpoint-tournament Search."""

from __future__ import annotations

from pydantic import BaseModel, ConfigDict, Field


class CheckpointTournamentSearchInput(BaseModel):
    """Business parameters of this Search; every field belongs to it alone."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    objective: str = Field(description="The verbatim task statement recon and training serve.")
    data_dir: str = Field(description="Read-only directory holding the raw task corpus.")
    eval_script: str = Field(description="Absolute path to the fixed, official evaluation script.")
    soup_script: str = Field(description="Absolute path to the script that averages checkpoint weights into one soup directory.")
    sft_module: str = Field(description="Module run as `python -m <sft_module>` to execute one SFT job.")
    max_rounds: int = Field(default=2, ge=1, le=6, description="Hard ceiling on train-tournament-soup rounds.")
    soup_k: int = Field(default=3, ge=2, le=6, description="Width of the adjacent checkpoint window souped.")
    min_round_seconds: int = Field(
        default=1800,
        ge=60,
        description="A round starts only while the exploration remainder pays for one of this size.",
    )
    recon_wall_clock_seconds: int = Field(default=1200, ge=60)
    round_wall_clock_seconds: int = Field(
        default=5400,
        ge=60,
        description="Wall clock of one round role: its training, every checkpoint evaluation, and the soup together.",
    )

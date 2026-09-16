"""The one role: it writes the scripts, runs SFT, then GRPO, and scores each stage itself."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import StageOutput, TrainerInput
from .policy import TRAINER_POLICY


class TrainerAgent(Agent[TrainerInput, StageOutput]):
    """Write sft_train.py, grpo_train.py, and eval.py; run the stage the message names; report its score."""

    name = "trainer"
    policy = TRAINER_POLICY
    prompt_template = "agent/trainer.j2"

"""Operation E: run the selector's frozen train.py once and report its metric."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import TrainerInput, TrainerOutput
from .policy import TRAINER_POLICY


class TrainerAgent(Agent[TrainerInput, TrainerOutput]):
    """Run the frozen train.py once with the selected features and read the metric."""

    name = "trainer"
    policy = TRAINER_POLICY
    prompt_template = "agent/trainer.j2"

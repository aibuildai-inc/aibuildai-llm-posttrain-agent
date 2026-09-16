"""The role that trains the chosen preference method, soups it with the parent, and scores both."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import TrainerInput, TrainerOutput
from .policy import TRAINER_POLICY


class TrainerAgent(Agent[TrainerInput, TrainerOutput]):
    """Train DPO or RAFT from the parent on the ranked data, evaluate it, soup it with the parent, evaluate the soup."""

    name = "trainer"
    policy = TRAINER_POLICY
    prompt_template = "agent/trainer.j2"

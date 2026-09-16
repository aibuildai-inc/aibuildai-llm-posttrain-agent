"""The role that estimates one non-terminal state's downstream value."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import RolloutInput, RolloutOutput
from .policy import READ_ONLY_POLICY


class RolloutAgent(Agent[RolloutInput, RolloutOutput]):
    """Judge how promising one path is without running further transitions."""

    name = "rollout"
    policy = READ_ONLY_POLICY
    prompt_template = "agent/rollout.j2"

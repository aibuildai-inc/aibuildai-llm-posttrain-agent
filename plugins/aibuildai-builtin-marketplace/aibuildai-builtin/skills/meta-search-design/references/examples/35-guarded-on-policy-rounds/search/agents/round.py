"""The role that runs one on-policy round: sample the standing checkpoint, train by yield, evaluate."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import RoundInput, RoundOutput
from .policy import ROUND_POLICY


class RoundAgent(Agent[RoundInput, RoundOutput]):
    """Sample the parent, pick RFT or on-policy distillation by the yield rule, train, and score the result."""

    name = "round"
    policy = ROUND_POLICY
    prompt_template = "agent/round.j2"

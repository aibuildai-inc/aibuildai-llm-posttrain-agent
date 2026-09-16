"""The role that runs one train-tournament-soup round on the card."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import RoundInput, RoundOutput
from .policy import ROUND_POLICY


class RoundAgent(Agent[RoundInput, RoundOutput]):
    """Train from the given checkpoint, score every checkpoint, soup the best window, report the winner."""

    name = "round"
    policy = ROUND_POLICY
    prompt_template = "agent/round.j2"

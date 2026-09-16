"""The role that diagnoses one round's failures and writes the targeted fix dataset."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import FixerInput, FixerOutput
from .policy import FIXER_POLICY


class FixerAgent(Agent[FixerInput, FixerOutput]):
    """Read each failed example, categorize it, and generate the synthetic fix plus a replay sample."""

    name = "fixer"
    policy = FIXER_POLICY
    prompt_template = "agent/fixer.j2"

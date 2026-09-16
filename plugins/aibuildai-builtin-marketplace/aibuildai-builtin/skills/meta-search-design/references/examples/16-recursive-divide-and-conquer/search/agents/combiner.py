"""The role that joins child summaries into one summary for the parent range."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import CombinerInput, CombinerOutput
from .policy import COMBINER_POLICY


class CombinerAgent(Agent[CombinerInput, CombinerOutput]):
    """Read every child summary and write one coherent parent summary."""

    name = "combiner"
    policy = COMBINER_POLICY
    prompt_template = "agent/combiner.j2"

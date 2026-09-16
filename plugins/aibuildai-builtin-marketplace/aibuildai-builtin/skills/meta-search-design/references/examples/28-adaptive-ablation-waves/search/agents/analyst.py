"""The role that synthesizes component evidence after the last wave."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import AnalystInput, AnalystOutput
from .policy import ANALYST_POLICY


class AnalystAgent(Agent[AnalystInput, AnalystOutput]):
    """Read the complete ledger and write the ablation report."""

    name = "analyst"
    policy = ANALYST_POLICY
    prompt_template = "agent/analyst.j2"

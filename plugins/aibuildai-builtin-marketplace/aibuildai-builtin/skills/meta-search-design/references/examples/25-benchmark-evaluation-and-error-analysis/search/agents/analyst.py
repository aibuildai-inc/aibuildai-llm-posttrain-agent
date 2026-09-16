"""The role that interprets the terminal benchmark evidence."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ErrorAnalysisInput, ErrorAnalysisOutput
from .policy import ANALYST_POLICY


class ErrorAnalysisAgent(Agent[ErrorAnalysisInput, ErrorAnalysisOutput]):
    """Read every record, including failures, and select the strongest successful one."""

    name = "error_analyst"
    policy = ANALYST_POLICY
    prompt_template = "agent/analyst.j2"

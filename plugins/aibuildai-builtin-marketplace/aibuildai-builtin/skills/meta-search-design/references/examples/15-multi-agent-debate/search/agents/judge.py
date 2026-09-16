"""The role that decides the debate from the final positions and their critiques."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import JudgeInput, JudgeOutput
from .policy import READ_ONLY_POLICY


class JudgeAgent(Agent[JudgeInput, JudgeOutput]):
    """Apply the fixed rubric to the final slate and rank the surviving positions."""

    name = "judge"
    policy = READ_ONLY_POLICY
    prompt_template = "agent/judge.j2"

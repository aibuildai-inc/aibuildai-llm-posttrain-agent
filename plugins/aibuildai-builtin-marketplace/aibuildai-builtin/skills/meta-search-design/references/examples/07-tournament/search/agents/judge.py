"""The one role every match runs: compare two contestants under one rubric."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import JudgeInput, JudgeOutput
from .policy import JUDGE_POLICY


class JudgeAgent(Agent[JudgeInput, JudgeOutput]):
    """Pick the stronger of two contestants, or declare the match tied."""

    name = "judge"
    policy = JUDGE_POLICY
    prompt_template = "agent/judge.j2"

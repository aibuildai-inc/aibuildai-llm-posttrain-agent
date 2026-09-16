"""The two judge roles: one measures the judge against the grader, a fresh one ranks candidates."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import CalibrationInput, CalibrationOutput, RankingInput, RankingOutput
from .policy import JUDGE_POLICY


class CalibrationAgent(Agent[CalibrationInput, CalibrationOutput]):
    """Score the graded pairs with the judge and report how often it agrees with the grader."""

    name = "calibration"
    policy = JUDGE_POLICY
    prompt_template = "agent/calibration.j2"


class RankingAgent(Agent[RankingInput, RankingOutput]):
    """Sample candidates from the parent and rank them with the judge into pairs and a best-only set."""

    name = "ranking"
    policy = JUDGE_POLICY
    prompt_template = "agent/ranking.j2"

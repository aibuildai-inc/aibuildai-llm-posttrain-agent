"""Operation D: reuse the profile alone to judge risks the selector never sees."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import QualityCheckInput, QualityCheckOutput
from .policy import QUALITY_POLICY


class QualityCheckAgent(Agent[QualityCheckInput, QualityCheckOutput]):
    """Judge whether the profile alone already rules out a trustworthy model."""

    name = "quality_check"
    policy = QUALITY_POLICY
    prompt_template = "agent/quality.j2"

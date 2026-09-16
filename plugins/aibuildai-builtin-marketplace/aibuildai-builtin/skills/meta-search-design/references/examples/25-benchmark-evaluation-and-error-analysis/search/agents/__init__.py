"""The two roles of the benchmark-evaluation Search."""

from __future__ import annotations

from .analyst import ErrorAnalysisAgent
from .planner import EvaluationPlanningAgent

__all__ = ["ErrorAnalysisAgent", "EvaluationPlanningAgent"]

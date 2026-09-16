"""The role that freezes the benchmark plan before any Program starts."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import EvaluationPlanningInput, EvaluationPlanOutput
from .policy import PLANNER_POLICY


class EvaluationPlanningAgent(Agent[EvaluationPlanningInput, EvaluationPlanOutput]):
    """Pick candidate/benchmark pairs, a primary metric, and requested slices; run nothing."""

    name = "evaluation_planner"
    policy = PLANNER_POLICY
    prompt_template = "agent/planner.j2"

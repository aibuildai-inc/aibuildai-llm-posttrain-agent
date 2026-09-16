"""The role that judges one version against the fixed rubric."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import EvaluatorInput, EvaluatorOutput
from .policy import EVALUATOR_POLICY


class EvaluatorAgent(Agent[EvaluatorInput, EvaluatorOutput]):
    """Accept the version, or name the concrete defects to fix."""

    name = "evaluator"
    policy = EVALUATOR_POLICY
    prompt_template = "agent/evaluator.j2"

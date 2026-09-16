"""The role that scores one candidate dataset under the fixed metric."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import EvaluatorInput, EvaluatorOutput
from .policy import EVALUATOR_POLICY


class EvaluatorAgent(Agent[EvaluatorInput, EvaluatorOutput]):
    """Score one dataset, flag it unviable, or call the pipeline complete."""

    name = "evaluator"
    policy = EVALUATOR_POLICY
    prompt_template = "agent/evaluator.j2"

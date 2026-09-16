"""Final join F: read D's concerns and E's tuned metric together."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import DecisionInput, DecisionOutput
from .policy import DECISION_POLICY


class FinalDecisionAgent(Agent[DecisionInput, DecisionOutput]):
    """Weigh the quality concerns against the tuned metric and accept or reject."""

    name = "final_decision"
    policy = DECISION_POLICY
    prompt_template = "agent/decision.j2"

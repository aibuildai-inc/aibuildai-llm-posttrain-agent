"""Stage B: carry out the plan and report the result."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ImplementerInput, ImplementerOutput
from .policy import IMPLEMENTER_POLICY


class ImplementerAgent(Agent[ImplementerInput, ImplementerOutput]):
    """Train and score exactly the model the plan describes."""

    name = "implementer"
    policy = IMPLEMENTER_POLICY
    prompt_template = "agent/implementer.j2"

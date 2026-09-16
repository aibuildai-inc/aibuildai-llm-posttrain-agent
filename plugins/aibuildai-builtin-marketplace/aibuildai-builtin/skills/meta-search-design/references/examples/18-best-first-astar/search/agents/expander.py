"""The role that expands one popped frontier state into candidate children."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ExpanderInput, ExpanderOutput
from .policy import EXPANDER_POLICY


class ExpanderAgent(Agent[ExpanderInput, ExpanderOutput]):
    """Propose up to branching_factor modifications of the parent pipeline."""

    name = "expander"
    policy = EXPANDER_POLICY
    prompt_template = "agent/expander.j2"

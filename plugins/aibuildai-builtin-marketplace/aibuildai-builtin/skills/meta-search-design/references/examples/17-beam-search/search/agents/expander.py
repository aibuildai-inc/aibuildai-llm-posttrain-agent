"""The role that proposes and applies bounded next transform steps."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ExpanderInput, ExpanderOutput
from .policy import EXPANDER_POLICY


class ExpanderAgent(Agent[ExpanderInput, ExpanderOutput]):
    """Apply up to max_children distinct next transforms to their own dataset copies."""

    name = "expander"
    policy = EXPANDER_POLICY
    prompt_template = "agent/expander.j2"

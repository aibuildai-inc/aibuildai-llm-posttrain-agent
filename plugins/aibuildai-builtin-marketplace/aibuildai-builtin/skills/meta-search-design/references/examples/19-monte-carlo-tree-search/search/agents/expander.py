"""The role that proposes untried actions from one tree state."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ExpanderInput, ExpanderOutput
from .policy import READ_ONLY_POLICY


class ExpanderAgent(Agent[ExpanderInput, ExpanderOutput]):
    """Propose distinct actions worth trying next, or none when the state is a dead end."""

    name = "expander"
    policy = READ_ONLY_POLICY
    prompt_template = "agent/expander.j2"

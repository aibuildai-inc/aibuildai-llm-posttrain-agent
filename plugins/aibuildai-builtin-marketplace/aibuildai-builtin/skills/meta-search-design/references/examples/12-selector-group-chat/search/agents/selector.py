"""The role that chooses the next speaker or requests finalization."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SelectorInput, SelectorOutput
from .policy import SELECTOR_POLICY


class SelectorAgent(Agent[SelectorInput, SelectorOutput]):
    """Pick one participant from the current public state, or stop the chat."""

    name = "selector"
    policy = SELECTOR_POLICY
    prompt_template = "agent/selector.j2"

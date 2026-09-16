"""Join C: read the profile and the baseline together and freeze the training source."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SelectorInput, SelectorOutput
from .policy import SELECTOR_POLICY


class SelectorAgent(Agent[SelectorInput, SelectorOutput]):
    """Choose a feature set from the profile and the baseline weights, then write train.py."""

    name = "selector"
    policy = SELECTOR_POLICY
    prompt_template = "agent/selector.j2"

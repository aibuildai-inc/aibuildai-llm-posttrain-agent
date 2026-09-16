"""The role that turns the public conversation into one written fix plan."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import FinalizerInput, FinalizerOutput
from .policy import FINALIZER_POLICY


class FinalizerAgent(Agent[FinalizerInput, FinalizerOutput]):
    """Consolidate the public conversation into one delivered fix plan."""

    name = "finalizer"
    policy = FINALIZER_POLICY
    prompt_template = "agent/finalizer.j2"

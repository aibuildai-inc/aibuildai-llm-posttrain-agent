"""The role that turns the accepted candidate into one written result."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import FinalizerInput, FinalizerOutput
from .policy import FINALIZER_POLICY


class FinalizerAgent(Agent[FinalizerInput, FinalizerOutput]):
    """Write the review-accepted candidate to durable storage with a score."""

    name = "finalizer"
    policy = FINALIZER_POLICY
    prompt_template = "agent/finalizer.j2"

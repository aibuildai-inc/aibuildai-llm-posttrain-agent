"""The role that combines several selected routes' reports into one result."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SynthesisInput, SynthesisOutput
from .policy import SYNTHESIS_POLICY


class SynthesisAgent(Agent[SynthesisInput, SynthesisOutput]):
    """Read every selected route's report and write one combined answer."""

    name = "synthesis"
    policy = SYNTHESIS_POLICY
    prompt_template = "agent/synthesis.j2"

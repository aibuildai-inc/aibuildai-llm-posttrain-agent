"""The role that writes the final root-cause report over every completed and failed assignment."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SynthesizerInput, SynthesizerOutput
from .policy import SYNTHESIZER_POLICY


class SynthesizerAgent(Agent[SynthesizerInput, SynthesizerOutput]):
    """Read the complete evidence and write the report; run nothing new."""

    name = "synthesizer"
    policy = SYNTHESIZER_POLICY
    prompt_template = "agent/synthesizer.j2"

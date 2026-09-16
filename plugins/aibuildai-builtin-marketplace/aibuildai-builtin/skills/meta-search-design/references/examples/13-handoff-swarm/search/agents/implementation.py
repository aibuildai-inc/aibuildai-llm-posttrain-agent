"""The role that writes one concrete change."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SpecialistInput, SpecialistOutput
from .policy import IMPLEMENTATION_POLICY


class ImplementationAgent(Agent[SpecialistInput, SpecialistOutput]):
    """Write one concrete change that addresses the current evidence."""

    name = "implementation"
    policy = IMPLEMENTATION_POLICY
    prompt_template = "agent/implementation.j2"

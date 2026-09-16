"""The role that adds grounded evidence from the task data."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SpecialistInput, SpecialistOutput
from .policy import RESEARCH_POLICY


class ResearchAgent(Agent[SpecialistInput, SpecialistOutput]):
    """Read the task data and report grounded evidence; do not propose a fix."""

    name = "research"
    policy = RESEARCH_POLICY
    prompt_template = "agent/research.j2"

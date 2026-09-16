"""The specialist for tasks about the data itself: shape, quality, statistics."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SpecialistInput, SpecialistOutput
from .policy import SPECIALIST_POLICY


class DataAnalysisAgent(Agent[SpecialistInput, SpecialistOutput]):
    """Profile and analyze the task data; report what the numbers show."""

    name = "data_analysis"
    policy = SPECIALIST_POLICY
    prompt_template = "agent/data_analysis.j2"

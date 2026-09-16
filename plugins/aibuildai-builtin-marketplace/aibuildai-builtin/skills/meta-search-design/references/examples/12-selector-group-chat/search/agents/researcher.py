"""The role that adds grounded evidence from the failing run's data."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ParticipantInput, ParticipantOutput
from .policy import RESEARCHER_POLICY


class ResearcherAgent(Agent[ParticipantInput, ParticipantOutput]):
    """Read the run's logs and config; report grounded evidence only."""

    name = "researcher"
    policy = RESEARCHER_POLICY
    prompt_template = "agent/researcher.j2"

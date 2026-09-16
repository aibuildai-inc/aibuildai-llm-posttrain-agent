"""The role that names concrete defects in the current evidence or proposal."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ParticipantInput, ParticipantOutput
from .policy import CRITIC_POLICY


class CriticAgent(Agent[ParticipantInput, ParticipantOutput]):
    """Name defects, or state that none remain; do not propose fixes."""

    name = "critic"
    policy = CRITIC_POLICY
    prompt_template = "agent/critic.j2"

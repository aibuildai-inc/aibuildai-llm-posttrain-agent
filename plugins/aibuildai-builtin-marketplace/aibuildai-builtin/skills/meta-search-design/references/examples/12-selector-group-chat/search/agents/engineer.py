"""The role that proposes one concrete, implementable fix."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ParticipantInput, ParticipantOutput
from .policy import ENGINEER_POLICY


class EngineerAgent(Agent[ParticipantInput, ParticipantOutput]):
    """Propose one exact change; do not gather new evidence or judge it."""

    name = "engineer"
    policy = ENGINEER_POLICY
    prompt_template = "agent/engineer.j2"

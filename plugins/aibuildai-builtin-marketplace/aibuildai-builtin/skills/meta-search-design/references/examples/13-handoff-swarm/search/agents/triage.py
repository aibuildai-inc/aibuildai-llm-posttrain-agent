"""The role that scopes the failure and picks the first specialist to act."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import SpecialistInput, SpecialistOutput
from .policy import TRIAGE_POLICY


class TriageAgent(Agent[SpecialistInput, SpecialistOutput]):
    """Scope the objective and hand off to the specialist best placed to act."""

    name = "triage"
    policy = TRIAGE_POLICY
    prompt_template = "agent/triage.j2"

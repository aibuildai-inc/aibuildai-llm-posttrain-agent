"""Operation A: describe the raw data, independent of every other role."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ProfileInput, ProfileOutput
from .policy import PROFILE_POLICY


class ProfileAgent(Agent[ProfileInput, ProfileOutput]):
    """Read the raw data once and report its shape and risky columns."""

    name = "profile"
    policy = PROFILE_POLICY
    prompt_template = "agent/profile.j2"

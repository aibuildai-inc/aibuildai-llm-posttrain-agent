"""The one role every committee member runs: cast one ballot on the shared slate."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import VoterInput, VoterOutput
from .policy import VOTER_POLICY


class VoterAgent(Agent[VoterInput, VoterOutput]):
    """Judge the shared slate under the fixed rubric and cast one independent ballot."""

    name = "voter"
    policy = VOTER_POLICY
    prompt_template = "agent/voter.j2"

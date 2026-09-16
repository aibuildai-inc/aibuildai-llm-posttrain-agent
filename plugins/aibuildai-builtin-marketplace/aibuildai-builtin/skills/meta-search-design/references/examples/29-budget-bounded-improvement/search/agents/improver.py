"""The role that produces or improves the one candidate lineage."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import ImproverInput, ImproverOutput
from .policy import IMPROVER_POLICY


class ImproverAgent(Agent[ImproverInput, ImproverOutput]):
    """Produce the first version, or improve the previous version."""

    name = "improver"
    policy = IMPROVER_POLICY
    prompt_template = "agent/improver.j2"

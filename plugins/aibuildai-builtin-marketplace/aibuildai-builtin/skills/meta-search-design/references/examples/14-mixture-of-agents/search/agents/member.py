"""One layer-1 member: propose a candidate under its own role and emphasis."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import MemberInput, MemberOutput
from .policy import MEMBER_POLICY


class MemberAgent(Agent[MemberInput, MemberOutput]):
    """Produce one candidate; other members cover the other approaches."""

    name = "member"
    policy = MEMBER_POLICY
    prompt_template = "agent/member.j2"

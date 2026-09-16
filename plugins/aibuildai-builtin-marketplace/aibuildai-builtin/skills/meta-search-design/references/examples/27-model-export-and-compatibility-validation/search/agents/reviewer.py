"""The role that interprets export and compatibility evidence."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import PackagingReviewInput, PackagingReviewOutput
from .policy import REVIEWER_POLICY


class PackagingReviewAgent(Agent[PackagingReviewInput, PackagingReviewOutput]):
    """Accept, ask for a revised plan, or stop; never override the hard gate."""

    name = "packaging_reviewer"
    policy = REVIEWER_POLICY
    prompt_template = "agent/reviewer.j2"

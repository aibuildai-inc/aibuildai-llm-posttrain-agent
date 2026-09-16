"""The mapper: review one declared package, independent of every other package."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import PackageReviewInput, PackageReviewOutput
from .policy import REVIEWER_POLICY


class PackageReviewerAgent(Agent[PackageReviewInput, PackageReviewOutput]):
    """Read one package and report its defects; never read or judge any other package."""

    name = "package_reviewer"
    policy = REVIEWER_POLICY
    prompt_template = "agent/reviewer.j2"

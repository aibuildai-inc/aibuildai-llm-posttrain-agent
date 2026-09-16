"""The role that chooses one bounded export and compatibility plan."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import PackagingStrategyInput, PackagingStrategyOutput
from .policy import STRATEGIST_POLICY


class PackagingStrategyAgent(Agent[PackagingStrategyInput, PackagingStrategyOutput]):
    """Pick target, precision, shape, options, and tolerance from the offered sets."""

    name = "packaging_strategist"
    policy = STRATEGIST_POLICY
    prompt_template = "agent/strategist.j2"

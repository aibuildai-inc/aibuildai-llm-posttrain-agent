"""The role that classifies the Input into one or more declared routes."""

from __future__ import annotations

from engine.work_unit.agent import Agent

from .io import RouterInput, RouterOutput
from .policy import ROUTER_POLICY


class RouterAgent(Agent[RouterInput, RouterOutput]):
    """Classify the objective; never solve it."""

    name = "router"
    policy = ROUTER_POLICY
    prompt_template = "agent/router.j2"

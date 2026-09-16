"""RouterAgent — per-role model routing decision (LLM)."""

from __future__ import annotations

from typing import TYPE_CHECKING, ClassVar

from engine.work_unit.agent.base import Agent
from engine.work_unit.agent.policy import RolePolicy
from engine.builtin.tree.agents.router.io import RouterInput, RouterOutput

if TYPE_CHECKING:
    from config import AgentConfig


ROUTER_POLICY = RolePolicy(
    tools=("Read", "Glob", "Grep", "WebSearch", "WebFetch", "mcp__*"),
)


class RouterAgent(Agent[RouterInput, RouterOutput]):
    """The role whose whole answer is a complete per-role model assignment.

    It runs no verifier: what makes a routing choice usable is a property of
    the answer against this Router's own Input, so ``RouterOutput`` states it
    and the schema gate enforces it before any child work could start."""

    name: ClassVar[str] = "router"
    prompt_template = "agent/router.j2"
    policy = ROUTER_POLICY

    @classmethod
    def enabled_in(cls, configs: "AgentConfig") -> bool:
        return configs.llm.router_on

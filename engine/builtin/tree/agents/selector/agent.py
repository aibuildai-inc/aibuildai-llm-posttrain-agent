"""SelectorAgent — names the proposed designs to execute next, in priority order. An empty answer ends the search.

Reads the run; the only place it may write is its own scratch dir. Selector WorkUnits belong to Search: each request appends a fresh one, and a resume reads the newest.
"""

from __future__ import annotations

from typing import ClassVar
from engine.work_unit.agent.base import Agent
from engine.work_unit.agent.policy import READONLY_TOOLS_WITH_SCRATCH, RolePolicy
from engine.builtin.tree.agents.selector.io import SelectorInput, SelectorOutput

SELECTOR_POLICY = RolePolicy(
    tools=tuple(READONLY_TOOLS_WITH_SCRATCH),
)


class SelectorAgent(Agent[SelectorInput, SelectorOutput]):
    name: ClassVar[str] = "selector"
    prompt_template = "agent/selector.j2"
    policy = SELECTOR_POLICY

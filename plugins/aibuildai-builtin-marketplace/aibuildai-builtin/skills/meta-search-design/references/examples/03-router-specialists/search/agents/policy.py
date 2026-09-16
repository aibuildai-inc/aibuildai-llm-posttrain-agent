"""Tool and directory policies of the router, the specialists, and the synthesizer."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

ROUTER_POLICY = RolePolicy(
    tools=("Read", "Grep"),
)

SPECIALIST_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
)

SYNTHESIS_POLICY = RolePolicy(
    tools=("Read", "Write", "Grep"),
)

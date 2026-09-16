"""Tool and directory policies: members write, the aggregator only reads."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

MEMBER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
)

AGGREGATOR_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

"""Tool and directory policies: the planner only reads, the implementer also writes."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

PLANNER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

IMPLEMENTER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
)

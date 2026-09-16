"""Tool and directory policies: the planner only reads offered facts; the analyst also writes a report."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

PLANNER_POLICY = RolePolicy(
    tools=("Read", "Glob", "Grep"),
)

ANALYST_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Glob", "Grep"),
)

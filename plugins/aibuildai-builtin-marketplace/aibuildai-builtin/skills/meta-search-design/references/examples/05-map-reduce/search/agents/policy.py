"""Tool and directory policies: the reviewer only reads, the reducer also writes."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

REVIEWER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

REDUCER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Glob", "Grep"),
)

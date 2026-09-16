"""Tool and directory policies: debaters write their proposals, critics and the judge only read."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

DEBATER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
)

READ_ONLY_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

"""Tool and directory policy: the improver reads the task and writes its version."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

IMPROVER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
)

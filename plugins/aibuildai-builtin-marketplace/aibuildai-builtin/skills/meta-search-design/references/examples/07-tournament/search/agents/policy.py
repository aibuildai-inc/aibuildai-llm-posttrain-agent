"""The tool and directory policy every judge runs under: read-only comparison."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

JUDGE_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

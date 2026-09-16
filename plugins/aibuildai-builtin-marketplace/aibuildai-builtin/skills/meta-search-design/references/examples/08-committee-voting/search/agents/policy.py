"""The tool and directory policy every voter runs under: read-only comparison."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

VOTER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

"""The tool and directory policy every candidate runs under."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

CANDIDATE_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
)

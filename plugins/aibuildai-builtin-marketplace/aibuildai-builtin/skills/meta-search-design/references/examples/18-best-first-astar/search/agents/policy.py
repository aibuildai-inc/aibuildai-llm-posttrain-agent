"""Tool and directory policies of the two roles; neither writes artifacts."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

EXPANDER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

HEURISTIC_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

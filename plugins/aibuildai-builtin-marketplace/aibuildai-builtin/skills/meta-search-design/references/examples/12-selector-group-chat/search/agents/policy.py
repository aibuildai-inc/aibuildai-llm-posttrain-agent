"""Tool and directory policies of the five roles."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

SELECTOR_POLICY = RolePolicy(
    tools=("Read",),
)

RESEARCHER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

ENGINEER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

CRITIC_POLICY = RolePolicy(
    tools=("Read", "Glob", "Grep"),
)

FINALIZER_POLICY = RolePolicy(
    tools=("Read", "Write", "Glob", "Grep"),
)

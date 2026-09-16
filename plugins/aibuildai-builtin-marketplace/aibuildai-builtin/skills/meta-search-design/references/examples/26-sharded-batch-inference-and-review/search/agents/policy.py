"""Tool and directory policies of the two roles."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

STRATEGY_POLICY = RolePolicy(
    tools=("Read", "Grep"),
)

REVIEW_POLICY = RolePolicy(
    tools=("Bash", "Read", "Grep"),
)

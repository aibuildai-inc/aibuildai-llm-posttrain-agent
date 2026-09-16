"""Tool and directory policies: the expander writes datasets, the evaluator only reads."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

EXPANDER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
)

EVALUATOR_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

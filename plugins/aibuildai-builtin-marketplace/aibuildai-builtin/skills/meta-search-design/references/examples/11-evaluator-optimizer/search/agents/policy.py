"""Tool and directory policies: the producer writes, the evaluator only reads."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

PRODUCER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
)

EVALUATOR_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

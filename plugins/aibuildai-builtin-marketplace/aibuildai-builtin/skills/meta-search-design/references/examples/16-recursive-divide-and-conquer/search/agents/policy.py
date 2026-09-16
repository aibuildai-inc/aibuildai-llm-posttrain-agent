"""Tool and directory policies: only the combiner writes the joined artifact."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

SOLVER_POLICY = RolePolicy(
    tools=("Read", "Glob", "Grep"),
)

DECOMPOSER_POLICY = RolePolicy(
    tools=("Read", "Glob", "Grep"),
)

COMBINER_POLICY = RolePolicy(
    tools=("Read", "Glob", "Grep"),
)

"""Tool and directory policies of the four specialists and the finalizer."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

TRIAGE_POLICY = RolePolicy(
    tools=("Read", "Glob", "Grep"),
)

RESEARCH_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

IMPLEMENTATION_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Glob", "Grep"),
)

REVIEW_POLICY = RolePolicy(
    tools=("Read", "Glob", "Grep"),
)

FINALIZER_POLICY = RolePolicy(
    tools=("Read", "Write", "Glob", "Grep"),
)

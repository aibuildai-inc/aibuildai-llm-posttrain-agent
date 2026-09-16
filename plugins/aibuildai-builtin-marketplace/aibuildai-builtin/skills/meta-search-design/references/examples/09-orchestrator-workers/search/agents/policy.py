"""Tool and directory policies of the three roles."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

ORCHESTRATOR_POLICY = RolePolicy(
    tools=("Read", "Glob", "Grep"),
)

WORKER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Glob", "Grep"),
)

SYNTHESIZER_POLICY = RolePolicy(
    tools=("Read", "Write", "Glob", "Grep"),
)

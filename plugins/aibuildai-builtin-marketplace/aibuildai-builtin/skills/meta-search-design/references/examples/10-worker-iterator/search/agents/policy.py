"""Tool and directory policies: the worker probes scratch, the iterator files a final report."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

WORKER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

ITERATOR_POLICY = RolePolicy(
    tools=("Read", "Grep"),
)

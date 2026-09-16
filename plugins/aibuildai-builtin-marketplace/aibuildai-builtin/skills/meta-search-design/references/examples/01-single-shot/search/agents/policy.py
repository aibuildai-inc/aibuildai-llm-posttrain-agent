"""The tool and directory policy the modeler runs under."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

MODELER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
)

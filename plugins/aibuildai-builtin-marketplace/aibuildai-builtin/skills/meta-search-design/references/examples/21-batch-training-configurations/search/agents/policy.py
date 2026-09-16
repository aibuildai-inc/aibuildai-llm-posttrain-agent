"""The tool and directory policy the designer runs under."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy, SystemDir

DESIGNER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
)

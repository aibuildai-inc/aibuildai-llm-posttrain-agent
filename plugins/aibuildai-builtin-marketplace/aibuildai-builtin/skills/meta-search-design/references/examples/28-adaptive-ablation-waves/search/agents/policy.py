"""Tool and directory policies of the two roles."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy, SystemDir

ORCHESTRATOR_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
)

ANALYST_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Glob", "Grep"),
)

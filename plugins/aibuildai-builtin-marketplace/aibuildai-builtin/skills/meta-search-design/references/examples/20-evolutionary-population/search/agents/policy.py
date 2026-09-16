"""Tool and directory policies: the initializer writes the fixed source, mutation only reads."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy, SystemDir

INITIALIZER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
)

MUTATION_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

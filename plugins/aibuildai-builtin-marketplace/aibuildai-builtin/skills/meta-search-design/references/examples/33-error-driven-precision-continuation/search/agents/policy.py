"""Tool and directory policies: the worker trains and evaluates on the card; the fixer reads its failures."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy, SystemDir

# The worker does every training and evaluation itself: the task Conda env's
# python is first on its PATH (task_environment) and its Search grants it the
# card (gpu_count=1).
WORKER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
    task_environment=True,
)

FIXER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Glob", "Grep"),
)

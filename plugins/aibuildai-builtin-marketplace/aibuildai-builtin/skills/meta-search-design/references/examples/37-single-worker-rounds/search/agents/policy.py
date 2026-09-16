"""Tool and directory policy: the worker does the whole round in the task environment, on the card."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy, SystemDir

# task_environment puts the task Conda env's python first on the worker's PATH;
# the Search grants it the card with gpu_count=1. The worker reads the run
# workspace so a later round can open what an earlier round wrote.
WORKER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
    task_environment=True,
)

"""Tool and directory policy: the trainer writes, runs, repairs, and scores its own scripts on the card."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy, SystemDir

# One role does the whole job: the task Conda env's python is first on its PATH
# (task_environment) and its Search grants it the card (gpu_count=1).
TRAINER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
    task_environment=True,
)

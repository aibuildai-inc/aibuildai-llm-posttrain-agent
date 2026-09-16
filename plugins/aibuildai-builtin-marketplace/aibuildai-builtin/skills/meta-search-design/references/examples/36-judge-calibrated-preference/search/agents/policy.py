"""Tool and directory policies: every role runs on the card in the task environment; the judges are fresh."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy, SystemDir

# The task Conda env's python is first on each role's PATH (task_environment)
# and the Search grants each the card (gpu_count=1). Judge roles read the run
# workspace to open the parent's artifacts; they never write training data.
PARENT_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
    task_environment=True,
)

JUDGE_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
    task_environment=True,
)

TRAINER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
    task_environment=True,
)

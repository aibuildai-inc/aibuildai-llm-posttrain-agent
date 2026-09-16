"""Tool and directory policies: both roles run on the card in the task environment."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy, SystemDir

# Both roles train and evaluate themselves: the task Conda env's python is first
# on their PATH (task_environment) and their Search grants them the card
# (gpu_count=1). The round role reads the run workspace to open the standing
# checkpoint an earlier role wrote.
SEED_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
    task_environment=True,
)

ROUND_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
    task_environment=True,
)

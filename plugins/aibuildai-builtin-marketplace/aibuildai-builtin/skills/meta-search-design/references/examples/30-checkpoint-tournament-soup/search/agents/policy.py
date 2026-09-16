"""Tool and directory policies: recon writes the data policy; the round role trains on the card."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy, SystemDir

RECON_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
)

# The round role runs training, evaluation, and souping itself: the task Conda
# env's python is first on its PATH (task_environment) and its Search grants it
# the card (gpu_count=1). It reads the run workspace so a later round can open an
# earlier round's checkpoints.
ROUND_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Glob", "Grep"),
    system_read=(SystemDir.CONDA_PACKAGES,),
    task_environment=True,
)

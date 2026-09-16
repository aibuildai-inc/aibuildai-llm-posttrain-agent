"""Tool and directory policies: two read-only reasoning roles, one trial role on the card."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

READ_ONLY_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

# The trial role runs the fixed train.py itself: the task Conda env's python is
# first on its PATH (task_environment) and its Search grants it the card.
TRIAL_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
    task_environment=True,
)

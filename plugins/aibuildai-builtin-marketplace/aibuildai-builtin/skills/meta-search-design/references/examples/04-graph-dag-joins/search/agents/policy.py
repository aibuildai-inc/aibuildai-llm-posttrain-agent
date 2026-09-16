"""Tool and directory policies of the six roles."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

PROFILE_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

BASELINE_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
)

SELECTOR_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Edit", "Glob", "Grep"),
)

QUALITY_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

DECISION_POLICY = RolePolicy(
    tools=("Bash", "Read", "Write", "Glob", "Grep"),
)

# The trainer runs train.py itself: the task Conda env's python is first on its
# PATH (task_environment) and its Search grants it the card (gpu_count=1).
TRAINER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
    task_environment=True,
)

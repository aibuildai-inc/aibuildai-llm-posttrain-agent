"""Tool and directory policies of the two roles; neither writes an artifact directly."""

from __future__ import annotations

from engine.work_unit.agent import RolePolicy

STRATEGIST_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

REVIEWER_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
)

# Export and validation run the fixed toolchain themselves: the task Conda env's
# python is first on their PATH (task_environment); the Search grants the card to
# the export and to a CUDA validation, none to a CPU one.
EXPORT_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
    task_environment=True,
)

VALIDATION_POLICY = RolePolicy(
    tools=("Bash", "Read", "Glob", "Grep"),
    task_environment=True,
)

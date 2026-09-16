"""The universal Search family: the public authoring surface.

A Search is an orchestration abstraction, not a gateway to the live product
root. What an author gets is here: the family itself, the replay-safe budget
reader its control flow may branch on, and what it receives about work it
delegated to an owner whose Actions it did not spawn itself. The AIBuildAI
product shell, the built-in packages and the kind-to-package map are not
authoring names and are imported from their own modules by the startup path
that needs them.
"""

from engine.search.base import (
    BudgetSnapshot,
    ExecutionBudgetSnapshot,
    RecordedAction,
    Search,
    SearchBudget,
    run_search,
)

# The public Search surface.
__all__ = [
    "BudgetSnapshot",
    "ExecutionBudgetSnapshot",
    "RecordedAction",
    "Search",
    "SearchBudget",
    "run_search",
]

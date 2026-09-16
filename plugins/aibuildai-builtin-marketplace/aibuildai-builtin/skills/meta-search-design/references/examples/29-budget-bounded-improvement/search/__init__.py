"""Budget-bounded improvement Search: repeat rounds while the budget pays for one."""

from __future__ import annotations

from .search import BudgetBoundedImprovementSearch

SEARCH_TYPE = BudgetBoundedImprovementSearch

__all__ = ["SEARCH_TYPE", "BudgetBoundedImprovementSearch"]

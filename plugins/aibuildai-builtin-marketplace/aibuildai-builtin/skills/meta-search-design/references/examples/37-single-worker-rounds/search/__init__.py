"""Single-worker rounds: one Agent on the card does a whole round; the Search loops on the budget."""

from __future__ import annotations

from .search import SingleWorkerRoundsSearch

SEARCH_TYPE = SingleWorkerRoundsSearch

__all__ = ["SEARCH_TYPE", "SingleWorkerRoundsSearch"]

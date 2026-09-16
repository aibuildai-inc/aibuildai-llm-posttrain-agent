"""Worker-Iterator Search: one bounded diagnostic action per round, chosen by the iterator."""

from __future__ import annotations

from .search import WorkerIteratorSearch

SEARCH_TYPE = WorkerIteratorSearch

__all__ = ["SEARCH_TYPE", "WorkerIteratorSearch"]

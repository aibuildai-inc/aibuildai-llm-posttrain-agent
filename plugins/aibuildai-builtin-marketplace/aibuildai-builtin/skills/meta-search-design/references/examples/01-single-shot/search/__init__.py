"""Single-shot Search: one modeling role turns the task into one scored result."""

from __future__ import annotations

from .search import SingleShotSearch

SEARCH_TYPE = SingleShotSearch

__all__ = ["SEARCH_TYPE", "SingleShotSearch"]

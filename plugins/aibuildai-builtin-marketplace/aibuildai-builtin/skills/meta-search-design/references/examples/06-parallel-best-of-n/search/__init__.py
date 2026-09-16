"""Parallel best-of-N Search: N independent modelers, one winner by score."""

from __future__ import annotations

from .search import BestOfNSearch

SEARCH_TYPE = BestOfNSearch

__all__ = ["SEARCH_TYPE", "BestOfNSearch"]

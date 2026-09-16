"""Heuristic best-first Search: expand the cheapest-so-far pipeline state first."""

from __future__ import annotations

from .search import HeuristicBestFirstSearch

SEARCH_TYPE = HeuristicBestFirstSearch

__all__ = ["SEARCH_TYPE", "HeuristicBestFirstSearch"]

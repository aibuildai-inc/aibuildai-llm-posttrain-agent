"""Monte Carlo Tree Search Search: repeated selection, expansion, simulation, and backup."""

from __future__ import annotations

from .search import MonteCarloTreeSearch

SEARCH_TYPE = MonteCarloTreeSearch

__all__ = ["SEARCH_TYPE", "MonteCarloTreeSearch"]

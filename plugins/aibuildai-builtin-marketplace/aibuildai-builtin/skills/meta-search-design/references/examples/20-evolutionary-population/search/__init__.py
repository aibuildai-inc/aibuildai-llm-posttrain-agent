"""Evolutionary Search: a population of frozen configurations, mutated across generations."""

from __future__ import annotations

from .search import EvolutionarySearch

SEARCH_TYPE = EvolutionarySearch

__all__ = ["SEARCH_TYPE", "EvolutionarySearch"]

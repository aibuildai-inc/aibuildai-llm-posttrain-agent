"""Mixture-of-Agents Search: heterogeneous proposers, one Aggregator picks the winner."""

from __future__ import annotations

from .search import MixtureOfAgentsSearch

SEARCH_TYPE = MixtureOfAgentsSearch

__all__ = ["SEARCH_TYPE", "MixtureOfAgentsSearch"]

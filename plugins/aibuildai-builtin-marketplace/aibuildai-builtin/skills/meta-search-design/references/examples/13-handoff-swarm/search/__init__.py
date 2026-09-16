"""Handoff swarm Search: the active specialist itself picks the next specialist."""

from __future__ import annotations

from .search import HandoffSwarmSearch

SEARCH_TYPE = HandoffSwarmSearch

__all__ = ["SEARCH_TYPE", "HandoffSwarmSearch"]

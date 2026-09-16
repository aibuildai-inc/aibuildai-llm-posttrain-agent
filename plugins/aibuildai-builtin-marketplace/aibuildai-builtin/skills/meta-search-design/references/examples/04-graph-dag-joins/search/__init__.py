"""Dependency-graph Search: two sources join, one branch reuses a source, two sinks join again."""

from __future__ import annotations

from .search import GraphDagJoinsSearch

SEARCH_TYPE = GraphDagJoinsSearch

__all__ = ["SEARCH_TYPE", "GraphDagJoinsSearch"]

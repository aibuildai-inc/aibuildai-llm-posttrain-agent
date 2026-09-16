"""Router and specialists Search: classify once, run only the selected routes."""

from __future__ import annotations

from .search import RouterSpecialistsSearch

SEARCH_TYPE = RouterSpecialistsSearch

__all__ = ["SEARCH_TYPE", "RouterSpecialistsSearch"]

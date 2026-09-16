"""Tournament Search: single-elimination bracket over an existing candidate set."""

from __future__ import annotations

from .search import TournamentSearch

SEARCH_TYPE = TournamentSearch

__all__ = ["SEARCH_TYPE", "TournamentSearch"]

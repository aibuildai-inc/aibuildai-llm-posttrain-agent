"""Precision-continuation Search: one worker trains and continues; the fixer targets its failures."""

from __future__ import annotations

from .search import PrecisionSearch

SEARCH_TYPE = PrecisionSearch

__all__ = ["SEARCH_TYPE", "PrecisionSearch"]

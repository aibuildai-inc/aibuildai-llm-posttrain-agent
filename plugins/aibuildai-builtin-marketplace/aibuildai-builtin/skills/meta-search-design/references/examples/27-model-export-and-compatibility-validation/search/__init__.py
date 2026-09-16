"""Model export and compatibility Search: one plan, one export, N compatibility checks, reviewed."""

from __future__ import annotations

from .search import ExportCompatibilitySearch

SEARCH_TYPE = ExportCompatibilitySearch

__all__ = ["SEARCH_TYPE", "ExportCompatibilitySearch"]

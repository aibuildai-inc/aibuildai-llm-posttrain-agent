"""Batch-training Search: one designer freezes N configurations, N Programs train them."""

from __future__ import annotations

from .search import BatchTrainingSearch

SEARCH_TYPE = BatchTrainingSearch

__all__ = ["SEARCH_TYPE", "BatchTrainingSearch"]

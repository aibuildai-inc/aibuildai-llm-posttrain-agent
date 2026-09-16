"""Map-reduce Search: one reviewer per declared package, one reducer combines them."""

from __future__ import annotations

from .search import CodeReviewMapReduceSearch

SEARCH_TYPE = CodeReviewMapReduceSearch

__all__ = ["SEARCH_TYPE", "CodeReviewMapReduceSearch"]

"""Evaluator-optimizer Search: one candidate lineage refined under a fixed rubric."""

from __future__ import annotations

from .search import EvaluatorOptimizerSearch

SEARCH_TYPE = EvaluatorOptimizerSearch

__all__ = ["SEARCH_TYPE", "EvaluatorOptimizerSearch"]

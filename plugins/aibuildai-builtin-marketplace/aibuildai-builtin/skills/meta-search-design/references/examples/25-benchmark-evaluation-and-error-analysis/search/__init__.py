"""Benchmark-evaluation Search: one planner freezes N targets, N Programs score them, one analyst interprets."""

from __future__ import annotations

from .search import BenchmarkAnalysisSearch

SEARCH_TYPE = BenchmarkAnalysisSearch

__all__ = ["SEARCH_TYPE", "BenchmarkAnalysisSearch"]

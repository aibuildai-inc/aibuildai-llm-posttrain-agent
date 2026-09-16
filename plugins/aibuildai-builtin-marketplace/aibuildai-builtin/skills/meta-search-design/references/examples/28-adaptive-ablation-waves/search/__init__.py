"""Adaptive-ablation Search: an orchestrator plans bounded waves of frozen cells."""

from __future__ import annotations

from .search import AdaptiveAblationSearch

SEARCH_TYPE = AdaptiveAblationSearch

__all__ = ["SEARCH_TYPE", "AdaptiveAblationSearch"]

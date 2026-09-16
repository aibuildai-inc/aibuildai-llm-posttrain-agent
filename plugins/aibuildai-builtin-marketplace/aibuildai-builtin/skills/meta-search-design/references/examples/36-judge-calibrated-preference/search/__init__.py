"""Judge-calibrated preference Search: measure the judge, then branch on what it measured."""

from __future__ import annotations

from .search import JudgeCalibratedPreferenceSearch

SEARCH_TYPE = JudgeCalibratedPreferenceSearch

__all__ = ["SEARCH_TYPE", "JudgeCalibratedPreferenceSearch"]

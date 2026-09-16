"""Beam search Search: a width-bounded frontier of feature-pipeline states."""

from __future__ import annotations

from .search import BeamSearch

SEARCH_TYPE = BeamSearch

__all__ = ["SEARCH_TYPE", "BeamSearch"]

"""Orchestrator-workers Search: an orchestrator plans bounded waves of typed assignments."""

from __future__ import annotations

from .search import OrchestratorWorkersSearch

SEARCH_TYPE = OrchestratorWorkersSearch

__all__ = ["SEARCH_TYPE", "OrchestratorWorkersSearch"]

"""Checkpoint-tournament Search: one round role per round trains, scores, and soups."""

from __future__ import annotations

from .search import CheckpointTournamentSearch

SEARCH_TYPE = CheckpointTournamentSearch

__all__ = ["SEARCH_TYPE", "CheckpointTournamentSearch"]

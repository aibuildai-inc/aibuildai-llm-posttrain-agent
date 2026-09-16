"""Guarded on-policy Search: one seed role, then one round role per round, guarded by the Search."""

from __future__ import annotations

from .search import GuardedOnPolicySearch

SEARCH_TYPE = GuardedOnPolicySearch

__all__ = ["SEARCH_TYPE", "GuardedOnPolicySearch"]

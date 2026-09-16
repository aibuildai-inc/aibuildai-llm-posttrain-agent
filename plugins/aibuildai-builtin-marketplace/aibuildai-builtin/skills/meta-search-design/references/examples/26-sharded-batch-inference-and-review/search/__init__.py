"""Sharded-inference Search: a strategy Agent freezes a policy, Programs apply it per shard."""

from __future__ import annotations

from .search import ShardedInferenceSearch

SEARCH_TYPE = ShardedInferenceSearch

__all__ = ["SEARCH_TYPE", "ShardedInferenceSearch"]

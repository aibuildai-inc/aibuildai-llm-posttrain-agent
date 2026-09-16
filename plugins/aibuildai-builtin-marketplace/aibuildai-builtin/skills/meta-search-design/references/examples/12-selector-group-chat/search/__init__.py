"""Selector group chat Search: one Selector repeatedly picks the next speaker."""

from __future__ import annotations

from .search import SelectorGroupChatSearch

SEARCH_TYPE = SelectorGroupChatSearch

__all__ = ["SEARCH_TYPE", "SelectorGroupChatSearch"]

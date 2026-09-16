"""SFT-into-GRPO Search: one trainer role runs SFT, then GRPO, and scores both."""

from __future__ import annotations

from .search import SFTIntoGRPOSearch

SEARCH_TYPE = SFTIntoGRPOSearch

__all__ = ["SEARCH_TYPE", "SFTIntoGRPOSearch"]

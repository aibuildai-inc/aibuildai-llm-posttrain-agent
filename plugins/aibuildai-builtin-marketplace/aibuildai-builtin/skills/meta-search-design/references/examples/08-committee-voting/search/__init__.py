"""Committee-voting Search: independent ballots over one slate, tallied deterministically."""

from __future__ import annotations

from .search import CommitteeVotingSearch

SEARCH_TYPE = CommitteeVotingSearch

__all__ = ["SEARCH_TYPE", "CommitteeVotingSearch"]

"""The shared AIBuildAI product package.

What every AIBuildAI run has whatever algorithm it selected: the product
Search shell and its fixed lifecycle, the Definitions that lifecycle owns
(Setup, Submitter, Finalizer, the whole-run Writer and the Score Program), and
the two Output contracts the product speaks.

The names re-exported here are the ones a Search written OUTSIDE the product --
a package a MetaAgent generates -- may use: its top-level Search returns the
same ``SearchOutput`` a built-in package returns, and the product shell turns
that into the user's ``SearchResult``.
"""

from engine.builtin.aibuildai.io import (
    DeliveryRecord,
    SearchOutput,
    SearchResult,
    task_data,
)

__all__ = [
    "DeliveryRecord",
    "SearchOutput",
    "SearchResult",
    "task_data",
]

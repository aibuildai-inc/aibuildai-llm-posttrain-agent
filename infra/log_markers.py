"""Shared marker for aibuildai logging.

The logging bootstrap is two-phase and the two phases live in different layers:

- Phase 1 (infra) -- ``ProcessLifecycle.__enter__`` installs an early stderr handler before config is loaded, so warnings emitted during startup are still visible.
- Phase 2 (startup) -- ``configure_logging`` later replaces the owned handler with the run's own stderr handler.

``OWNED_HANDLER_ATTR`` tags handlers installed by either phase, so phase 2 removes exactly those and keeps foreign handlers. The marker lives in ``infra`` so both setup phases can import it without reversing the layer direction.
"""
from __future__ import annotations

OWNED_HANDLER_ATTR = "_aibuildai_owned"

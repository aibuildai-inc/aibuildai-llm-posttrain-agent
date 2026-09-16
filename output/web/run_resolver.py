"""Resolve one Run ID to its one journal projection worker.

The browser always asks the same run-namespaced route. Process liveness changes
controls, never which endpoint serves Run state.
"""

from __future__ import annotations

import asyncio
import time
from dataclasses import dataclass
from pathlib import Path

from engine.run_query import RunHeadFacts
from output.web.models import RunAvailability
from output.web.projection_workers import ProjectionWorkers
from output.web.run_catalog import RunCatalog
from startup.run_catalog import (
    KUBERNETES_CONTROL_UNAVAILABLE,
    IndexedRun,
)

_READY_TIMEOUT_S = 10.0
_READY_POLL_S = 0.05


@dataclass(frozen=True)
class Resolution:
    availability: RunAvailability
    endpoint: Path | None
    diagnostic: str | None


class RunEndpointResolver:
    def __init__(self, catalog: RunCatalog, workers: ProjectionWorkers) -> None:
        self._catalog = catalog
        self._workers = workers

    async def resolve(self, run_id: str) -> Resolution | None:
        """None for a run id the durable index does not know."""
        run = await asyncio.to_thread(self._catalog.by_id, run_id)
        if run is None:
            return None
        facts = await self._catalog.facts(run)
        resolution = self.resolve_indexed(run, facts, start_projection=True)
        if resolution.availability != "restoring":
            return resolution
        # The first frame request owns the bounded cold-start wait. Returning
        # 503 here would turn the normal creation of the one projection worker
        # into a browser resource error even though that same request started it.
        deadline = time.monotonic() + _READY_TIMEOUT_S
        while time.monotonic() < deadline:
            await asyncio.sleep(_READY_POLL_S)
            resolution = self.resolve_indexed(run, facts, start_projection=True)
            if resolution.availability != "restoring":
                return resolution
        return resolution

    def resolve_indexed(
        self, run: IndexedRun, facts: RunHeadFacts, *, start_projection: bool
    ) -> Resolution:
        """The provider of one indexed run. The catalog lists runs with ``start_projection=False`` so that listing never restores anything."""
        if run.identity.kubernetes_start_mode:
            return Resolution("unavailable", None, KUBERNETES_CONTROL_UNAVAILABLE)
        if facts.incompatible is not None:
            return Resolution("unavailable", None, facts.incompatible)
        if not facts.readable:
            return Resolution(
                "unavailable", None, "the Run has no readable journal history"
            )
        if start_projection:
            state = self._workers.state(run.run_id, run.run_home)
        else:
            endpoint = self._workers.loaded(run.run_id, run.run_home)
            return Resolution("ready", endpoint, None)
        if state.endpoint is not None:
            return Resolution("ready", state.endpoint, None)
        if state.restoring:
            return Resolution("restoring", None, None)
        return Resolution("unavailable", None, state.diagnostic)

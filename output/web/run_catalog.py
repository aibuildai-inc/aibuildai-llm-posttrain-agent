"""The Web run catalog: every indexed run as one selector row.

Run existence comes from the durable index, and head facts come from the Run's PostgreSQL journal. Listing does not restore a Run.
A loaded projection adds its compact display facts. Process liveness is read independently from systemd.
"""

from __future__ import annotations

import asyncio
from typing import TYPE_CHECKING

from engine.run_query import RunHeadFacts
from output.web.live_registry import probe_summary
from output.web.models import RunSummary
from startup.run_catalog import (
    IndexedRun,
    indexed_runs,
    run_active,
    run_facts,
)

if TYPE_CHECKING:
    from output.web.run_resolver import RunEndpointResolver

_FACTS_LOCK = asyncio.Lock()


class RunCatalog:
    def by_id(self, run_id: str) -> IndexedRun | None:
        return next((run for run in indexed_runs() if run.run_id == run_id), None)

    async def facts(self, run: IndexedRun) -> RunHeadFacts:
        async with _FACTS_LOCK:
            return await asyncio.to_thread(run_facts, run.run_id, run.run_home)

    async def summaries(self, resolver: "RunEndpointResolver") -> list[RunSummary]:
        runs = await asyncio.to_thread(indexed_runs)
        rows = await asyncio.gather(*(self._summary(run, resolver) for run in runs))

        # Active runs first by latest update, then open (unfinished) runs,
        # then ended runs by end time, then incompatible ones; run_id breaks
        # ties.
        def order(row: RunSummary) -> int:
            if row.active:
                return 0
            if row.incompatible:
                return 3
            return 2 if row.result is not None else 1

        return sorted(
            rows,
            key=lambda row: (
                order(row),
                -(
                    row.updated_at_unix
                    or row.ended_at_unix
                    or row.started_at_unix
                    or 0.0
                ),
                row.run_id,
            ),
        )

    async def _summary(
        self, run: IndexedRun, resolver: "RunEndpointResolver"
    ) -> RunSummary:
        facts = await self.facts(run)
        resolution = resolver.resolve_indexed(run, facts, start_projection=False)
        member = (
            await probe_summary(resolution.endpoint)
            if resolution.endpoint is not None
            else None
        )
        active = await asyncio.to_thread(run_active, run)
        # Derived labels for one inactive history: an explicit final result
        # first, then the newest epoch's unresolved Failure, then its
        # recorded suspend, then a plain stop.
        if facts.incompatible is not None:
            word, token = "INCOMPATIBLE", "pending"
        elif active:
            word, token = "RUNNING", "running"
        elif facts.result == "completed":
            word, token = "COMPLETED", "success"
        elif facts.result == "failed" or facts.blocked_reason is not None:
            word, token = "FAILED", "error"
        elif facts.suspended:
            word, token = "PAUSED", "warning"
        elif facts.readable:
            word, token = "STOPPED", "warning"
        else:
            word, token = "STARTING", "pending"
        return RunSummary(
            run_id=run.run_id,
            task_name=run.identity.task_name,
            active=active,
            result=facts.result,
            incompatible=facts.incompatible is not None,
            availability=resolution.availability,
            started_at_unix=facts.started_at_unix,
            updated_at_unix=facts.updated_at_unix,
            ended_at_unix=facts.ended_at_unix,
            model=member.model if member is not None else run.identity.model,
            metric_name=member.metric_name if member is not None else None,
            selected_metric=member.selected_metric if member is not None else None,
            elapsed_s=(
                member.elapsed_s if member is not None else facts.head_elapsed_s
            ),
            status_word=word,
            status_style_token=token,
            diagnostic=resolution.diagnostic,
        )

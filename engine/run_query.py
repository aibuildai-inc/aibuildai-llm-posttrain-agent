"""Read the small authoritative head facts of one Run."""

from __future__ import annotations

from dataclasses import dataclass
from itertools import chain
from pathlib import Path
from typing import Literal

from engine.event.events import (
    ActionCompleted,
    ExecutionFailureRecorded,
    RunEpochOpened,
    RunSuspendRequested,
)
from engine.event.store import (
    SCHEMA_VERSION,
    iter_run_events,
    open_search_store,
    read_run_opened_fields,
)
from engine.run_state import RunState
from aibuildai_version import APP_VERSION


_RETIRED_JOURNAL = Path("run") / "journal.db"


@dataclass(frozen=True)
class RunHeadFacts:
    """The Run facts shared by catalog, control, and display decisions."""

    readable: bool = False
    incompatible: str | None = None
    result: Literal["completed", "failed"] | None = None
    blocked_reason: str | None = None
    suspended: bool = False
    head_elapsed_s: float | None = None
    started_at_unix: float | None = None
    updated_at_unix: float | None = None
    ended_at_unix: float | None = None


def read_run_head(run_id: str, run_home: str | Path) -> RunHeadFacts:
    """Read one Run head without creating an aggregate or restoring RunState."""
    # The retired layout kept the journal at <run home>/run/journal.db. No
    # current run creates that directory, so this path can only ever match a
    # run recorded by the old binary: it is the detector's target, not a live
    # location, and the only answer it produces is a refusal. Spelled out here
    # because the constant that used to supply the segment is deleted.
    legacy = Path(run_home) / _RETIRED_JOURNAL
    if legacy.is_file():
        return RunHeadFacts(
            incompatible="this Run uses retired SQLite storage and needs the old binary"
        )
    app = open_search_store(run_id, read_only=True)
    try:
        opened = read_run_opened_fields(app)
        if opened is None:
            return RunHeadFacts()
        if (
            opened.get("app_version") != APP_VERSION
            or opened.get("schema_version") != SCHEMA_VERSION
        ):
            return RunHeadFacts(
                readable=True,
                incompatible=(
                    f"the Run was recorded by aibuildai {opened.get('app_version')} "
                    f"(schema {opened.get('schema_version')}); this build is "
                    f"{APP_VERSION} (schema {SCHEMA_VERSION})"
                ),
            )
        started = opened.get("run_started_at_unix_s")
        if not isinstance(started, int | float):
            raise TypeError("stored RunOpened has no numeric start time")
        events = iter_run_events(app, reverse=True)
        latest = next(events, None)
        if latest is None:
            return RunHeadFacts()
        result: Literal["completed", "failed"] | None = None
        blocked_reason: str | None = None
        suspended = False
        ended_at_unix: float | None = None
        for event in chain((latest,), events):
            if (
                isinstance(event, ActionCompleted)
                and event.target == RunState.ROOT_PATH
            ):
                failed = event.output.get("failed")
                if not isinstance(failed, bool):
                    return RunHeadFacts(
                        readable=True,
                        incompatible="the Run's final result cannot be read by this build",
                        head_elapsed_s=latest.ts,
                        started_at_unix=float(started),
                        updated_at_unix=latest.timestamp.timestamp(),
                    )
                result = "failed" if failed else "completed"
                ended_at_unix = event.timestamp.timestamp()
                break
            if isinstance(event, RunEpochOpened):
                break
            if isinstance(event, RunSuspendRequested):
                suspended = True
            elif isinstance(event, ExecutionFailureRecorded) and blocked_reason is None:
                blocked_reason = event.failure.reason
        return RunHeadFacts(
            readable=True,
            incompatible=None,
            result=result,
            blocked_reason=None if result is not None else blocked_reason,
            suspended=False if result is not None else suspended,
            head_elapsed_s=latest.ts,
            started_at_unix=float(started),
            updated_at_unix=latest.timestamp.timestamp(),
            ended_at_unix=ended_at_unix,
        )
    finally:
        app.close()

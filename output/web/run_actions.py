"""Temporary per-Run Pause and Resume operations for Web Workspace."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Coroutine

import httpx

from infra.process.transient_unit import unit_active
from output.web.live_registry import (
    answers,
    fixed_port,
    member_socket_path,
    socket_client,
    workspace_name,
)
from output.web.models import MemberPauseResponse, RunActionFailure, RunActionKind
from startup import run_launch
from startup.run_catalog import (
    KUBERNETES_CONTROL_UNAVAILABLE,
    IndexedRun,
    run_active,
    run_facts,
)

logger = logging.getLogger(__name__)
PAUSE_TIMEOUT_S = 600.0
RESUME_TIMEOUT_S = 300.0
_POLL_S = 1.0
_CONTROL_TIMEOUT_S = 10.0


@dataclass
class _Action:
    kind: RunActionKind
    error: str | None = None
    task: asyncio.Task[None] | None = None


class RunActions:
    def __init__(self) -> None:
        self._runs: dict[str, _Action] = {}

    def pending_kind(self, run_id: str) -> RunActionKind | None:
        action = self._runs.get(run_id)
        if action is None or action.task is None:
            return None
        return action.kind

    def failure(self, run_id: str) -> RunActionFailure | None:
        action = self._runs.get(run_id)
        if action is None or action.error is None:
            return None
        return RunActionFailure(kind=action.kind, error=action.error)

    def _start(
        self,
        run: IndexedRun,
        kind: RunActionKind,
        work: "Coroutine[object, object, str | None]",
    ) -> None:
        current = self._runs.get(run.run_id)
        if current is not None and current.task is not None:
            work.close()
            return
        action = _Action(kind)
        self._runs[run.run_id] = action
        action.task = asyncio.create_task(self._settle(run.run_id, action, work))

    async def _settle(
        self,
        run_id: str,
        action: _Action,
        work: "Coroutine[object, object, str | None]",
    ) -> None:
        try:
            action.error = await work
        except Exception:  # noqa: BLE001 — every action must settle.
            logger.exception("run %s action failed", run_id)
            action.error = "the Workspace action failed unexpectedly"
        action.task = None
        if action.error is None and self._runs.get(run_id) is action:
            del self._runs[run_id]

    def pause(self, run: IndexedRun) -> None:
        self._start(run, "pause", self._pause(run))

    async def _pause(self, run: IndexedRun) -> str | None:
        if run.identity.kubernetes_start_mode:
            return KUBERNETES_CONTROL_UNAVAILABLE
        try:
            async with socket_client(
                member_socket_path(run.run_id), timeout=_CONTROL_TIMEOUT_S
            ) as client:
                answer = await client.post("/internal/v1/control/pause")
        except (httpx.HTTPError, OSError):
            answer = None
        if answer is not None and answer.status_code != 200:
            return f"the run refused the pause request: {answer.text[:400]}"
        if answer is not None:
            result = MemberPauseResponse.model_validate_json(answer.content).result
            if result == "terminal":
                return None
        deadline = time.monotonic() + PAUSE_TIMEOUT_S
        while time.monotonic() < deadline:
            facts = await asyncio.to_thread(run_facts, run.run_id, run.run_home)
            if facts.result is not None:
                return None
            if not await asyncio.to_thread(run_active, run):
                return None if facts.suspended else "the run stopped without recording a pause"
            await asyncio.sleep(_POLL_S)
        return f"the run did not stop within {PAUSE_TIMEOUT_S:.0f}s of the pause request"

    def resume(self, run: IndexedRun) -> None:
        self._start(run, "resume", self._resume(run))

    async def _resume(self, run: IndexedRun) -> str | None:
        try:
            unit, result_file = await asyncio.to_thread(
                run_launch.start_existing_run,
                run,
                workspace_name(),
                fixed_port(),
            )
        except ValueError as error:
            return str(error)
        deadline = time.monotonic() + RESUME_TIMEOUT_S
        member = member_socket_path(run.run_id)
        while time.monotonic() < deadline:
            if await asyncio.to_thread(answers, member):
                return None
            if not await asyncio.to_thread(unit_active, unit):
                return await asyncio.to_thread(
                    run_launch.read_resume_failure, result_file
                ) or "the resumed process exited before its live member became ready"
            await asyncio.sleep(_POLL_S)
        return f"the resumed process did not become live within {RESUME_TIMEOUT_S:.0f}s"

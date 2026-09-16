"""The live Run identity and Pause endpoint over a private Unix socket.

``LiveWebMember`` owns no Run business frame. The PostgreSQL projection worker
serves every frame while this endpoint reports interactive availability and
accepts Pause. It also starts the user's Workspace and prints the Run URL.

After the run has started, any Web failure (a view build, a request, the
server task dying) is non-essential observability and never fails the
Search. Startup failures are logged the same way: the journal and the run
index are what make the run visible later, so a run whose member could not
start still appears in the Workspace once it ends.
"""

from __future__ import annotations

import asyncio
import contextlib
import logging
import socket
from collections.abc import Iterator
from pathlib import Path
from types import TracebackType
from typing import TYPE_CHECKING, Callable, Literal

import uvicorn
from fastapi import FastAPI

from infra.host_resource.cgroup import kubernetes_start_mode
from output.web.live_registry import (
    MEMBER_PROTOCOL_VERSION,
    bind_member_socket,
    member_socket_path,
)
from output.web.models import MemberIdentity, MemberPauseResponse
from output.web.service_manager import ensure_workspace

if TYPE_CHECKING:
    from engine.run_state import RunState

logger = logging.getLogger(__name__)


class _RunOwnedServer(uvicorn.Server):
    """Uvicorn without its own signal handlers.

    ``Server.serve`` replaces the process SIGINT/SIGTERM handlers for its
    lifetime. This server runs inside the run's own asyncio loop, and the run
    already owns those signals (Ctrl-C suspend, the SIGTERM handler in
    cli_impl), so capturing them here would silently swallow a user's stop.
    """

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


class LiveWebMember:
    """Expose one live run over a private UDS; own no fixed TCP listener.

    The run drives one live control surface: ``render`` records optional host
    resource samples, ``set_suspend_callback`` binds Pause, and the async
    context manager owns the endpoint's lifetime. The journal projection, not
    this endpoint, owns the browser's Run frames."""

    _RESOURCE_SAMPLE_INTERVAL = 15.0

    def __init__(
        self,
        *,
        run_id: str,
        resource_history_path: "Path | None" = None,
    ) -> None:
        self._run_id = run_id
        self._resource_history_path = resource_history_path
        self._last_resource_sample_at: float = -999.0
        self._resource_sample_task: asyncio.Task[None] | None = None
        self._server: _RunOwnedServer | None = None
        self._server_task: asyncio.Task | None = None
        self._sock: socket.socket | None = None
        self._server_task_failed = False
        self._suspend: "Callable[[str], None] | None" = None
        self.active = False
        self.url: str | None = None

    def render(
        self,
        run_state: "RunState",
        *,
        now_relative: float,
        now_absolute: float,
    ) -> None:
        """Record host resources. The journal projection owns every Run frame."""
        del run_state, now_absolute
        self._report_dead_server_once()
        self._maybe_record_resources(now_relative)

    def _maybe_record_resources(self, now_relative: float) -> None:
        path = self._resource_history_path
        if path is None:
            return
        if now_relative - self._last_resource_sample_at < self._RESOURCE_SAMPLE_INTERVAL:
            return
        if self._resource_sample_task is not None and not self._resource_sample_task.done():
            return
        self._last_resource_sample_at = now_relative

        async def _sample() -> None:
            try:
                from output.web.system_resources import append_history_sample, collect_history_sample
                sample = await asyncio.to_thread(collect_history_sample, now_relative)
                await asyncio.to_thread(append_history_sample, path, sample)
            except Exception:  # noqa: BLE001
                logger.debug("resource sample failed", exc_info=True)

        self._resource_sample_task = asyncio.ensure_future(_sample())

    def set_suspend_callback(self, callback: "Callable[[str], None]") -> None:
        # The private pause route calls this back with the "web" reason,
        # landing on the run's durable suspend operation.
        self._suspend = callback

    def _pause(self) -> "Literal['accepted', 'already-requested', 'terminal']":
        """Decide one pause request on the run's own event loop."""
        if self._suspend is None:
            return "terminal"
        self._suspend("web")
        return "accepted"

    def _app(self) -> FastAPI:
        app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)

        @app.get("/api/v1/identity", response_model=MemberIdentity)
        async def identity() -> MemberIdentity:
            return MemberIdentity(
                run_id=self._run_id,
                member_protocol_version=MEMBER_PROTOCOL_VERSION,
            )

        @app.post(
            "/internal/v1/control/pause", response_model=MemberPauseResponse
        )
        async def pause() -> MemberPauseResponse:
            return MemberPauseResponse(result=self._pause())

        return app

    async def __aenter__(self) -> "LiveWebMember":
        # Issue 2609: the Web observer is isolated from Search execution. A
        # member that cannot start, or a Workspace gateway that cannot be
        # reached, is logged and the run goes on; the run stays durable and
        # the Workspace can show it from its journal later.
        if kubernetes_start_mode():
            logger.info(
                "Web Workspace unavailable: Kubernetes start mode supports fresh runs only"
            )
            return self
        try:
            self._sock = bind_member_socket(member_socket_path(self._run_id))
            self._server = _RunOwnedServer(
                uvicorn.Config(self._app(), log_config=None, access_log=False)
            )
            self._server_task = asyncio.ensure_future(self._server.serve(sockets=[self._sock]))
            while not self._server.started:
                if self._server_task.done():
                    self._server_task.result()
                    raise AssertionError("web member task ended before it started")
                await asyncio.sleep(0.01)
            self.active = True
        except Exception:  # noqa: BLE001 — the observer boundary named above.
            logger.exception("web member for run %s unavailable; run unaffected", self._run_id)
            await self._stop()
            return self
        try:
            self.url = f"{await asyncio.to_thread(ensure_workspace)}/run/{self._run_id}"
        except Exception as exc:  # noqa: BLE001 — the observer boundary named above.
            # One line, not a traceback: the diagnostic is the message.
            logger.error("AIBuildAI Workspace unavailable: %s; run unaffected", exc)  # noqa: TRY400
            return self
        print(f"AIBuildAI Workspace: {self.url}", flush=True)
        logger.info("web member serving run %s at %s", self._run_id, self.url)
        return self

    async def __aexit__(
        self,
        exc_type: "type[BaseException] | None",
        exc: "BaseException | None",
        tb: "TracebackType | None",
    ) -> None:
        # Never wait for the browser: the Workspace backend sees the socket
        # close and answers the same URL from the recorded projection.
        self.active = False
        await self._stop()

    async def _stop(self) -> None:
        if self._resource_sample_task is not None and not self._resource_sample_task.done():
            try:
                await asyncio.wait_for(self._resource_sample_task, timeout=10.0)
            except (asyncio.TimeoutError, asyncio.CancelledError, Exception):  # noqa: BLE001, S110
                logger.debug("resource sample task did not finish cleanly")
        if self._server is not None:
            self._server.should_exit = True
        if self._server_task is not None:
            try:
                await asyncio.wait_for(self._server_task, timeout=5.0)
            except (asyncio.TimeoutError, asyncio.CancelledError):
                self._server_task.cancel()
            except Exception:  # noqa: BLE001 — a server task that died re-raises when awaited; after the run has started that is non-essential observability and must not contaminate a finished run.
                logger.exception("web member server failed; run unaffected")
        self._server_task = None
        self._server = None
        if self._sock is not None:
            self._sock.close()
            self._sock = None
            # Only a socket file this process bound is this process's to
            # remove: a refused duplicate start never unlinks the live one.
            member_socket_path(self._run_id).unlink(missing_ok=True)

    def _report_dead_server_once(self) -> None:
        if self._server_task_failed or self._server_task is None or not self._server_task.done():
            return
        self._server_task_failed = True
        if self._server_task.cancelled():
            logger.error("web member server task was cancelled; page is gone")
            return
        error = self._server_task.exception()
        if error is not None:
            logger.error("web member server died: %r; page is gone", error)

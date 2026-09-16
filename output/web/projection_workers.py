"""Lazy, bounded, idle-evicted projection workers for loaded Runs.

The Workspace backend never restores a Run in its own interpreter. One worker
serves one loaded Run on a private socket. It catches up once and subscribes to
later PostgreSQL commits, so it stays the same process when the Run advances.
The backend keeps the loaded set bounded and evicts idle workers.
"""

from __future__ import annotations

import logging
import os
import signal
import subprocess
import time
from dataclasses import dataclass
from pathlib import Path

from output.web.live_registry import answers, projection_socket_path
from output.web.service_manager import own_command

MAX_LOADED = 4
RETRY_AFTER_S = 60.0
# The tail of a failed worker's stderr that becomes its diagnostic.
_DIAGNOSTIC_CHARS = 240

logger = logging.getLogger(__name__)


@dataclass
class _Worker:
    process: subprocess.Popen[bytes]
    socket_path: Path
    log_path: Path
    last_used: float


@dataclass(frozen=True)
class ProjectionState:
    """Where one Run's continuous projection is right now: a socket when ready, otherwise whether it is restoring or why it is unavailable."""

    endpoint: Path | None
    restoring: bool
    diagnostic: str | None


class ProjectionWorkers:
    def __init__(self, generation: str) -> None:
        self._generation = generation
        self._workers: dict[str, _Worker] = {}
        self._failed: dict[str, tuple[float, str]] = {}

    def state(self, run_id: str, run_home: str) -> ProjectionState:
        """The run's projection, started now if it is not loaded."""
        worker = self._current(run_id, run_home)
        failed = self._failed.get(run_id)
        if failed is not None:
            failed_at, diagnostic = failed
            if time.monotonic() - failed_at < RETRY_AFTER_S:
                return ProjectionState(None, False, diagnostic)
            del self._failed[run_id]
        if worker is None:
            worker = self._start(run_id, run_home)
        worker.last_used = time.monotonic()
        if answers(worker.socket_path):
            return ProjectionState(worker.socket_path, False, None)
        return ProjectionState(None, True, None)

    def loaded(self, run_id: str, run_home: str) -> Path | None:
        """The run's socket when a ready worker serves it; starts nothing."""
        worker = self._current(run_id, run_home)
        if worker is None or not answers(worker.socket_path):
            return None
        return worker.socket_path

    def _current(self, run_id: str, run_home: str) -> _Worker | None:
        del run_home
        self._reap()
        return self._workers.get(run_id)

    def _start(self, run_id: str, run_home: str) -> _Worker:
        while len(self._workers) >= MAX_LOADED:
            oldest = min(self._workers, key=lambda r: self._workers[r].last_used)
            logger.info("projection workers evict run %s", oldest)
            self._stop(oldest)
        socket_path = projection_socket_path(run_id, self._generation)
        log_path = socket_path.with_suffix(".log")
        # Private like the sockets beside it: a failed restore writes a traceback.
        with open(os.open(log_path, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600), "wb") as log:
            process = subprocess.Popen(
                [*own_command(), "__web_projection", run_home, str(socket_path)],
                stdin=subprocess.DEVNULL,
                stdout=log,
                stderr=subprocess.STDOUT,
            )
        logger.info("projection worker for run %s started as pid %d", run_id, process.pid)
        worker = _Worker(process, socket_path, log_path, time.monotonic())
        self._workers[run_id] = worker
        return worker

    def _stop(self, run_id: str) -> None:
        worker = self._workers.pop(run_id)
        if worker.process.poll() is None:
            worker.process.send_signal(signal.SIGTERM)
            try:
                worker.process.wait(timeout=5.0)
            except subprocess.TimeoutExpired:
                worker.process.kill()
                worker.process.wait()
        worker.socket_path.unlink(missing_ok=True)
        worker.log_path.unlink(missing_ok=True)

    def _reap(self) -> None:
        for run_id, worker in list(self._workers.items()):
            code = worker.process.poll()
            if code is None:
                continue
            del self._workers[run_id]
            worker.socket_path.unlink(missing_ok=True)
            if code == 0:
                logger.info("projection worker for run %s exited idle", run_id)
            else:
                tail = worker.log_path.read_bytes()[-_DIAGNOSTIC_CHARS:].decode("utf-8", "replace")
                last_line = tail.strip().splitlines()[-1] if tail.strip() else f"exit code {code}"
                logger.error("projection worker for run %s failed: %s", run_id, tail.strip())
                self._failed[run_id] = (
                    time.monotonic(),
                    f"this run could not be restored: {last_line}",
                )
            worker.log_path.unlink(missing_ok=True)

    def shutdown(self) -> None:
        for run_id in list(self._workers):
            self._stop(run_id)

    def __len__(self) -> int:
        self._reap()
        return len(self._workers)

    def pids(self) -> dict[str, int]:
        return {run_id: w.process.pid for run_id, w in self._workers.items()}

"""Run a short host command under a caller-owned relative timeout."""

from __future__ import annotations

import asyncio
import contextlib
import os
import signal
import subprocess
import threading
from collections.abc import Callable, Mapping, Sequence
from typing import TypeVar


_T = TypeVar("_T")


class _BlockingStopped(Exception):
    """The caller asked blocking work to stop."""


async def run_captured(
    argv: Sequence[str],
    timeout_s: float,
    *,
    cwd: str | os.PathLike[str] | None = None,
    env: Mapping[str, str] | None = None,
) -> subprocess.CompletedProcess[str]:
    """Capture one command and reap its process group on timeout or cancellation."""
    if timeout_s <= 0:
        raise TimeoutError
    process = await asyncio.create_subprocess_exec(
        *argv,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd,
        env=env,
        start_new_session=True,
    )
    completion = asyncio.create_task(process.communicate())
    try:
        stdout, stderr = await asyncio.wait_for(asyncio.shield(completion), timeout_s)
    except (TimeoutError, asyncio.CancelledError):
        with contextlib.suppress(ProcessLookupError):
            os.killpg(process.pid, signal.SIGKILL)
        await completion
        raise
    returncode = process.returncode
    if returncode is None:
        raise AssertionError("reaped command has no return code")
    return subprocess.CompletedProcess(
        argv, returncode, stdout.decode(errors="replace"), stderr.decode(errors="replace")
    )


async def run_blocking(
    operation: Callable[[threading.Event], _T], timeout_s: float
) -> _T:
    """Run blocking work off the loop and stop it on timeout or cancellation."""
    if timeout_s <= 0:
        raise TimeoutError
    stop = threading.Event()
    task = asyncio.create_task(asyncio.to_thread(operation, stop))
    try:
        return await asyncio.wait_for(asyncio.shield(task), timeout_s)
    except asyncio.CancelledError as cancelled:
        stop.set()
        try:
            await task
        except _BlockingStopped:
            raise cancelled from None
        raise
    except TimeoutError as expired:
        stop.set()
        try:
            await task
        except _BlockingStopped:
            raise expired from None
        raise

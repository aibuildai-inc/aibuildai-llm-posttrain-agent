"""Local and Program process declarations.

Describes a command, its IO, resources, relative timeout, and confinement.

This module imports the engine ``ExecutionCapability`` it carries. It never imports ``config`` — ``apply_bwrap`` is resolved by the engine call site from ``resources.sandbox.enable`` and handed in as a plain bool.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass, field

from engine.capability import ExecutionCapability


class ExecCapture(enum.Enum):
    """How the subprocess's stdout/stderr are collected."""

    STREAM_TO_FILE = "stream_to_file"  # stdout and stderr streamed to one file
    PIPE_CAPTURE = (
        "pipe_capture"  # stdout/stderr collected in memory (score program / gray zone)
    )


@dataclass(frozen=True)
class IOManifest:
    """One unit's declared read/write set — the single structural home of the closed read set."""

    read_paths: tuple[str, ...]  # hard --ro-bind (must exist)
    write_paths: tuple[str, ...]  # hard --bind


@dataclass(frozen=True)
class ExecSpec:
    """Local subprocess request consumed only by the local Executor."""

    argv: list[str]  # inner command, without the interpreter prepended below
    cwd: str  # child working directory (the executor .resolve()s and binds it)
    io_manifest: IOManifest
    capability: ExecutionCapability
    timeout_s: float
    stop_wait_seconds: float  # wait after SIGTERM before forced stop
    cgroup: str
    apply_bwrap: bool
    system_read_paths: tuple[str, ...]
    system_write_paths: tuple[str, ...]
    required_system_read_paths: tuple[str, ...]
    required_system_write_paths: tuple[str, ...]
    capture: ExecCapture
    log_path: str | None  # STREAM_TO_FILE destination
    unit_tag: str  # the unit's display + failure-attribution key
    # The child interpreter, already resolved by the site that declared it, or
    # None to run under whatever PATH already provides. When set it is
    # resolved to its physical path (required under a symlinked
    # playground), passed as argv[0], and its bin dir is prepended to PATH.
    interpreter: str | None
    require_gpu_use: bool = False
    extra_env: dict[str, str] = field(
        default_factory=dict
    )  # Extra environment values for the child and systemd-run.
    inherit_env: bool = (
        True  # False for checks that must not receive the manager environment.
    )
    cwd_writable: bool = True
    capture_limit_bytes: int | None = None


@dataclass(frozen=True)
class ProgramExecSpec:
    """Substrate-neutral declaration for one Program attempt."""

    module: str
    args: tuple[str, ...]
    cwd: str
    io_manifest: IOManifest
    capability: ExecutionCapability
    timeout_s: float
    stop_wait_seconds: float
    log_path: str  # a Program always streams to a file
    unit_tag: str
    extra_env: dict[str, str] = field(default_factory=dict)
    require_gpu_use: bool = False

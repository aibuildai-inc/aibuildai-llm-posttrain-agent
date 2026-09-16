"""Mechanism-only local or Program process outcome.

A process boundary never constructs an engine Failure. It reports raw process facts such as the return code, timeout, resource-cap kill, substrate failure, work time, and log location. Cancellation is not one of them: a cancelled process is terminated and its ``CancelledError`` is re-raised, so there is no outcome to report. The fixed Program driver in ``engine/work_unit/program/base.py`` maps those facts and the typed ``main()`` result to an engine Failure.

``KillCause`` covers unit-owned resource-cap kills (this unit exceeded its own budget); the local cgroup memory cap is the one such kill the product has. ``InfraReason`` covers substrate-owned failures (the host/tooling could not run the unit at all, or a supervisor mechanism error). Both are closed enums so a classifier can pattern-match exhaustively and assert on any unmapped member.
"""

from __future__ import annotations

import enum
from dataclasses import dataclass


class KillCause(enum.Enum):
    """A kill attributed to THIS unit's own resource cap (unit-owned)."""

    NONE = "none"
    MEMORY = "memory"  # per-unit cgroup memory cap tripped


class InfraReason(enum.Enum):
    """Substrate-owned failure reason (closed enum). NONE = not an infra failure."""

    NONE = "none"
    SYSTEMD_UNAVAILABLE = (
        "systemd_unavailable"  # systemd-run / user systemd unreachable
    )
    BWRAP_UNAVAILABLE = "bwrap_unavailable"  # sandbox requested but bwrap not on PATH
    HOST_GPU_UNAVAILABLE = (
        "host_gpu_unavailable"  # host cannot present the allocated GPU (preflight)
    )
    SYSTEM_PATH_NOT_ALLOWED = (
        "system_path_not_allowed"  # sandbox config omits a required host path
    )
    SPAWN_FAILURE = "spawn_failure"  # create_subprocess_exec raised OSError
    SUPERVISOR_ERROR = (
        "supervisor_error"  # our own supervisor mechanism raised unexpectedly
    )


# Human diagnostics for each substrate (InfraReason) cause. Co-located with the
# enum because the per-reason text is a property of the reason itself: an
# Executor that adds an InfraReason member adds its diagnostic here too,
# without touching an engine file. Every infra Failure call
# site builds its feedback text from this one table, prepending its own site
# context (e.g. "the Program could not run due to an infrastructure failure:
# {base}."). The messages are therefore SITE-NEUTRAL: each names the substrate
# cause actionably without assuming which subprocess site hit it, so the same
# entry reads correctly for every Program.
INFRA_REASON_DIAGNOSTIC: dict[InfraReason, str] = {
    InfraReason.SYSTEMD_UNAVAILABLE: (
        "the user systemd manager / systemd-run was unavailable, so no cgroup "
        "scope could be created for the subprocess"
    ),
    InfraReason.BWRAP_UNAVAILABLE: (
        "the bubblewrap sandbox binary was not available on PATH"
    ),
    InfraReason.HOST_GPU_UNAVAILABLE: (
        "the host could not present the allocated GPU device(s) to the subprocess"
    ),
    InfraReason.SYSTEM_PATH_NOT_ALLOWED: (
        "the sandbox system path config does not cover a required host path"
    ),
    InfraReason.SPAWN_FAILURE: "the subprocess could not be spawned",
    InfraReason.SUPERVISOR_ERROR: "the execution supervisor raised an unexpected error",
}


@dataclass(frozen=True)
class ExecOutcome:
    """Mechanism-only facts about one completed or aborted process.

    At most one failure signal may be true at once. A clean success has every signal false. Enforced by ``__post_init__``.
    """

    return_code: (
        int | None
    )  # None when failing before/without an exit code (timeout/kill/spawn failure)
    timed_out: bool  # hit the wall-clock cap (SIGTERM path)
    kill_cause: KillCause  # unit-owned mechanism kill
    infra_reason: InfraReason  # substrate-owned reason
    consumed_s: float  # work time; a timeout reports the stated cap
    log_path: str | None  # STREAM_TO_FILE destination; None under PIPE_CAPTURE
    stdout: str | None  # PIPE_CAPTURE captured stdout; None under STREAM_TO_FILE
    stderr: str | None  # PIPE_CAPTURE captured stderr; None under STREAM_TO_FILE
    gpu_unused: bool = False  # the run exited cleanly but no process of its own
    # ever appeared on a card it was given. The local
    # rule is that presence, not a utilization
    # fraction. Not a crash: a failure signal that
    # co-occurs with return_code == 0. What it becomes
    # is the driver's call, and a Program's own Failure
    # outranks it.
    detail: str | None = None  # exact mechanism text, such as a missing system
    # path or CUDA probe failure. NOT a failure signal.
    # The engine keeps it in the user-visible reason.
    output_limit_exceeded: bool = (
        False  # captured output crossed the caller's hard byte cap
    )

    def __post_init__(self) -> None:
        failure_signals = (
            self.kill_cause != KillCause.NONE,
            self.infra_reason != InfraReason.NONE,
            self.timed_out,
            self.return_code not in (None, 0),
            self.gpu_unused,
            self.output_limit_exceeded,
        )
        signal_count = sum(1 for signal in failure_signals if signal)
        if signal_count > 1:
            raise AssertionError(
                "ExecOutcome must carry at most one failure signal, got "
                f"{signal_count}: kill_cause={self.kill_cause}, "
                f"infra_reason={self.infra_reason}, timed_out={self.timed_out}, "
                f"return_code={self.return_code}, "
                f"gpu_unused={self.gpu_unused}"
                f", output_limit_exceeded={self.output_limit_exceeded}"
            )

    def infra_diagnostic(self) -> str:
        """Return the full user-facing reason for an infrastructure failure."""
        if self.infra_reason is InfraReason.NONE:
            raise AssertionError("infra_diagnostic requires an infrastructure failure")
        base = INFRA_REASON_DIAGNOSTIC[self.infra_reason]
        return f"{base}: {self.detail}" if self.detail else base

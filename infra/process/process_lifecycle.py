"""Process-level OS lifecycle for the aibuildai entry point.

Owns the OS lifecycle that wraps the whole process regardless of subcommand: re-exec as the main process of a transient systemd user service and tracemalloc. The service owns every local process in the run. When its main process exits for any reason, systemd kills anything left in the service cgroup.

Signals have no synchronous handler here. The whole pipeline runs under ``asyncio.run`` in cli.py, and ``cli_impl._interrupt_signals`` installs one asyncio handler for both SIGINT and SIGTERM via ``loop.add_signal_handler`` (delivered at a safe loop-iteration boundary, never mid-bytecode). The first signal records a suspend request through ``Search.suspend`` while the supervisor keeps joining the root workflow; a second signal forces teardown. ``RunCommand.execute`` reads the saved reason after cancellation, so this class no longer records the signal.

The CLI gives its command action to the shared runner:

    args = parse_args()

    def execute_command(fresh_environment_file):
        command, config = load_config(args)
        return COMMANDS[command]().execute(config)

    outcome = ProcessLifecycle.run(execute_command)
    sys.exit(outcome.finalize())

The runner uses this class as a context manager. __enter__ runs the re-exec first. The service kills any local process left after the main process exits.

Pure infra: imports only stdlib and low-level infra modules.
"""
import atexit
import logging
import os
import shutil
import subprocess
import sys
from collections.abc import Callable
from pathlib import Path
from types import TracebackType
from typing import TypeVar

from infra.log_markers import OWNED_HANDLER_ATTR
from infra.host_resource.cgroup import (
    HostCapabilityUnavailable,
    ProcessNotKillable,
    WorkerCgroupUnavailable,
    kubernetes_start_mode,
    own_starttime,
    run_service_name,
    systemd_unavailable_guidance,
)
from infra.process.transient_unit import render_environment_file

_T = TypeVar("_T")
_EX_UNAVAILABLE = 69
# Set on the service itself instead of being left in the caller
# EnvironmentFile. Each one describes THIS launch -- the service, the Web
# workspace that started it, where its startup result goes -- so keeping it in
# the file that becomes the run's private launch environment would carry a
# stale launch into the next epoch. A caller choice that belongs to the run
# stays in the file and reaches every later epoch through it.
_HANDOFF_ENV_NAMES = (
    "AIBUILDAI_IN_SERVICE",
    "AIBUILDAI_ENV_FILE",
    "AIBUILDAI_RUN_ID",
    "AIBUILDAI_WEB_NAME",
    "AIBUILDAI_WEB_PORT",
    "AIBUILDAI_RESUME_RESULT_FILE",
)

def _make_this_process_and_all_its_children_killable() -> None:
    """Reset this process's ``oom_score_adj`` to 0, ONCE, before anything is spawned.

    ``oom_score_adj`` is inherited across fork AND exec, and the orchestrator is the root of every process this product will ever create. So resetting it here makes the whole run killable -- the orchestrator, every sidecar it forks (the local MCP servers, the LLM adapter), every subprocess on every launch path, and every path nobody has thought of yet. Doing it per-launcher instead patches one path at a time, and it missed three.

    Why it must be done at all: the orchestrator can inherit ``oom_score_adj = -1000`` from the ssh or VS Code Remote session that started it. aibuildai never sets it. At -1000 a task is INELIGIBLE for the OOM killer -- and the run now lives in a cgroup tree with real ceilings, so ``framework/`` has a real ``memory.max`` and every task inside it was ineligible. A ceiling whose cgroup contains no eligible victim is not a ceiling: on a breach the memcg OOM killer finds nobody to kill, so instead it LIVELOCKS, re-reporting the OOM forever and pinning a core, and it takes the shared host with it.

    Measured, before this existed: a 600 MiB gray-zone subprocess against a 300 MiB ``framework/`` ceiling -- both it and the orchestrator sat at -1000, ``oom_kill`` stayed 0, neither process died, and neither made progress. Not a kill. A hang.

    Raising ``oom_score_adj`` toward 0 needs no capability (only LOWERING it does), and it writes ``/proc/self``, so it needs no cgroup delegation either. It is asserted, never best-effort: a host where this write fails is exactly the host that would livelock, and it must refuse to start rather than hang later.

    ``Confinement``'s per-launcher preamble stays. It is not redundant: the Claude SDK execs its launcher from its own process tree, and defence in depth on the one line whose absence hangs the machine is worth its two lines.
    """
    try:
        Path("/proc/self/oom_score_adj").write_text("0")
    except OSError as exc:
        # The write is refused in two different situations, and only one of them is
        # the danger this guard exists for. The kernel denies it when the new value
        # is below ``signal->oom_score_adj_min``, a floor a privileged writer set --
        # so a systemd user manager started with a POSITIVE ``OOMScoreAdjust``
        # propagates that positive floor to every transient service under it, and
        # lowering to 0 is denied there too. A process already at >= 0 is eligible
        # for the OOM killer, which is the whole property this guard protects: the
        # ceiling has a victim, nothing can livelock, and there is nothing to fix.
        # Refusing on that would ground the run on a host that is behaving correctly.
        current = _read_oom_score_adj()
        if current is not None and current >= 0:
            return
        inherited = "unreadable" if current is None else f"inherited as {current}"
        raise ProcessNotKillable(
            f"cannot reset this process's oom_score_adj ({exc}). It is "
            f"{inherited} from the "
            f"session that started aibuildai, which makes every process in this "
            f"run INELIGIBLE for the kernel's OOM killer. The run's memory ceilings would "
            f"then not kill anything -- the kernel would livelock instead, pin a core, and "
            f"take this host down. Refusing to start."
        ) from exc


def _read_oom_score_adj() -> "int | None":
    """This process's current ``oom_score_adj``, or None when it cannot be read.

    Only the failure path needs it, to tell a positive inherited floor (harmless) apart from a negative one (the livelock this guard refuses to start into).
    """
    try:
        return int(Path("/proc/self/oom_score_adj").read_text().strip())
    except (OSError, ValueError):
        return None


class ProcessLifecycle:
    @classmethod
    def run(
        cls,
        action: Callable[["Path | None"], _T],
        *,
        unit_name: "str | None" = None,
    ) -> _T:
        """Run one command with process cleanup and clear host errors.

        ``unit_name`` is the deterministic per-run service name for a command
        that owns a Run ID; a command with none gets a process-instance name."""
        try:
            with cls(unit_name=unit_name) as lifecycle:
                return action(lifecycle._fresh_environment_file)
        except HostCapabilityUnavailable as error:
            print(f"aibuildai: {error}", file=sys.stderr)
            raise SystemExit(_EX_UNAVAILABLE) from None

    @classmethod
    def run_already_in_service(cls, action: Callable[[], _T]) -> _T:
        """Run one process that the startup launcher already placed in systemd."""
        with cls(unit_name=None, reexec=False):
            return action()

    def __init__(
        self, *, unit_name: "str | None" = None, reexec: bool = True
    ) -> None:
        self._tracemalloc_log: "str | None" = None
        self._unit_name = unit_name
        self._reexec = reexec
        self._fresh_environment_file: "Path | None" = None

    def __enter__(self) -> "ProcessLifecycle":
        if self._reexec:
            self._reexec_in_systemd_service()    # MUST be first; may os.execvp (never returns) or no-op
        _make_this_process_and_all_its_children_killable()
        self._maybe_start_tracemalloc()
        self._install_early_log_handler()        # stage-1 of the logging bootstrap
        return self

    def _install_early_log_handler(self) -> None:
        """Stage-1 of the two-phase logging bootstrap: install an early root stderr handler before config is loaded, so warnings emitted during startup are visible. Mirrors ``logging.basicConfig`` semantics (add only when the root has no handlers, pin the root to WARNING) but tags the handler with the aibuildai-owned marker -- which basicConfig cannot do. That marker lets stage-2 ``startup.logging_config.configure_logging`` remove this handler when it installs the final set.
        """
        root = logging.getLogger()
        if root.handlers:
            return
        root.setLevel(logging.WARNING)
        handler = logging.StreamHandler(sys.stderr)
        handler.setLevel(logging.WARNING)
        handler.setFormatter(
            logging.Formatter("%(asctime)s %(levelname)s %(name)s %(message)s")
        )
        setattr(handler, OWNED_HANDLER_ATTR, True)
        root.addHandler(handler)

    def __exit__(self, exc_type: "type[BaseException] | None", exc: "BaseException | None", tb: "TracebackType | None") -> bool:
        if self._fresh_environment_file is not None:
            self._fresh_environment_file.unlink(missing_ok=True)
        return False                             # never swallow exceptions / SystemExit

    def _reexec_in_systemd_service(self) -> None:
        """Re-exec self as the main process of a delegated transient service.

        The service is the run's whole world. ``Delegate=yes`` hands its cgroup subtree to this process, which then builds the resource tree inside it (``infra/host_resource/tree.py``) without any further help from systemd -- and without any privilege, because a delegated subtree needs none.

        systemd is asked for exactly one thing and never asked again. That is deliberate: it does not report whether a limit took. Writing ``cpu.max`` into a cgroup whose controller was never delegated returns ENOENT, systemd downgrades that to a DEBUG log, and the caller sees success -- which is how this product's CPU limits were dead for three months. Every limit below the delegation root is written by the product and read back.

        Outside Kubernetes start mode there is no opt-out. A run that cannot be confined is a run that can take the host down, so it refuses to start instead.

        Must be called before any subprocess is spawned, argparse is run, or heavy imports happen -- ideally the first line of cli().
        """
        if kubernetes_start_mode():
            # Kubernetes start mode: the Pod's own cgroup is the
            # resource fence, and an unprivileged Pod has no cgroup2 filesystem
            # to delegate, so there is no service to re-exec into.
            return

        if os.environ.get("AIBUILDAI_IN_SERVICE") == "1":
            # The service is up, so systemd has already read the environment
            # handoff file. A fresh local Run keeps the pre-systemd source until
            # its launch context is published; every other command deletes it now.
            handoff = os.environ.pop("AIBUILDAI_ENV_FILE", None)
            if handoff:
                path = Path(handoff)
                if os.environ.get("AIBUILDAI_RUN_ID"):
                    self._fresh_environment_file = path
                else:
                    path.unlink()
            return

        systemd_run = shutil.which("systemd-run")
        if not systemd_run:
            raise WorkerCgroupUnavailable(
                "systemd-run not found on PATH, so this run cannot be confined in a "
                "delegated cgroup service.\n\n" + systemd_unavailable_guidance()
            )

        if not os.environ.get("XDG_RUNTIME_DIR"):
            raise WorkerCgroupUnavailable(
                "XDG_RUNTIME_DIR is unset, so there is no systemd user session to run "
                "inside.\n\n" + systemd_unavailable_guidance()
            )

        if not self._user_manager_reachable():
            # systemd-run is present and XDG_RUNTIME_DIR is set, but the user
            # manager itself is DEAD (WSL2 with systemd disabled, a container with
            # no user init). Exec'ing into systemd-run here would REPLACE this
            # process with one that dies on a raw "Failed to connect to bus" that
            # never reads as a product message. Refuse loudly with the SAME
            # actionable environment error the worker-slice path raises, so the
            # cli() boundary renders it plainly and exits EX_UNAVAILABLE — no
            # fallback, resource governance is load-bearing.
            raise WorkerCgroupUnavailable(
                "the systemd user manager is not reachable, so this run cannot be "
                "confined in a systemd service.\n\n" + systemd_unavailable_guidance()
            )

        inner_cmd = [sys.executable, *sys.argv]

        service_name = self._unit_name or run_service_name(
            os.getpid(), own_starttime()
        )
        terminal_mode = (
            "--pty"
            if sys.stdin.isatty() and sys.stdout.isatty() and sys.stderr.isatty()
            else "--pipe"
        )
        # A process command line is world-readable on Linux (/proc/<pid>/cmdline,
        # mode 444, no hidepid on a normal host), so a --setenv=NAME=VALUE per
        # variable publishes every provider key to every account on the machine
        # for as long as the run lives. Hand the values over in a file
        # only this account can read, and put only its PATH on the line. A
        # fresh Run keeps this pre-systemd source until startup publishes its
        # private launch context; every other command deletes it at entry.
        env_file = Path(os.environ["XDG_RUNTIME_DIR"]) / f"{service_name}.env"
        environment = dict(os.environ)
        handoff_values = {
            name: environment.pop(name)
            for name in _HANDOFF_ENV_NAMES
            if name in environment
        }
        # Render every value BEFORE the file exists. A value this format cannot
        # carry stops the run here, while there is still nothing on disk to leave
        # behind and no service exists to own it.
        try:
            rendered = render_environment_file(environment)
        except ValueError as error:
            raise WorkerCgroupUnavailable(str(error)) from error
        # Exclusive creation at 0600: the mode is set as the file comes into
        # existence, so the values are never readable through a wider mode, and a
        # name already taken stops the run instead of reusing someone's file.
        fd = os.open(env_file, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, "w") as handle:
            handle.write(rendered)
        wrapper = [
            systemd_run,
            "--user",
            "--quiet",
            f"--unit={service_name}",
            "--collect",
            "--wait",
            "--same-dir",
            "--service-type=exec",
            terminal_mode,
            # Hand the subtree to the product. Everything below this point is written
            # to cgroupfs directly, and read back.
            "--property=Delegate=yes",
            # Give the main process the normal signal path. Once it exits, kill
            # every remaining local process in the service cgroup immediately.
            "--property=KillMode=mixed",
            # A service starts with the user manager's environment, not this
            # command's environment. Pass the caller's values to the run.
            f"--property=EnvironmentFile={env_file}",
            "--setenv=AIBUILDAI_IN_SERVICE=1",
            f"--setenv=AIBUILDAI_ENV_FILE={env_file}",
            *(
                f"--setenv={name}={value}"
                for name, value in handoff_values.items()
            ),
            "--",
            *inner_cmd,
        ]

        try:
            os.execvp(wrapper[0], wrapper)
        except OSError as exc:
            # No service will start, so nothing else deletes the handoff file.
            env_file.unlink()
            raise WorkerCgroupUnavailable(
                f"could not exec systemd-run to place this run in a delegated cgroup "
                f"service ({exc}).\n\n" + systemd_unavailable_guidance()
            ) from exc

    def _user_manager_reachable(self) -> bool:
        """True iff the systemd user manager answers.

        ``systemctl --user is-system-running`` connects to the user bus and prints a state word (``running`` / ``degraded`` / ``starting`` / ...) on stdout even when it exits non-zero (a degraded-but-reachable manager exits non-zero); a DEAD manager prints nothing on stdout and ``Failed to connect to bus`` on stderr. So reachability is decided by a non-empty, non-``offline`` stdout state, NOT the exit code. Cheap: one short subprocess, run once at startup before the re-exec."""
        systemctl = shutil.which("systemctl")
        if not systemctl:
            return False
        try:
            res = subprocess.run(
                [str(systemctl), "--user", "is-system-running"],
                capture_output=True, text=True, timeout=10,
            )
        except (subprocess.SubprocessError, OSError):
            return False
        state = res.stdout.strip().lower()
        return bool(state) and state != "offline"

    def _maybe_start_tracemalloc(self) -> None:
        """Opt-in tracemalloc instrument for the 5GB-RSS-on-minimal-task bug. Activated by AIBUILDAI_TRACEMALLOC=1. Captures top-25 allocations grouped by file:lineno at process exit and writes them to /tmp/aibuildai_tracemalloc_<pid>.log. Disabled by default so production runs pay no overhead.
        """
        if os.environ.get("AIBUILDAI_TRACEMALLOC") != "1":
            return
        import tracemalloc
        tracemalloc.start(25)
        self._tracemalloc_log = f"/tmp/aibuildai_tracemalloc_{os.getpid()}.log"
        print(f"tracemalloc → {self._tracemalloc_log}", flush=True)

        tracemalloc_log = self._tracemalloc_log

        def _dump_tracemalloc() -> None:
            import tracemalloc as _tm
            if not _tm.is_tracing():
                return
            snap = _tm.take_snapshot()
            top_stats = snap.statistics("lineno")
            with open(tracemalloc_log, "w") as f:
                f.write(f"current,peak (MB): "
                        f"{_tm.get_traced_memory()[0] / 1024**2:.1f}, "
                        f"{_tm.get_traced_memory()[1] / 1024**2:.1f}\n\n")
                for stat in top_stats[:25]:
                    f.write(f"{stat.size / 1024**2:>8.2f} MB  {stat.count:>8} blocks  {stat.traceback}\n")
            _tm.stop()

        atexit.register(_dump_tracemalloc)

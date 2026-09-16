"""Run one ExecSpec on this host."""

from __future__ import annotations

import asyncio
import contextlib
import dataclasses
import logging
import os
import signal
import shutil
import stat
import subprocess
import time
from pathlib import Path
from typing import TYPE_CHECKING, Callable

from engine.capability import level_ceilings
from infra.exec.confinement_build import build_subproc_confinement
from infra.exec.executor import Executor
from infra.exec.outcome import ExecOutcome, InfraReason, KillCause
from infra.exec.spec import ExecCapture, ExecSpec, ProgramExecSpec
from infra.host_resource.cgroup import WorkerCgroupUnavailable
from infra.host_resource.confinement import OS_READ_BASELINE, Confinement
from infra.host_resource.cuda_probe import probe_cuda_visibility
from infra.host_resource.nvml import query_gpu_stats
from infra.host_resource.tree import kill_tree
from infra.sandbox.bwrap import device_dev_nodes
from infra.sandbox.gpu_nodes import device_minors, enumerate_nvidia_cap_nodes

if TYPE_CHECKING:
    from config import SandboxConfig
    from engine.program_environment import ProgramEnvironment
    from infra.host_resource.controller import HostResourceController

_logger = logging.getLogger(__name__)

_SIGKILL_WAIT_S = 5.0


class _CaptureBudget:
    """Keep bounded output tails and stop the cgroup at the combined byte cap."""

    def __init__(self, limit: int, cgroup: Path) -> None:
        self.limit = limit
        self.cgroup = cgroup
        self.total = 0
        self.exceeded = False
        self.stdout = bytearray()
        self.stderr = bytearray()

    async def drain(self, stream: asyncio.StreamReader | None, tail: bytearray) -> None:
        if stream is None:
            return
        tail_limit = max(1, self.limit // 2)
        while chunk := await stream.read(65536):
            self.total += len(chunk)
            tail.extend(chunk)
            if len(tail) > tail_limit:
                del tail[:-tail_limit]
            if self.total >= self.limit and not self.exceeded:
                self.exceeded = True
                kill_tree(self.cgroup)

    def decode(self, tail: bytearray) -> str:
        text = bytes(tail).decode("utf-8", errors="replace")
        return (
            f"[output truncated at {self.limit} bytes]\n{text}"
            if self.exceeded
            else text
        )


class _LocalProcess:
    """Run one local ``ExecSpec`` without Program placement semantics."""

    async def run(
        self,
        spec: ExecSpec,
        *,
        remaining_s: Callable[[], float],
    ) -> ExecOutcome:
        overall_start = time.monotonic()
        try:
            return await self._supervise(
                spec,
                remaining_s=remaining_s,
                overall_start=overall_start,
            )
        except asyncio.CancelledError:
            raise
        except TimeoutError:
            raise
        except Exception:
            _logger.exception(
                "unit_tag=%s: local process supervision failed", spec.unit_tag
            )
            return self._outcome(
                spec,
                infra_reason=InfraReason.SUPERVISOR_ERROR,
                consumed_s=time.monotonic() - overall_start,
            )

    async def _supervise(
        self,
        spec: ExecSpec,
        *,
        remaining_s: Callable[[], float],
        overall_start: float,
    ) -> ExecOutcome:
        # A check that refuses the manager environment still needs the system
        # path: the cgroup wrapper is spawned as a bare ``bash`` and the
        # interpreter bin is prepended to whatever PATH is here.
        env = os.environ.copy() if spec.inherit_env else {"PATH": os.defpath}
        device_indices = spec.capability.gpu_indices
        env["CUDA_VISIBLE_DEVICES"] = ",".join(str(i) for i in device_indices)
        if device_indices:
            env["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"
        env.update(spec.extra_env)

        argv = list(spec.argv)
        resolved_interp: str | None = None
        if spec.interpreter is not None:
            if not Path(spec.interpreter).is_file():
                return self._crash_outcome(
                    spec,
                    f"declared Python path no longer exists: {spec.interpreter!r}",
                    consumed_s=time.monotonic() - overall_start,
                )
            # The physical path: under a symlinked playground the declared one
            # can name a link the child would resolve differently.
            resolved_interp = str(Path(spec.interpreter).resolve())
            argv = [resolved_interp, *spec.argv]
            env["PATH"] = (
                f"{Path(resolved_interp).parent}{os.pathsep}{env.get('PATH', '')}"
            )

        if spec.require_gpu_use and device_indices:
            stats = await query_gpu_stats(min(spec.timeout_s, remaining_s()))
            present = {gpu.index for gpu in stats}
            if not all(index in present for index in device_indices):
                _logger.error(
                    "unit_tag=%s: host cannot present allocated GPUs %s",
                    spec.unit_tag,
                    list(device_indices),
                )
                return self._outcome(
                    spec,
                    infra_reason=InfraReason.HOST_GPU_UNAVAILABLE,
                    consumed_s=time.monotonic() - overall_start,
                )
            if resolved_interp is None:
                raise AssertionError(
                    "GPU-use enforcement requires a Program interpreter"
                )
            probe_ok, probe_diag = await probe_cuda_visibility(
                interpreter=resolved_interp,
                env=env,
                timeout_s=min(spec.timeout_s, remaining_s()),
            )
            if not probe_ok:
                _logger.error(
                    "unit_tag=%s: CUDA preflight failed: %s", spec.unit_tag, probe_diag
                )
                return self._crash_outcome(
                    spec, probe_diag, consumed_s=time.monotonic() - overall_start
                )

        if spec.apply_bwrap and shutil.which("bwrap") is None:
            _logger.error("unit_tag=%s: bwrap requested but not on PATH", spec.unit_tag)
            return self._outcome(
                spec,
                infra_reason=InfraReason.BWRAP_UNAVAILABLE,
                consumed_s=time.monotonic() - overall_start,
            )
        if not spec.cgroup:
            raise AssertionError(
                f"unit_tag={spec.unit_tag}: this unit was never placed in the resource "
                f"tree, so there is nowhere to launch it that the kernel is bounding"
            )
        cgroup = Path(spec.cgroup)
        dev_nodes = (
            (
                *device_dev_nodes(
                    await device_minors(
                        device_indices, min(spec.timeout_s, remaining_s())
                    )
                ),
                *enumerate_nvidia_cap_nodes(),
            )
            if spec.apply_bwrap and device_indices
            else ()
        )
        confinement = build_subproc_confinement(
            cgroup=cgroup,
            device_indices=device_indices,
            read_paths=spec.io_manifest.read_paths,
            write_paths=spec.io_manifest.write_paths,
            system_read_paths=spec.system_read_paths,
            system_write_paths=spec.system_write_paths,
            cwd=spec.cwd,
            apply_bwrap=spec.apply_bwrap,
            dev_nodes=dev_nodes,
            cwd_writable=spec.cwd_writable,
        )
        if confinement.apply_bwrap:
            try:
                confinement.require_readable(
                    *spec.required_system_read_paths,
                    owner=spec.unit_tag,
                )
                confinement.require_writable(
                    *spec.required_system_write_paths,
                    owner=spec.unit_tag,
                )
            except AssertionError as exc:
                return self._outcome(
                    spec,
                    infra_reason=InfraReason.SYSTEM_PATH_NOT_ALLOWED,
                    detail=str(exc),
                    consumed_s=time.monotonic() - overall_start,
                )
        oom_before = confinement.memory_kills()
        try:
            launch_argv = confinement.wrap(argv)
        except WorkerCgroupUnavailable:
            _logger.exception(
                "unit_tag=%s: user systemd unavailable for scope", spec.unit_tag
            )
            return self._outcome(
                spec,
                infra_reason=InfraReason.SYSTEMD_UNAVAILABLE,
                consumed_s=time.monotonic() - overall_start,
            )
        launch_cwd = confinement.cwd

        return await self._launch_and_wait(
            spec,
            launch_argv=launch_argv,
            launch_cwd=launch_cwd,
            env=env,
            remaining_s=remaining_s,
            overall_start=overall_start,
            confinement=confinement,
            oom_before=oom_before,
        )

    async def _launch_and_wait(
        self,
        spec: ExecSpec,
        *,
        confinement: Confinement,
        oom_before: int,
        launch_argv: list[str],
        launch_cwd: "str | None",
        env: dict[str, str],
        remaining_s: Callable[[], float],
        overall_start: float,
    ) -> ExecOutcome:
        log_file = None
        timed_out = False
        captured_rc: int | None = None
        captured_out: str | None = None
        captured_err: str | None = None
        output_limit_exceeded = False
        gpu_touched = False
        gpu_watch_failed = False
        gpu_watch: asyncio.subprocess.Process | None = None
        gpu_watch_task: asyncio.Task[bool] | None = None
        try:
            if spec.capture is ExecCapture.STREAM_TO_FILE:
                if spec.log_path is None:
                    raise AssertionError("STREAM_TO_FILE requires spec.log_path")
                log_file = open(spec.log_path, "a", encoding="utf-8", errors="replace")
                stdout_target: "int | object" = log_file
                stderr_target: int = subprocess.STDOUT
            else:
                stdout_target = subprocess.PIPE
                stderr_target = subprocess.PIPE

            timeout_s = max(0.0, min(spec.timeout_s, remaining_s()))
            if timeout_s == 0:
                return self._outcome(spec, timed_out=True, consumed_s=0.0)
            if spec.require_gpu_use and spec.capability.gpu_indices:
                gpu_watch = await asyncio.create_subprocess_exec(
                    "nvidia-smi",
                    f"--id={','.join(str(i) for i in spec.capability.gpu_indices)}",
                    "--query-compute-apps=pid",
                    "--format=csv,noheader,nounits",
                    "--loop-ms=100",
                    stdout=asyncio.subprocess.PIPE,
                    stderr=asyncio.subprocess.DEVNULL,
                )
                gpu_watch_task = asyncio.create_task(
                    self._observe_gpu_processes(gpu_watch)
                )
            try:
                proc = await asyncio.create_subprocess_exec(
                    *launch_argv,
                    stdout=stdout_target,
                    stderr=stderr_target,
                    cwd=launch_cwd,
                    env=env,
                    start_new_session=True,
                )
            except OSError:
                _logger.exception("unit_tag=%s: spawn failed", spec.unit_tag)
                return self._outcome(
                    spec,
                    infra_reason=InfraReason.SPAWN_FAILURE,
                    consumed_s=time.monotonic() - overall_start,
                )
            completion = asyncio.ensure_future(
                self._await_completion(
                    proc,
                    spec.capture,
                    spec.capture_limit_bytes,
                    confinement.cgroup,
                )
            )
            try:
                (
                    captured_rc,
                    captured_out,
                    captured_err,
                    output_limit_exceeded,
                ) = await asyncio.wait_for(
                    asyncio.shield(completion), timeout=timeout_s
                )
            except asyncio.CancelledError:
                _logger.warning(
                    "unit_tag=%s: cancelled externally; SIGTERM -> %.1fs stop wait -> SIGKILL",
                    spec.unit_tag,
                    spec.stop_wait_seconds,
                )
                await self._terminate(spec, proc, completion)
                if not completion.done():
                    completion.cancel()
                raise
            except asyncio.TimeoutError:
                timed_out = True
                _logger.warning(
                    "unit_tag=%s: exceeded %.0fs cap; SIGTERM -> %.1fs stop wait -> SIGKILL",
                    spec.unit_tag,
                    timeout_s,
                    spec.stop_wait_seconds,
                )
                await self._terminate(spec, proc, completion)
                if completion.done() and not completion.cancelled():
                    (
                        captured_rc,
                        captured_out,
                        captured_err,
                        output_limit_exceeded,
                    ) = completion.result()
                elif not completion.done():
                    completion.cancel()
        finally:
            if gpu_watch is not None:
                stopped_by_driver = False
                try:
                    gpu_watch.terminate()
                    stopped_by_driver = True
                except ProcessLookupError:
                    pass
                await gpu_watch.wait()
                gpu_watch_failed = not stopped_by_driver
            if gpu_watch_task is not None:
                gpu_touched = await gpu_watch_task
            if log_file is not None:
                log_file.close()

        consumed_s = time.monotonic() - overall_start
        if gpu_watch_failed:
            return self._outcome(
                spec,
                infra_reason=InfraReason.HOST_GPU_UNAVAILABLE,
                detail="the local GPU process observer stopped before the Program",
                consumed_s=consumed_s,
            )
        if timed_out:
            return self._outcome(
                spec,
                timed_out=True,
                consumed_s=timeout_s,
                stdout=captured_out,
                stderr=captured_err,
            )
        if output_limit_exceeded:
            return self._outcome(
                spec,
                output_limit_exceeded=True,
                consumed_s=consumed_s,
                stdout=captured_out,
                stderr=captured_err,
            )

        # Failure attribution, from the kernel. memory.events counts every OOM kill in
        # this unit's cgroup, for free, with no polling window -- so the delta across the
        # launch IS the answer, and it is exact.
        #
        # A kill supersedes the raw exit code (a killed process exits nonzero anyway), so
        # kill_cause is reported with return_code None to keep the single-failure-signal
        # invariant.
        if confinement.memory_kills() > oom_before:
            return self._outcome(
                spec, kill_cause=KillCause.MEMORY, consumed_s=consumed_s
            )
        if (
            spec.require_gpu_use
            and spec.capability.gpu_indices
            and captured_rc == 0
            and not gpu_touched
        ):
            return self._outcome(
                spec,
                return_code=0,
                gpu_unused=True,
                consumed_s=consumed_s,
            )
        # Local execution has no per-attempt disk cap. A filesystem write failure
        # stays the payload's own error and needs no kill attribution here.

        return self._outcome(
            spec,
            return_code=captured_rc,
            consumed_s=consumed_s,
            stdout=captured_out,
            stderr=captured_err,
        )

    @staticmethod
    async def _await_completion(
        proc: "asyncio.subprocess.Process",
        capture: ExecCapture,
        capture_limit_bytes: int | None,
        cgroup: Path,
    ) -> "tuple[int | None, str | None, str | None, bool]":
        """Wait for the child to exit; for PIPE_CAPTURE also drain + decode its pipes concurrently (a full pipe buffer would otherwise deadlock the child). Returns ``(return_code, stdout, stderr, output_limit_exceeded)`` — stdout/stderr None under STREAM_TO_FILE (output went to the log file)."""
        if capture is ExecCapture.PIPE_CAPTURE:
            if capture_limit_bytes is not None:
                if capture_limit_bytes <= 0:
                    raise AssertionError("capture_limit_bytes must be positive")
                budget = _CaptureBudget(capture_limit_bytes, cgroup)
                await asyncio.gather(
                    proc.wait(),
                    budget.drain(proc.stdout, budget.stdout),
                    budget.drain(proc.stderr, budget.stderr),
                )
                return (
                    proc.returncode,
                    budget.decode(budget.stdout),
                    budget.decode(budget.stderr),
                    budget.exceeded,
                )
            out_b, err_b = await proc.communicate()
            out = out_b.decode("utf-8", errors="replace") if out_b is not None else ""
            err = err_b.decode("utf-8", errors="replace") if err_b is not None else ""
            return proc.returncode, out, err, False
        rc = await proc.wait()
        return rc, None, None, False

    async def _terminate(
        self,
        spec: ExecSpec,
        proc: "asyncio.subprocess.Process",
        completion: "asyncio.Future",
    ) -> None:
        """SIGTERM -> stop wait -> SIGKILL -> 5s wait. Waits on the already-running ``completion`` task so PIPE_CAPTURE pipes are drained as the child dies."""
        with contextlib.suppress(ProcessLookupError):
            # bwrap supervises the sandbox payload instead of replacing itself,
            # so signaling only ``proc`` never reaches the actual command. The
            # dedicated process group contains both and gives the payload its
            # requested stop wait.
            os.killpg(proc.pid, signal.SIGTERM)
        try:
            await asyncio.wait_for(
                asyncio.shield(completion), timeout=spec.stop_wait_seconds
            )
            return
        except asyncio.TimeoutError:
            pass
        # The WorkUnit cgroup is its complete process ownership boundary.
        # cgroup.kill is atomic against forks, so the forced stop cannot leave a
        # sandbox payload behind after killing only its bwrap supervisor.
        kill_tree(Path(spec.cgroup))
        try:
            await asyncio.wait_for(asyncio.shield(completion), timeout=_SIGKILL_WAIT_S)
        except asyncio.TimeoutError:
            _logger.warning(
                "unit_tag=%s: subprocess did not exit %.1fs after SIGKILL; orphaned",
                spec.unit_tag,
                _SIGKILL_WAIT_S,
            )

    def _outcome(
        self,
        spec: ExecSpec,
        *,
        consumed_s: float,
        return_code: int | None = None,
        timed_out: bool = False,
        kill_cause: KillCause = KillCause.NONE,
        infra_reason: InfraReason = InfraReason.NONE,
        stdout: str | None = None,
        stderr: str | None = None,
        detail: str | None = None,
        output_limit_exceeded: bool = False,
        gpu_unused: bool = False,
    ) -> ExecOutcome:
        """Build an ExecOutcome, placing captured output in the field matching the capture mode: STREAM_TO_FILE -> log_path (stdout/stderr None); PIPE_CAPTURE -> stdout/stderr (log_path None). ``detail`` carries exact mechanism text into the user-visible failure reason. Cancellation never reaches here: it re-raises out of the supervisor."""
        if spec.capture is ExecCapture.STREAM_TO_FILE:
            return ExecOutcome(
                return_code=return_code,
                timed_out=timed_out,
                kill_cause=kill_cause,
                infra_reason=infra_reason,
                consumed_s=consumed_s,
                log_path=spec.log_path,
                stdout=None,
                stderr=None,
                detail=detail,
                output_limit_exceeded=output_limit_exceeded,
                gpu_unused=gpu_unused,
            )
        return ExecOutcome(
            return_code=return_code,
            timed_out=timed_out,
            kill_cause=kill_cause,
            infra_reason=infra_reason,
            consumed_s=consumed_s,
            log_path=None,
            stdout=stdout,
            stderr=stderr,
            detail=detail,
            output_limit_exceeded=output_limit_exceeded,
            gpu_unused=gpu_unused,
        )

    @staticmethod
    async def _observe_gpu_processes(proc: asyncio.subprocess.Process) -> bool:
        if proc.stdout is None:
            raise AssertionError("GPU process watcher has no stdout")
        touched = False
        while line := await proc.stdout.readline():
            touched = touched or line.decode(errors="replace").strip().isdigit()
        return touched

    def _crash_outcome(
        self, spec: ExecSpec, diagnostic: str, *, consumed_s: float
    ) -> ExecOutcome:
        """Return a crash outcome when the CUDA check fails before spawn."""
        if spec.capture is ExecCapture.STREAM_TO_FILE:
            if spec.log_path is None:
                raise AssertionError("STREAM_TO_FILE requires spec.log_path")
            with open(spec.log_path, "a", encoding="utf-8", errors="replace") as f:
                f.write(diagnostic + "\n")
            return ExecOutcome(
                return_code=None,
                timed_out=False,
                kill_cause=KillCause.NONE,
                infra_reason=InfraReason.NONE,
                consumed_s=consumed_s,
                log_path=spec.log_path,
                stdout=None,
                stderr=None,
                detail=diagnostic,
            )
        return ExecOutcome(
            return_code=None,
            timed_out=False,
            kill_cause=KillCause.NONE,
            infra_reason=InfraReason.NONE,
            consumed_s=consumed_s,
            log_path=None,
            stdout=None,
            stderr=diagnostic,
            detail=diagnostic,
        )


class LocalExecutor(Executor):
    """Place every formal Program attempt on this host."""

    def __init__(
        self,
        *,
        host: "HostResourceController",
        environment: "ProgramEnvironment",
        sandbox: "SandboxConfig",
    ) -> None:
        self._host = host
        self._environment = environment
        self._sandbox = sandbox

    async def run_program(
        self,
        spec: ProgramExecSpec,
        *,
        owner_path: str,
        remaining_s: Callable[[], float],
    ) -> ExecOutcome:
        """Place, run, and release one local Program on the run's environment."""
        ceilings = level_ceilings(spec.capability)
        output_kinds = {
            Path(raw): _program_output_kind(Path(raw))
            for raw in spec.io_manifest.write_paths
        }
        placement_created = False
        try:
            # The Action was placed on its exact cards before it was recorded,
            # so this launch reads that placement instead of asking for one.
            # Another Action may hold the same card; that is allowed.
            process, _commands = self._host.place_work_unit(
                owner_path,
                "program",
                memory_max_bytes=ceilings["memory_max_bytes"],
                cpu_max_millicores=ceilings["cpu_max_millicores"],
            )
            placement_created = True
            python_path = self._environment.python
            environment_root = str(Path(python_path).resolve().parent.parent)
            local_spec = ExecSpec(
                argv=["-m", spec.module, *spec.args],
                cwd=spec.cwd,
                io_manifest=type(spec.io_manifest)(
                    read_paths=(*spec.io_manifest.read_paths, environment_root),
                    write_paths=spec.io_manifest.write_paths,
                ),
                capability=spec.capability,
                timeout_s=spec.timeout_s,
                stop_wait_seconds=spec.stop_wait_seconds,
                cgroup=str(process),
                apply_bwrap=self._sandbox.enable,
                # The exact system view of a Program: the operating system
                # it runs on and what the operator added. Its interpreter
                # environment is a read path of its own manifest above.
                system_read_paths=(
                    *OS_READ_BASELINE,
                    *self._sandbox.system_read_paths,
                ),
                system_write_paths=self._sandbox.system_write_paths,
                required_system_read_paths=(environment_root,),
                required_system_write_paths=(),
                capture=ExecCapture.STREAM_TO_FILE,
                log_path=spec.log_path,
                unit_tag=spec.unit_tag,
                interpreter=python_path,
                extra_env=spec.extra_env,
                require_gpu_use=spec.require_gpu_use,
            )
            outcome = await _LocalProcess().run(local_spec, remaining_s=remaining_s)
            if outcome.return_code == 0 and not outcome.gpu_unused:
                try:
                    for path, original_kind in output_kinds.items():
                        current_kind = _program_output_kind(path)
                        if current_kind != original_kind:
                            raise AssertionError(
                                f"Program output changed from {original_kind} "
                                f"to {current_kind}: {path}"
                            )
                        _validate_program_output(path)
                except AssertionError as exc:
                    outcome = dataclasses.replace(
                        outcome, return_code=1, detail=str(exc)
                    )
            return outcome
        finally:
            if placement_created:
                self._host.release_work_unit(owner_path)


def _program_output_kind(path: Path) -> str:
    try:
        status = path.stat(follow_symlinks=False)
    except FileNotFoundError as exc:
        raise AssertionError(f"Program output is missing: {path}") from exc
    if stat.S_ISLNK(status.st_mode):
        raise AssertionError(f"Program output must not be a symlink: {path}")
    if stat.S_ISDIR(status.st_mode):
        return "directory"
    if stat.S_ISREG(status.st_mode):
        return "file"
    raise AssertionError(f"Program output must be a file or directory: {path}")


def _validate_program_output(path: Path) -> None:
    status = path.stat(follow_symlinks=False)
    if stat.S_ISDIR(status.st_mode) and not stat.S_ISLNK(status.st_mode):
        for child in path.iterdir():
            _validate_program_output(child)
        return
    if (
        stat.S_ISLNK(status.st_mode)
        or not stat.S_ISREG(status.st_mode)
        or status.st_nlink != 1
    ):
        raise AssertionError(
            f"Program output must be a regular single-link file tree: {path}"
        )

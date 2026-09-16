"""Prepare the one Program environment owned by a run."""

from __future__ import annotations

import asyncio
import fcntl
import hashlib
import logging
import os
import shutil
import sys
import tempfile
import threading
from collections.abc import Callable
from pathlib import Path

from engine.failure import Failure, FailureKind
from infra.owned_tools import micromamba_root_prefix, owned
from infra.process.command import _BlockingStopped, run_blocking, run_captured
from infra.process.conda_env import conda_executable, resolve_task_interpreter_or_none
from aibuildai_version import (
    APP_LINE,
    APP_VERSION,
    RUNTIME_PACKAGES,
    RUNTIME_TOP_LEVEL_MODULES,
)

logger = logging.getLogger(__name__)

_BUILD_CLEANUP_TIMEOUT_S = 300.0


_RUNTIME_PYTHON_FLOOR = (3, 11)
_RUNTIME_PYTHON_CEILING = (3, 14)


class ProgramEnvironmentError(Exception):
    """A known preparation failure that leaves the run resumable."""

    def __init__(self, failure: Failure) -> None:
        super().__init__(failure.reason)
        self.failure = failure


async def _require_runtime_capable_base(
    base_python: str, conda_env_name: str, remaining_s: Callable[[], float]
) -> None:
    """Refuse a clone base whose interpreter cannot host the Program runtime.

    The environment named by ``run.conda_env`` is cloned and then has the
    AIBuildAI Program runtime installed into it, so its interpreter must satisfy
    the runtime's own ``requires-python``. Without this check a base outside that
    range is cloned first -- minutes of copying -- and then fails inside pip with
    "Package 'aibuildai' requires a different Python", which names pip rather
    than the setting that chose the base. The floor is real rather than
    conservative metadata: the runtime uses ``BaseExceptionGroup``, a 3.11
    builtin.
    """
    probe = await run_captured(
        [base_python, "-c", "import sys; print('%d.%d' % sys.version_info[:2])"],
        remaining_s(),
    )
    raw = (probe.stdout or "").strip()
    if probe.returncode != 0 or not raw:
        # An unreadable base is the clone's problem to report, not this check's.
        return
    try:
        version = tuple(int(part) for part in raw.split("."))
    except ValueError:
        return
    if _RUNTIME_PYTHON_FLOOR <= version < _RUNTIME_PYTHON_CEILING:
        return
    floor = ".".join(str(n) for n in _RUNTIME_PYTHON_FLOOR)
    ceiling = ".".join(str(n) for n in _RUNTIME_PYTHON_CEILING)
    raise ProgramEnvironmentError(
        Failure(
            kind=FailureKind.INFRA,
            reason=f"run.conda_env names {conda_env_name!r}, whose interpreter is Python "
            f"{raw} ({base_python}). It is cloned into this run's Program "
            f"environment and the AIBuildAI Program runtime is installed into that "
            f"clone, so it must be Python >={floor},<{ceiling}. Rebuild that "
            f"environment on a supported interpreter, point run.conda_env at one "
            f"that is, or unset run.conda_env to get a fresh Python "
            f"{floor} environment that Setup fills itself.",
        )
    )


class ProgramEnvironment:
    """The one real environment directory every task consumer of this run uses.

    Setup installs the task's packages into it, and each later Program, Setup's
    own score check, and each task-environment Agent run against that same
    interpreter. It is an ordinary resource of the run home, not a durable
    lifecycle fact. ``prepare()`` makes it ready and is the only thing that
    writes it, so each of those consumers awaits ``prepare()`` before it
    launches: the Program worker launch and the Agent launch spec both do,
    and whoever arrives second waits rather than writing the directory the
    first is writing.
    """

    def __init__(self, *, run_home: Path, conda_env_name: str) -> None:
        self._root = run_home / "program-environment"
        self._prefix = self._root / "env"
        self._conda_env_name = conda_env_name
        self._gate = asyncio.Lock()
        self._prepared = False

    @property
    def python(self) -> str:
        path = self._prefix / "bin" / "python"
        if not path.is_file():
            raise AssertionError("Program environment is not prepared")
        return str(path)

    async def prepare(self, remaining_s: Callable[[], float]) -> None:
        """Make this run's Program environment ready, once per process.

        The runtime install runs whether or not the interpreter is already
        there, because the interpreter existing is not the same as the
        environment being ready: a process killed between the create and the
        install leaves a Python with no Program runtime in it, and reusing
        that would launch every later Program against an interpreter that
        cannot import its own worker. pip makes the repeat cheap, and the
        version check that ends the install is the answer either way. A resume
        is a new process, so it does that work again.

        Readiness is one fact and this is where it is established, so every
        caller that needs the environment awaits THIS -- the run's own
        lifecycle at the start, and each Program worker launch. Whoever
        arrives second waits for the first to finish instead of installing
        into the directory the first is writing: a resume reinstalls the
        runtime at the moment durable retry relaunches a worker, and the
        worker died importing a module out of the site-packages being
        rewritten under it.
        """
        try:
            async with self._gate:
                if self._prepared:
                    return
                python_path = self._prefix / "bin" / "python"
                if not python_path.is_file():
                    await self._create(remaining_s)
                wheel = await self._runtime_wheel(remaining_s)
                await self._install_runtime(wheel, remaining_s)
                self._prepared = True
        except TimeoutError as exc:
            raise ProgramEnvironmentError(
                Failure(
                    kind=FailureKind.TIMEOUT,
                    reason="Program environment preparation reached its time limit",
                )
            ) from exc
        except OSError as exc:
            raise ProgramEnvironmentError(
                Failure(
                    kind=FailureKind.INFRA,
                    reason=f"Program environment preparation failed: {type(exc).__name__}: {exc}",
                )
            ) from exc

    async def _create(self, remaining_s: Callable[[], float]) -> None:
        """Clone the named host environment, or make a fresh one."""
        self._root.mkdir(parents=True, exist_ok=True)
        if self._prefix.exists():
            await run_blocking(
                lambda stop: _remove_tree(self._prefix, stop),
                remaining_s(),
            )
        base_python = await resolve_task_interpreter_or_none(
            conda_env_name=self._conda_env_name,
            remaining_s=remaining_s,
        )
        if base_python is None:
            # The owned micromamba creates the fresh environment, so no
            # host conda is required, and every input that decides what
            # lands in it is pinned by the product: the binary, the
            # conda-forge channel (--no-rc shuts out user and system rc
            # files, whose default channels can point at Anaconda's
            # commercially licensed repository), and the owned root prefix
            # holding the package cache.
            create = [
                str(owned("micromamba")),
                "create",
                "--yes",
                "--no-rc",
                "--root-prefix",
                str(micromamba_root_prefix()),
                "--channel",
                "conda-forge",
                "--prefix",
                str(self._prefix),
                "python=3.11",
                "pip",
            ]
        else:
            # Cloning a named host environment stays on the host conda that
            # owns it; conda_executable() explains why micromamba cannot.
            await _require_runtime_capable_base(
                base_python, self._conda_env_name, remaining_s
            )
            create = [
                conda_executable(),
                "create",
                "--yes",
                "--prefix",
                str(self._prefix),
                "--clone",
                str(Path(base_python).parent.parent),
            ]
        created = await run_captured(
            create,
            remaining_s(),
        )
        if created.returncode != 0:
            raise ProgramEnvironmentError(
                Failure(
                    kind=FailureKind.INFRA,
                    reason=f"could not create Program environment {self._conda_env_name!r}: "
                    f"{(created.stderr or created.stdout)[-2000:]}",
                )
            )
        if not (self._prefix / "bin" / "python").is_file():
            raise AssertionError(
                f"Program environment {self._conda_env_name!r} has no Python"
            )

    async def _runtime_wheel(self, remaining_s: Callable[[], float]) -> Path:
        source_root = Path(__file__).resolve().parent.parent
        cache_root = Path.home() / ".cache" / "aibuildai" / "program-runtime"
        cache_root.mkdir(parents=True, exist_ok=True)
        private_root = Path(tempfile.mkdtemp(prefix="runtime-wheel-", dir=cache_root))
        build_root = private_root / "source"
        output_root = private_root / "output"
        lock_fd: int | None = None
        primary_error: BaseException | None = None
        try:
            # The key and the wheel read the same staged bytes. Git can hide
            # a source edit, and a dirty tree can change without changing HEAD.
            source_digest = await run_blocking(
                lambda stop: self._stage_runtime_source(source_root, build_root, stop),
                remaining_s(),
            )
            wheel_dir = cache_root / source_digest
            wheel_dir.mkdir(parents=True, exist_ok=True)
            print(
                f"Program runtime {APP_VERSION}, source {source_digest}",
                file=sys.stderr,
            )
            lock_fd = await run_blocking(
                lambda stop: _acquire_lock(cache_root / f".{source_digest}.lock", stop),
                remaining_s(),
            )
            wheels = sorted(wheel_dir.glob("aibuildai-*.whl"))
            if len(wheels) == 1:
                return wheels[0]
            for wheel in wheels:
                wheel.unlink()
            output_root.mkdir()
            built = await run_captured(
                [
                    sys.executable,
                    "-m",
                    "pip",
                    "wheel",
                    "--no-deps",
                    "--wheel-dir",
                    str(output_root),
                    str(build_root),
                ],
                remaining_s(),
                cwd=build_root,
            )
            if built.returncode != 0:
                raise ProgramEnvironmentError(
                    Failure(
                        kind=FailureKind.INFRA,
                        reason="could not build Program runtime wheel: "
                        f"{(built.stderr or built.stdout)[-4000:]}",
                    )
                )
            built_wheels = sorted(output_root.glob("aibuildai-*.whl"))
            if len(built_wheels) != 1:
                raise ProgramEnvironmentError(
                    Failure(
                        kind=FailureKind.INFRA,
                        reason=f"Program runtime build produced {len(built_wheels)} wheels",
                    )
                )
            published = wheel_dir / built_wheels[0].name
            built_wheels[0].replace(published)
            return published
        except BaseException as exc:
            primary_error = exc
            raise
        finally:
            cleanup_error: BaseException | None = None
            try:
                if lock_fd is not None:
                    os.close(lock_fd)
            except OSError as exc:
                cleanup_error = exc
            try:
                await run_blocking(
                    lambda stop: _remove_tree(private_root, stop),
                    _BUILD_CLEANUP_TIMEOUT_S,
                )
            except BaseException as exc:
                if cleanup_error is None:
                    cleanup_error = exc
                else:
                    cleanup_error.add_note(f"Private source cleanup also failed: {exc}")
                    logger.exception("Private source cleanup failed")
            if cleanup_error is not None:
                if primary_error is None:
                    raise cleanup_error
                primary_error.add_note(
                    f"Runtime wheel cleanup also failed: {cleanup_error}"
                )
                logger.error("Runtime wheel cleanup failed", exc_info=cleanup_error)

    @staticmethod
    def _stage_runtime_source(
        source_root: Path, build_root: Path, stop: "threading.Event"
    ) -> str:
        if build_root.exists():
            _remove_tree(build_root, stop)
        build_root.mkdir(parents=True)
        for relative in (
            *RUNTIME_TOP_LEVEL_MODULES,
            "pyproject.toml",
            "README.md",
        ):
            _check_stop(stop)
            shutil.copy2(source_root / relative, build_root / relative)
        for package in RUNTIME_PACKAGES:
            _check_stop(stop)
            shutil.copytree(
                source_root / package,
                build_root / package,
                ignore=shutil.ignore_patterns(
                    "__pycache__", "*.pyc", "build", "node_modules", ".ruff_cache"
                ),
            )
        version_path = build_root / "aibuildai_version.py"
        version_source = version_path.read_text(encoding="utf-8")
        marker = "APP_VERSION, APP_LINE = _derive_version()"
        if version_source.count(marker) != 1:
            raise AssertionError(
                "aibuildai_version.py must declare APP_VERSION and APP_LINE exactly once"
            )
        version_path.write_text(
            version_source.replace(
                marker,
                f"APP_VERSION = {APP_VERSION!r}\nAPP_LINE = {APP_LINE!r}",
            ),
            encoding="utf-8",
        )

        digest = hashlib.sha256()
        for path in sorted(build_root.rglob("*")):
            _check_stop(stop)
            if path.is_file():
                digest.update(path.relative_to(build_root).as_posix().encode("utf-8"))
                digest.update(b"\0")
                with path.open("rb") as stream:
                    digest.update(hashlib.file_digest(stream, "sha256").digest())
        return digest.hexdigest()

    async def _install_runtime(
        self, wheel: Path, remaining_s: Callable[[], float]
    ) -> None:
        python_path = self.python
        for extra in ((), ("--force-reinstall", "--no-deps")):
            installed = await run_captured(
                [
                    python_path,
                    "-I",
                    "-m",
                    "pip",
                    "install",
                    *extra,
                    str(wheel),
                ],
                remaining_s(),
            )
            if installed.returncode != 0:
                raise ProgramEnvironmentError(
                    Failure(
                        kind=FailureKind.INFRA,
                        reason="could not install the Program runtime: "
                        f"{(installed.stderr or installed.stdout)[-4000:]}",
                    )
                )
        checked = await run_captured(
            [
                python_path,
                "-I",
                "-c",
                "import importlib.metadata; print(importlib.metadata.version('aibuildai'))",
            ],
            remaining_s(),
        )
        if checked.returncode != 0 or checked.stdout.strip() != APP_VERSION:
            raise AssertionError(
                "Program runtime version differs from this run: "
                f"expected {APP_VERSION}, got {checked.stdout.strip()!r}"
            )


def _check_stop(stop: threading.Event) -> None:
    if stop.is_set():
        raise _BlockingStopped


def _remove_tree(root: Path, stop: threading.Event) -> None:
    if root.is_symlink():
        root.unlink(missing_ok=True)
        return
    if not root.exists():
        return
    for current, dirs, files in os.walk(root, topdown=False):
        _check_stop(stop)
        current_path = Path(current)
        for name in files:
            _check_stop(stop)
            (current_path / name).unlink(missing_ok=True)
        for name in dirs:
            _check_stop(stop)
            path = current_path / name
            if path.is_symlink():
                path.unlink(missing_ok=True)
            else:
                path.rmdir()
    root.rmdir()


def _acquire_lock(path: Path, stop: threading.Event) -> int:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd = os.open(path, os.O_CREAT | os.O_RDWR, 0o600)
    try:
        while True:
            _check_stop(stop)
            try:
                fcntl.flock(fd, fcntl.LOCK_EX | fcntl.LOCK_NB)
                return fd
            except BlockingIOError:
                if stop.wait(0.1):
                    raise _BlockingStopped from None
    except BaseException as primary:
        try:
            os.close(fd)
        except OSError as cleanup:
            primary.add_note(f"Runtime wheel lock cleanup also failed: {cleanup}")
            logger.exception("Runtime wheel lock cleanup failed")
        raise

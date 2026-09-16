"""Own the private local Run environment and its single Resume launch path."""

from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from config import StartupUserInputError
from engine.event import store as event_store
from infra.fs.pathing import (
    _assert_owner_only_file_mode,
    atomic_write_owner_only_text,
    fsync_directory,
)
from infra.host_resource.cgroup import run_unit_name
from infra.process.transient_unit import start_transient_user_unit
from startup.run_catalog import (
    KUBERNETES_CONTROL_UNAVAILABLE,
    IndexedRun,
    run_active,
    run_facts,
)

_RESUME_RESULT_ENV = "AIBUILDAI_RESUME_RESULT_FILE"


class WebSafeDiagnostic(StartupUserInputError):
    """A public startup message allowed to cross into Web."""


def _require_private_file(path: Path) -> None:
    if path.is_symlink() or not path.is_file():
        raise ValueError("this Run has no private launch environment")
    _assert_owner_only_file_mode(path, purpose="AIBuildAI private Run launch state")


def _launch_root() -> Path:
    product_root = Path.home() / ".aibuildai"
    if product_root.is_symlink():
        raise ValueError("the private AIBuildAI directory is a symlink")
    product_root.mkdir(mode=0o700, parents=True, exist_ok=True)
    root = product_root / "run-launch"
    if root.is_symlink():
        raise ValueError("the private Run launch directory is a symlink")
    root.mkdir(mode=0o700, exist_ok=True)
    os.chmod(root, 0o700)
    return root


def _environment_path(run_id: str) -> Path:
    return _launch_root() / f"{run_id}.environment"


def publish_run_launch_context(run_id: str, source: Path) -> None:
    """Publish the exact private pre-systemd caller EnvironmentFile."""
    _require_private_file(source)
    target = _environment_path(run_id)
    if target.exists() or target.is_symlink():
        raise ValueError(f"Run {run_id} already has a private launch environment")
    try:
        atomic_write_owner_only_text(
            target,
            source.read_text(encoding="utf-8"),
            purpose="AIBuildAI private Run launch environment",
        )
        source.unlink()
        fsync_directory(source.parent)
    except BaseException:
        target.unlink(missing_ok=True)
        fsync_directory(target.parent)
        raise


def load_run_launch_context(run_id: str) -> Path:
    path = _environment_path(run_id)
    _require_private_file(path)
    return path


def remove_run_launch_context(run_id: str) -> None:
    path = _environment_path(run_id)
    path.unlink(missing_ok=True)
    fsync_directory(path.parent)


def resume_refusal(run: IndexedRun) -> str | None:
    """Return the complete startup-owned refusal for one Web Resume."""
    if run.identity.kubernetes_start_mode:
        return KUBERNETES_CONTROL_UNAVAILABLE
    if run_active(run):
        return f"run {run.run_id} is still running in another process"
    facts = run_facts(run.run_id, run.run_home)
    if facts.result is not None:
        return "the run has finished"
    if facts.incompatible is not None:
        return facts.incompatible
    if not facts.readable:
        return "the run has not started and cannot resume"
    try:
        load_run_launch_context(run.run_id)
    except ValueError as error:
        return str(error)
    return None


def _result_root() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise ValueError("the startup launcher has no private runtime directory")
    root = Path(runtime) / "aibuildai" / "resume-results"
    root.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(root, 0o700)
    return root


def start_existing_run(
    run: IndexedRun, workspace_name: str, workspace_port: int
) -> tuple[str, Path]:
    """Start one existing Run after one complete authoritative refusal check."""
    refusal = resume_refusal(run)
    if refusal is not None:
        raise ValueError(refusal)
    environment_file = load_run_launch_context(run.run_id)
    app = event_store.open_search_store(run.run_id, read_only=True)
    try:
        first_epoch = event_store.read_first_epoch(app)
    finally:
        app.close()
    if first_epoch is None:
        raise ValueError("the Run has no recorded first process epoch")
    command = (sys.executable, str(Path(__file__).resolve().parents[1] / "cli.py"))
    unit = run_unit_name(run.run_id)
    result_file = _result_root() / f"{run.run_id}.json"
    result_file.unlink(missing_ok=True)
    try:
        start_transient_user_unit(
            unit=unit,
            command=(*command, "__resume_run", run.run_id),
            environment={
                "AIBUILDAI_IN_SERVICE": "1",
                "AIBUILDAI_WEB_NAME": workspace_name,
                "AIBUILDAI_WEB_PORT": str(workspace_port),
                _RESUME_RESULT_ENV: str(result_file),
            },
            environment_file=environment_file,
            properties=(
                "Delegate=yes",
                "KillMode=mixed",
                f"WorkingDirectory={first_epoch.cwd}",
            ),
        )
    except RuntimeError as error:
        raise ValueError(str(error)) from error
    return unit, result_file


def claim_resume_result(run_id: str) -> Path:
    raw_result = os.environ.pop(_RESUME_RESULT_ENV, None)
    if raw_result is None:
        raise ValueError("the existing-Run process has no startup result path")
    result_file = Path(raw_result)
    if result_file.name != f"{run_id}.json":
        raise ValueError("the existing-Run startup result path is malformed")
    return result_file


def write_resume_failure(
    result_file: Path, diagnostic: WebSafeDiagnostic
) -> None:
    atomic_write_owner_only_text(
        result_file,
        json.dumps({"error": str(diagnostic)}),
        purpose="AIBuildAI private Resume result",
    )


def read_resume_failure(result_file: Path) -> str | None:
    if not result_file.exists():
        return None
    try:
        _require_private_file(result_file)
        payload = json.loads(result_file.read_text(encoding="utf-8"))
        error = payload.get("error") if isinstance(payload, dict) else None
        if not isinstance(error, str) or not error:
            return "the resumed process returned an invalid private startup result"
        return error
    except (OSError, ValueError):
        return "the resumed process returned an unreadable private startup result"
    finally:
        result_file.unlink(missing_ok=True)

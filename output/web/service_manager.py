"""Deploy private backend generations for one named Workspace through Caddy."""

from __future__ import annotations

import fcntl
import logging
import os
import secrets
import subprocess
import sys
import time
from pathlib import Path

import httpx

from infra.process.transient_unit import start_transient_user_unit, stop_transient_user_unit
from output.web.app import verify_frontend_assets, web_build_id
from output.web.caddy import (
    backend_dir,
    backend_socket_path,
    configured_backends,
    ensure_gateway,
    gateway_ready,
    load_gateway_route,
)
from output.web.live_registry import (
    MEMBER_PROTOCOL_VERSION,
    NAME_ENV,
    PORT_ENV,
    WEB_PROTOCOL_VERSION,
    fixed_port,
    runtime_dir,
    workspace_name,
)
from output.web.models import WorkspaceHealth
from aibuildai_version import APP_VERSION

BACKEND_UNIT_PREFIX = "aibuildai-web-backend-"
HEALTH_TIMEOUT_S = 1.0
START_TIMEOUT_S = 15.0
# The only environment a service gets from the run that starts it: where the
# product is, where the run index is, and which name and port to own. Never a
# provider credential, never the run's whole environment.
_PASSED_ENV = (
    "PATH",
    "PYTHONPATH",
    "XDG_DATA_HOME",
    "HOME",
    NAME_ENV,
    PORT_ENV,
)

logger = logging.getLogger(__name__)


class PortOwnedElsewhere(RuntimeError):
    """The fixed port answers, but not as the requested AIBuildAI Workspace."""


def own_command() -> list[str]:
    """Start the product tree that supplied this deployed backend."""
    return [sys.executable, str(Path(__file__).resolve().parents[2] / "cli.py")]


def origin() -> str:
    return f"http://127.0.0.1:{fixed_port()}"


def probe_health() -> WorkspaceHealth | None:
    """The public health, or None when nothing accepts the connection."""
    try:
        response = httpx.get(f"{origin()}/api/v1/health", timeout=HEALTH_TIMEOUT_S)
    except httpx.ConnectError:
        return None
    except httpx.HTTPError as exc:
        raise PortOwnedElsewhere(f"127.0.0.1:{fixed_port()} answered, but not as an AIBuildAI Workspace ({exc!r})") from exc
    if response.status_code != 200:
        raise PortOwnedElsewhere(f"127.0.0.1:{fixed_port()} answered HTTP {response.status_code} to the Workspace health probe, so another program owns it")
    try:
        return WorkspaceHealth.model_validate(response.json())
    except ValueError as exc:
        raise PortOwnedElsewhere(f"127.0.0.1:{fixed_port()} answered the health probe with something that is not a Workspace health record") from exc


def _require_gateway_port_available() -> None:
    health = probe_health()
    if health is not None:
        raise PortOwnedElsewhere(
            f"127.0.0.1:{fixed_port()} is already used by Workspace {health.workspace_name!r}; "
            f"stop the program that owns the port or set {PORT_ENV} to a free port"
        )


def _compatible(health: WorkspaceHealth | None, build_id: str) -> bool:
    return (
        health is not None
        and _protocol_compatible(health)
        and health.app_version == APP_VERSION
        and health.web_build_id == build_id
    )


def _protocol_compatible(health: WorkspaceHealth | None) -> bool:
    return health is not None and (
        health.workspace_name,
        health.web_protocol_version,
        health.member_protocol_version,
    ) == (workspace_name(), WEB_PROTOCOL_VERSION, MEMBER_PROTOCOL_VERSION)


def _health(client: httpx.Client) -> WorkspaceHealth | None:
    try:
        response = client.get("/api/v1/health")
    except httpx.HTTPError:
        return None
    if response.status_code != 200:
        return None
    try:
        return WorkspaceHealth.model_validate_json(response.content)
    except ValueError:
        return None


def public_health() -> WorkspaceHealth | None:
    with httpx.Client(base_url=origin(), timeout=HEALTH_TIMEOUT_S) as client:
        return _health(client)


def _public_is_compatible(build_id: str) -> bool:
    return gateway_ready() and _compatible(public_health(), build_id)


def backend_health(socket_path: Path) -> WorkspaceHealth | None:
    transport = httpx.HTTPTransport(uds=str(socket_path))
    with httpx.Client(transport=transport, base_url="http://127.0.0.1", timeout=HEALTH_TIMEOUT_S) as client:
        return _health(client)


class DeploymentLock:
    """The user-private lock for one atomic gateway deployment attempt."""

    def __init__(self) -> None:
        self._fd: int | None = None

    def __enter__(self) -> "DeploymentLock":
        self._fd = os.open(
            runtime_dir() / f"deployment-{workspace_name()}-{fixed_port()}.lock",
            os.O_RDWR | os.O_CREAT,
            0o600,
        )
        fcntl.flock(self._fd, fcntl.LOCK_EX)
        return self

    def __exit__(self, *exc: object) -> None:
        if self._fd is not None:
            fcntl.flock(self._fd, fcntl.LOCK_UN)
            os.close(self._fd)
            self._fd = None


def _environment(names: tuple[str, ...]) -> dict[str, str]:
    return {name: os.environ[name] for name in names if name in os.environ}


def _backend_unit(socket_path: Path) -> str:
    key = socket_path.stem
    if not key or socket_path.parent != backend_dir() or socket_path.suffix != ".sock":
        raise RuntimeError(f"not an AIBuildAI Workspace backend socket: {socket_path}")
    return f"{BACKEND_UNIT_PREFIX}{workspace_name()}-{fixed_port()}-{key}"


def _start_backend() -> Path:
    generation = f"{time.time_ns():x}{secrets.token_hex(4)}"
    socket_path = backend_socket_path(generation)
    start_transient_user_unit(
        unit=_backend_unit(socket_path),
        command=(*own_command(), "__web_backend", str(socket_path)),
        environment=_environment(_PASSED_ENV),
        properties=(
            "KillMode=mixed",
            f"StandardOutput=append:{socket_path.with_suffix('.log')}",
            f"StandardError=append:{socket_path.with_suffix('.log')}",
        ),
    )
    return socket_path


def _ready_backend(build_id: str) -> Path | None:
    return next(
        (path for path in backend_dir().glob("*.sock") if _compatible(backend_health(path), build_id)),
        None,
    )


def _wait_for_backend(socket_path: Path, build_id: str) -> None:
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if _compatible(backend_health(socket_path), build_id):
            return
        time.sleep(0.1)
    raise RuntimeError(
        f"Workspace backend {_backend_unit(socket_path)} did not pass its readiness contract within {START_TIMEOUT_S:.0f}s; "
        f"see {socket_path.with_suffix('.log')}"
    )


def _stop_backend(socket_path: Path) -> None:
    stop_transient_user_unit(_backend_unit(socket_path))
    socket_path.unlink(missing_ok=True)
    socket_path.with_suffix(".log").unlink(missing_ok=True)


def _backend_stop_error(socket_path: Path) -> str | None:
    try:
        _stop_backend(socket_path)
    except (OSError, RuntimeError, subprocess.CalledProcessError) as error:
        if isinstance(error, subprocess.CalledProcessError) and error.stderr:
            return error.stderr.strip()
        return str(error)
    return None


def _retire_backends(backends: list[Path]) -> None:
    for backend in backends:
        if detail := _backend_stop_error(backend):
            logger.error("Workspace generation retirement failed for %s: %s; a later deployment will retry", backend, detail)


def _retire_unconfigured_backends(configured: list[Path] | None = None) -> None:
    try:
        retained = set(configured if configured is not None else configured_backends())
        directory = backend_dir()
        found = set(directory.glob("*.sock"))
        found.update(path.with_suffix(".sock") for path in directory.glob("*.log"))
    except (OSError, RuntimeError, httpx.HTTPError) as error:
        logger.exception("Workspace generation retirement scan failed: %s; a later deployment will retry", error)
        return
    _retire_backends(sorted(path for path in found if path not in retained))


def _cleanup_candidate(socket_path: Path, failure: BaseException) -> None:
    if detail := _backend_stop_error(socket_path):
        failure.add_note(f"candidate cleanup failed for {socket_path}: {detail}")
        logger.error("Workspace candidate cleanup failed for %s: %s; retaining its socket", socket_path, detail)


def _wait_for_public(build_id: str) -> None:
    deadline = time.monotonic() + START_TIMEOUT_S
    while time.monotonic() < deadline:
        if _public_is_compatible(build_id):
            return
        time.sleep(0.1)
    raise RuntimeError(
        f"the Workspace gateway did not serve build {build_id} on 127.0.0.1:{fixed_port()} within {START_TIMEOUT_S:.0f}s"
    )


def ensure_workspace() -> str:
    """Return the stable Workspace origin with the caller's build primary."""
    workspace_name()
    verify_frontend_assets()
    build_id = web_build_id()
    with DeploymentLock():
        if gateway_ready() and _public_is_compatible(build_id):
            _retire_unconfigured_backends()
            return origin()
        if not gateway_ready():
            _require_gateway_port_available()
        ensure_gateway(_environment(_PASSED_ENV))
        if _public_is_compatible(build_id):
            _retire_unconfigured_backends()
            return origin()
        previous_backends = configured_backends()
        candidate = _ready_backend(build_id) or _start_backend()
        fallback = [backend for backend in previous_backends if backend != candidate and _protocol_compatible(backend_health(backend))][:1]
        promoted = False
        try:
            _wait_for_backend(candidate, build_id)
            load_gateway_route([candidate, *fallback])
            promoted = True
            _wait_for_public(build_id)
        except (RuntimeError, httpx.HTTPError, subprocess.CalledProcessError) as failure:
            restored = not promoted
            if promoted:
                try:
                    load_gateway_route(previous_backends)
                    restored = True
                except (RuntimeError, httpx.HTTPError) as rollback_error:
                    failure.add_note(f"Caddy rollback failed: {rollback_error}")
                    logger.exception("Workspace Caddy rollback failed: %s", rollback_error)
            if restored:
                _cleanup_candidate(candidate, failure)
            raise
        _retire_unconfigured_backends([candidate, *fallback])
        return origin()

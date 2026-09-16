"""Live-member discovery for the Web Workspace: one private Unix socket per run.

Every run process exposes its own member API on a Unix domain
socket under the user's runtime directory. The connectable sockets ARE the
live registry: there is no registry file, PID file, heartbeat, or leader. A
socket that does not answer is stale and ignored; a missing socket is not a
live run. Caddy is the only owner of each named Workspace's fixed TCP port; a
run never binds it.

This module owns the paths, the ids, the member socket, the probe, and the
two protocol versions. Caddy owns the fixed browser listener. It never reads
another run's state: it only asks a member over HTTP on its socket.
"""

from __future__ import annotations

import logging
import os
import re
import socket
from pathlib import Path

import httpx

from output.web.models import MemberSummary

# The browser-facing contract: bumped whenever the service HTTP shape changes
# in a way an older frontend bundle could not follow.
WEB_PROTOCOL_VERSION = 7
# The member (Unix socket) contract between the service and a run or a
# projection worker: bumped whenever the member routes change shape.
MEMBER_PROTOCOL_VERSION = 6

# One named Workspace owns one fixed port. These are host deployment settings,
# never run settings.
DEFAULT_WORKSPACE_NAME = "default"
DEFAULT_PORT = 47321
NAME_ENV = "AIBUILDAI_WEB_NAME"
PORT_ENV = "AIBUILDAI_WEB_PORT"

RUN_ID_RE = re.compile(r"^[0-9a-f]{32}$")

# Bounded per-member probe: a member that cannot answer inside this window
# is not shown on this request. A live member answers on the run's own
# event loop, which a busy run can hold for more than a second, so the
# window is wide enough not to blink a healthy run out of the list.
PROBE_TIMEOUT_S = 2.0

logger = logging.getLogger(__name__)


def workspace_name() -> str:
    name = os.environ.get(NAME_ENV, DEFAULT_WORKSPACE_NAME)
    if re.fullmatch(r"[a-z0-9][a-z0-9-]{0,31}", name) is None:
        raise ValueError(f"{NAME_ENV} must contain lowercase letters, digits, and hyphens")
    if name != DEFAULT_WORKSPACE_NAME and PORT_ENV not in os.environ:
        raise ValueError(f"{NAME_ENV}={name!r} requires an explicit {PORT_ENV}")
    return name


def fixed_port() -> int:
    raw = os.environ.get(PORT_ENV)
    if raw is None:
        return DEFAULT_PORT
    if not raw.isdigit() or not 1 <= int(raw) <= 65535:
        raise ValueError(f"{PORT_ENV}={raw!r} is not a TCP port number")
    return int(raw)


def runtime_dir() -> Path:
    """``$XDG_RUNTIME_DIR/aibuildai/w``, user-private, created on demand. Short on purpose: a Unix socket path must fit ``sockaddr_un`` (108 bytes)."""
    base = os.environ.get("XDG_RUNTIME_DIR")
    if not base:
        raise RuntimeError(
            "the Web Workspace needs XDG_RUNTIME_DIR for its socket directory; "
            "the user systemd manager sets it for every session"
        )
    path = Path(base) / "aibuildai" / "w"
    path.mkdir(mode=0o700, parents=True, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def require_run_id(run_id: str) -> str:
    if RUN_ID_RE.match(run_id) is None:
        raise ValueError(f"not a run id: {run_id!r}")
    return run_id


def member_socket_path(run_id: str) -> Path:
    """The live member socket of one run. A projection worker's socket also has its Workspace port and backend generation, so the two cannot be confused."""
    return runtime_dir() / f"{require_run_id(run_id)}.sock"


def projection_socket_path(run_id: str, generation: str) -> Path:
    if re.fullmatch(r"[0-9a-f]+", generation) is None:
        raise ValueError(f"not a Workspace backend generation: {generation!r}")
    return runtime_dir() / f"{require_run_id(run_id)}.{fixed_port():x}.{generation}.p"


def socket_client(path: Path, *, timeout: "float | httpx.Timeout") -> httpx.AsyncClient:
    """An HTTP client bound to one member socket. The host name is a label: the socket path is the address."""
    transport = httpx.AsyncHTTPTransport(uds=str(path))
    return httpx.AsyncClient(
        transport=transport, base_url="http://member", timeout=timeout
    )


async def probe_summary(path: Path) -> MemberSummary | None:
    """The member's compact summary, or None when it cannot show one right now (it may not have rendered yet)."""
    try:
        async with socket_client(path, timeout=PROBE_TIMEOUT_S) as client:
            response = await client.get("/api/v1/member")
        if response.status_code != 200:
            return None
        return MemberSummary.model_validate_json(response.content)
    except Exception as exc:  # noqa: BLE001 — per-member discovery boundary, as above.
        logger.info("web member %s has no summary this probe: %s: %s", path.stem, type(exc).__name__, exc)
        return None
def bind_member_socket(path: Path) -> socket.socket:
    """A member listener. A leftover socket file from an earlier life of the same run is removed only when nothing answers on it."""
    if path.exists():
        if answers(path):
            raise RuntimeError(f"run {path.stem} already has a web member at {path}")
        path.unlink()
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(path))
    os.chmod(path, 0o600)
    sock.listen(128)
    sock.setblocking(False)
    return sock


def answers(path: Path) -> bool:
    """Whether something accepts a connection on the socket path right now."""
    probe = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    probe.settimeout(PROBE_TIMEOUT_S)
    try:
        probe.connect(str(path))
    except OSError:
        return False
    finally:
        probe.close()
    return True

"""One private Workspace backend generation."""

from __future__ import annotations

import logging
import socket
import sys
from pathlib import Path

import uvicorn

from output.web.app import verify_frontend_assets, web_build_id
from output.web.projection_workers import ProjectionWorkers
from output.web.run_catalog import RunCatalog
from output.web.service_app import create_service_app


def bind_backend(socket_path: Path) -> socket.socket:
    """Bind this generation's unique private Unix socket."""
    if socket_path.exists():
        raise RuntimeError(f"Workspace backend socket already exists: {socket_path}")
    sock = socket.socket(socket.AF_UNIX, socket.SOCK_STREAM)
    sock.bind(str(socket_path))
    socket_path.chmod(0o600)
    sock.listen(128)
    sock.setblocking(False)
    return sock


def main(socket_path: str) -> None:
    logging.basicConfig(level=logging.INFO, format="%(asctime)s %(levelname)s %(name)s %(message)s")
    # Every forwarded member request would otherwise be one journal line.
    logging.getLogger("httpx").setLevel(logging.WARNING)
    verify_frontend_assets()
    from infra.postgresql import ensure_postgresql

    ensure_postgresql()
    path = Path(socket_path)
    sock = bind_backend(path)
    workers = ProjectionWorkers(path.stem)
    app = create_service_app(catalog=RunCatalog(), workers=workers, build_id=web_build_id())
    logging.getLogger(__name__).info(
        "AIBuildAI Workspace backend serving at %s",
        path,
    )
    try:
        uvicorn.Server(uvicorn.Config(app, log_config=None, access_log=False)).run(sockets=[sock])
    finally:
        workers.shutdown()
        sock.close()
        path.unlink(missing_ok=True)


if __name__ == "__main__":
    sys.exit(main(sys.argv[1]))

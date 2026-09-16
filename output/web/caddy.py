"""Pinned Caddy binary and its Workspace gateway configuration."""

from __future__ import annotations

import json
import os
import time
from pathlib import Path

import httpx

from infra.owned_tools import owned
from infra.process.transient_unit import start_transient_user_unit
from output.web.live_registry import fixed_port, runtime_dir, workspace_name

WORKSPACE_PROXY_ID = "aibuildai_workspace_proxy"
_ADMIN_TIMEOUT_S = 1.0
_START_TIMEOUT_S = 15.0


def _workspace_suffix() -> str:
    return f"-{workspace_name()}-{fixed_port()}"


def caddy_config_home() -> Path:
    base = Path(os.environ.get("XDG_CONFIG_HOME", Path.home() / ".config"))
    return base / "aibuildai" / f"workspace{_workspace_suffix()}"


def admin_socket_path() -> Path:
    return runtime_dir() / f"caddy-admin{_workspace_suffix()}.sock"


def backend_dir() -> Path:
    path = runtime_dir() / f"b{_workspace_suffix()}"
    path.mkdir(mode=0o700, exist_ok=True)
    os.chmod(path, 0o700)
    return path


def caddy_runtime_path(name: str) -> Path:
    return runtime_dir() / f"caddy-{name}{_workspace_suffix()}.json"


def backend_socket_path(generation: str) -> Path:
    return backend_dir() / f"{generation}.sock"


def gateway_config(backends: list[Path]) -> dict[str, object]:
    admin = f"unix/{admin_socket_path()}"
    route: dict[str, object]
    if backends:
        route = {
            "handle": [{
                "@id": WORKSPACE_PROXY_ID,
                "handler": "reverse_proxy",
                "upstreams": [{"dial": f"unix/{path}"} for path in backends],
                "headers": {"request": {"set": {"Host": ["127.0.0.1"]}}},
                "load_balancing": {
                    "selection_policy": {"policy": "first"},
                    "retries": 1,
                    "try_duration": "2s",
                    "try_interval": "100ms",
                },
                "health_checks": {
                    "active": {
                        "uri": "/api/v1/health",
                        "interval": "1s",
                        "timeout": "1s",
                        "expect_status": 200,
                        "headers": {"Host": ["127.0.0.1"]},
                    },
                },
                "transport": {"protocol": "http", "dial_timeout": "1s"},
            }],
        }
    else:
        route = {"handle": [{"handler": "static_response", "status_code": 503}]}
    return {
        "admin": {"listen": admin},
        "apps": {
            "http": {
                "servers": {
                    "workspace": {
                        "listen": [f"127.0.0.1:{fixed_port()}"],
                        "routes": [route],
                    },
                },
            },
        },
    }


def gateway_bootstrap_config() -> dict[str, object]:
    """Start Caddy's private Admin API before it owns the public listener."""
    return {"admin": {"listen": f"unix/{admin_socket_path()}"}}


def gateway_ready() -> bool:
    transport = httpx.HTTPTransport(uds=str(admin_socket_path()))
    try:
        with httpx.Client(transport=transport, base_url="http://caddy", timeout=_ADMIN_TIMEOUT_S) as client:
            response = client.get("/config/")
    except httpx.HTTPError:
        return False
    return response.status_code == 200


def gateway_unit() -> str:
    return f"aibuildai-web-gateway-{workspace_name()}-{fixed_port()}"


def ensure_gateway(environment: dict[str, str]) -> None:
    """Start Caddy and wait for its private Admin API."""
    bootstrap = caddy_runtime_path("bootstrap")
    if gateway_ready():
        bootstrap.unlink(missing_ok=True)
        return
    config_home = caddy_config_home() / "caddy"
    admin_socket_path().unlink(missing_ok=True)
    command = [str(owned("caddy")), "run"]
    if (config_home / "autosave.json").is_file():
        command.append("--resume")
    else:
        bootstrap.write_text(json.dumps(gateway_bootstrap_config()))
        bootstrap.chmod(0o600)
        command.extend(("--config", str(bootstrap)))
    environment = {**environment, "XDG_CONFIG_HOME": str(caddy_config_home())}
    start_transient_user_unit(
        unit=gateway_unit(),
        command=command,
        environment=environment,
        properties=("KillMode=mixed",),
    )
    deadline = time.monotonic() + _START_TIMEOUT_S
    while time.monotonic() < deadline:
        if gateway_ready():
            bootstrap.unlink(missing_ok=True)
            return
        time.sleep(0.1)
    raise RuntimeError(
        f"the Workspace Caddy gateway did not become ready within {_START_TIMEOUT_S:.0f}s; "
        f"see `journalctl --user -u {gateway_unit()}`"
    )


def configured_backends() -> list[Path]:
    """Return the backend sockets in Caddy's saved order."""
    transport = httpx.HTTPTransport(uds=str(admin_socket_path()))
    with httpx.Client(transport=transport, base_url="http://caddy", timeout=_ADMIN_TIMEOUT_S) as client:
        response = client.get(f"/id/{WORKSPACE_PROXY_ID}/upstreams")
    if response.status_code == 404:
        return []
    if response.status_code != 200:
        raise RuntimeError(f"Caddy could not read Workspace upstreams: HTTP {response.status_code}")
    return [Path(upstream["dial"].removeprefix("unix/")) for upstream in response.json()]


def load_gateway_route(backends: list[Path]) -> None:
    """Atomically replace the Workspace route through Caddy's Admin API."""
    transport = httpx.HTTPTransport(uds=str(admin_socket_path()))
    with httpx.Client(transport=transport, base_url="http://caddy", timeout=_ADMIN_TIMEOUT_S) as client:
        response = client.post(
            "/load",
            content=json.dumps(gateway_config(backends)),
            headers={"Content-Type": "application/json"},
        )
    if response.status_code != 200:
        raise RuntimeError(f"Caddy rejected the Workspace route update: HTTP {response.status_code}: {response.text}")

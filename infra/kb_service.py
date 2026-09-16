"""Client contract for the remote kb service."""

from __future__ import annotations

import os
from urllib.parse import urlsplit, urlunsplit

AIBUILDAI_KB_BASE_URL = "AIBUILDAI_KB_BASE_URL"
AIBUILDAI_KB_TOKEN = "AIBUILDAI_KB_TOKEN"
_DERIVED_PATHS = frozenset({"/mcp", "/healthz"})


class KbServiceConfigError(ValueError):
    """The configured kb service endpoint cannot satisfy the client contract."""


def kb_base_url() -> str:
    """Return the configured kb service base URL: ``AIBUILDAI_KB_BASE_URL``, or the public AIBuildAI Kb."""
    return _normalize_base_url(os.environ.get(AIBUILDAI_KB_BASE_URL, "https://32.194.230.84/open"))


def kb_mcp_url() -> str:
    """Return the kb Streamable-HTTP MCP endpoint URL."""
    return _append_path(kb_base_url(), "/mcp")


def _normalize_base_url(raw: str) -> str:
    try:
        parts = urlsplit(raw)
    except ValueError as exc:
        raise KbServiceConfigError(
            f"{AIBUILDAI_KB_BASE_URL} must be a valid service base URL, got {raw!r}"
        ) from exc
    if not parts.scheme or not parts.netloc:
        raise KbServiceConfigError(
            f"{AIBUILDAI_KB_BASE_URL} must be an absolute service base URL, got {raw!r}"
        )
    if parts.query or parts.fragment:
        raise KbServiceConfigError(
            f"{AIBUILDAI_KB_BASE_URL} must not include query or fragment, got {raw!r}"
        )
    path = parts.path.rstrip("/")
    if any(path == derived or path.endswith(derived) for derived in _DERIVED_PATHS):
        raise KbServiceConfigError(
            f"{AIBUILDAI_KB_BASE_URL} must be the service base URL, not an endpoint: "
            f"{raw!r}"
        )
    return urlunsplit((parts.scheme, parts.netloc, path, "", ""))


def _append_path(base_url: str, path: str) -> str:
    parts = urlsplit(base_url)
    base_path = parts.path.rstrip("/")
    return urlunsplit((parts.scheme, parts.netloc, f"{base_path}{path}", "", ""))


def kb_bearer_header() -> dict[str, str]:
    """``Authorization`` header for the kb service from ``AIBUILDAI_KB_TOKEN``, or {} when unset."""
    token = os.environ.get(AIBUILDAI_KB_TOKEN, "").strip()
    return {"Authorization": f"Bearer {token}"} if token else {}

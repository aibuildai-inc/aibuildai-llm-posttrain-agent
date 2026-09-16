"""Frontend assets, the Web build identity, and the build-time schema export.

``python -m output.web.app`` prints the browser-facing OpenAPI schema of the
Workspace backend (the frontend's TypeScript types are generated from it),
``--hash`` prints its stamp, and ``--check`` is the machine-gate arm that
fails when the response models moved but the committed generated client was
not regenerated.
"""

from __future__ import annotations

import hashlib
import json
import sys
from pathlib import Path



def frontend_dist_dir() -> Path:
    """The built frontend, next to this module."""
    return Path(__file__).resolve().parent / "frontend" / "dist"


def verify_frontend_assets() -> Path:
    """Fail fast when the built frontend is absent."""
    dist = frontend_dist_dir()
    index = dist / "index.html"
    if not index.is_file():
        raise FileNotFoundError(
            f"the built Web frontend is missing at {dist}; "
            "build it with `npm ci && npm run build` in output/web/frontend"
        )
    return dist


def web_build_id() -> str:
    """What one Workspace backend serves: the API schema stamp plus the shipped frontend's entry page (its asset names change with every build). Caddy promotes another build only after it is ready and retains the prior build as fallback."""
    digest = hashlib.sha256()
    digest.update(_schema_stamp_path().read_text().strip().encode())
    digest.update((frontend_dist_dir() / "index.html").read_bytes())
    return digest.hexdigest()[:16]


def _schema_json() -> str:
    """The browser-facing OpenAPI schema as JSON; the frontend's TypeScript types are generated from it (``npm run gen-api``), so no hand-written interface can drift."""
    from output.web.service_app import schema_app

    return json.dumps(schema_app().openapi(), indent=2, sort_keys=True)


def _schema_hash() -> str:
    return hashlib.sha256(_schema_json().encode()).hexdigest()


def _schema_stamp_path() -> Path:
    return Path(__file__).resolve().parent / "frontend" / "src" / "api" / "schema.hash"


def _check_schema_stamp() -> None:
    """The machine-gate arm: fail when the backend schema moved but the committed generated frontend types were not regenerated. ``npm run gen-api`` (part of ``npm run build``) rewrites the stamp beside ``schema.d.ts``, so a stale stamp means a stale generated client."""
    stamp = _schema_stamp_path()
    committed = stamp.read_text().strip() if stamp.is_file() else "<missing>"
    current = _schema_hash()
    if committed != current:
        raise SystemExit(
            "web API schema stamp is stale: the Pydantic response models "
            "changed but the generated frontend types were not regenerated. "
            "Run `npm run build` in output/web/frontend and commit "
            "src/api/schema.d.ts, src/api/schema.hash, and dist/."
        )
    print("web api schema stamp matches")


if __name__ == "__main__":
    argument = sys.argv[1] if len(sys.argv) > 1 else ""
    if argument == "--hash":
        print(_schema_hash())
    elif argument == "--check":
        _check_schema_stamp()
    else:
        print(_schema_json())

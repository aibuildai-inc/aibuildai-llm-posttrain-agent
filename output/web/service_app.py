"""The Workspace backend app: frontend, health, run catalog, and the typed run routes.

Served through the fixed loopback port by Caddy. Every run route resolves its
run id to a member socket and forwards exactly one typed member route through
``output.web.proxy``; the backend holds no run state of its own. The only
writes are the typed pause and resume actions
(``output.web.run_actions``), guarded by the same-origin CSRF token. No WebSocket, no CORS; the OpenAPI pages
are disabled at runtime (the browser-facing schema is exported at build
time by ``python -m output.web.app``).
"""

from __future__ import annotations

import asyncio
import secrets
from pathlib import Path
from typing import Awaitable, Callable
from urllib.parse import urlsplit

from fastapi import Depends, FastAPI, HTTPException, Path as PathParam, Query, Request, Response
from starlette.middleware.trustedhost import TrustedHostMiddleware

from output.web.app import verify_frontend_assets
from output.web.live_registry import (
    MEMBER_PROTOCOL_VERSION,
    RUN_ID_RE,
    WEB_PROTOCOL_VERSION,
    answers,
    member_socket_path,
    workspace_name,
)
from output.web.models import (
    CsrfResponse,
    ExecutionDetailResponse,
    RunControl,
    RunCapabilitiesResponse,
    RunResponse,
    RunsResponse,
    MetricSeriesResponse,
    TranscriptResponse,
    WorkspaceEntriesResponse,
    WorkspaceHealth,
    WorkspaceStatResponse,
    WorkspaceTextResponse,
)
from output.web.projection_workers import ProjectionWorkers
from output.web.proxy import forward, stream
from output.web.run_actions import RunActions
from output.web.run_catalog import RunCatalog
from output.web.run_resolver import Resolution, RunEndpointResolver
from output.web.theme import theme_css
from startup import run_launch
from startup.run_catalog import (
    IndexedRun,
    run_active,
)
from aibuildai_version import APP_VERSION

# One CSP for the whole page: the built bundle is self-contained, so nothing
# may load from anywhere but this origin, and no route ever embeds this page.
# font-src data: allows KaTeX math fonts (inline woff2 in the CSS bundle).
_CSP = (
    "default-src 'self'; img-src 'self' data:; font-src 'self' data:; "
    "style-src 'self' 'unsafe-inline'; frame-ancestors 'none'"
)
RESTORING_DETAIL = "the run is being restored from its journal; retry"


def create_service_app(
    *,
    catalog: RunCatalog,
    workers: ProjectionWorkers,
    build_id: str,
    serve_frontend: bool = True,
) -> FastAPI:
    resolver = RunEndpointResolver(catalog, workers)
    actions = RunActions()
    # One token per backend start. The page reads it same-origin and sends
    # it back in a custom header; a cross-site page can neither read the
    # token nor set the header without a CORS preflight this app refuses.
    csrf_token = secrets.token_urlsafe(32)
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    app.add_middleware(TrustedHostMiddleware, allowed_hosts=["127.0.0.1", "localhost"])

    def mutation_guard(request: Request) -> None:
        """Refuse any mutation a cross-site page could send. Loopback-only is not enough against browser CSRF: require the same-origin token, and reject a foreign Origin or Fetch-Metadata site. The hostname alone is compared — SSH port forwarding legitimately changes the port the browser sees."""
        origin = request.headers.get("origin")
        if origin is not None and urlsplit(origin).hostname not in ("127.0.0.1", "localhost"):
            raise HTTPException(status_code=403, detail="cross-origin mutation refused")
        site = request.headers.get("sec-fetch-site")
        if site is not None and site not in ("same-origin", "none"):
            raise HTTPException(status_code=403, detail="cross-site mutation refused")
        sent = request.headers.get("x-aibuildai-csrf", "")
        if not secrets.compare_digest(sent, csrf_token):
            raise HTTPException(status_code=403, detail="missing or stale CSRF token; read /api/v1/csrf first")

    @app.middleware("http")
    async def security_headers(
        request: Request, call_next: Callable[[Request], Awaitable[Response]]
    ) -> Response:
        response = await call_next(request)
        # A served workspace document carries its own stricter policy.
        response.headers.setdefault("Content-Security-Policy", _CSP)
        response.headers["X-Content-Type-Options"] = "nosniff"
        response.headers["Referrer-Policy"] = "no-referrer"
        # The SPA entry page names the content-hashed asset URLs, so a
        # heuristically cached entry keeps serving the previous build to a
        # returning browser after a backend upgrade: force an ETag
        # revalidation on every HTML navigation (a 304 when unchanged).
        # Hash-named assets never change under the same URL.
        if request.url.path.startswith("/assets/") and response.status_code == 200:
            response.headers.setdefault("Cache-Control", "public, max-age=31536000, immutable")
        elif response.headers.get("content-type", "").startswith("text/html"):
            response.headers["Cache-Control"] = "no-cache"
        return response

    run_id_param = PathParam(pattern=RUN_ID_RE.pattern)

    @app.get("/api/v1/health", response_model=WorkspaceHealth)
    async def health() -> WorkspaceHealth:
        return WorkspaceHealth(
            workspace_name=workspace_name(),
            ready=True,
            app_version=APP_VERSION,
            web_protocol_version=WEB_PROTOCOL_VERSION,
            web_build_id=build_id,
            member_protocol_version=MEMBER_PROTOCOL_VERSION,
        )

    @app.get("/api/v1/runs", response_model=RunsResponse)
    async def runs() -> RunsResponse:
        return RunsResponse(runs=await catalog.summaries(resolver))

    @app.get("/api/v1/csrf", response_model=CsrfResponse)
    async def csrf(response: Response) -> CsrfResponse:
        # Never cached: the token changes with every service start, and a
        # page holding a cached one would 403 on every mutation after a
        # restart. The frontend reads it fresh before each mutation.
        response.headers["Cache-Control"] = "no-store"
        return CsrfResponse(token=csrf_token)

    async def indexed(run_id: str) -> IndexedRun:
        if (run := await asyncio.to_thread(catalog.by_id, run_id)) is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
        return run

    @app.get("/api/v1/runs/{run_id}/capabilities", response_model=RunCapabilitiesResponse)
    async def run_capabilities(run_id: str = run_id_param) -> RunCapabilitiesResponse:
        """The server-decided action surface: the frontend renders these and invents no lifecycle policy of its own."""
        run = await indexed(run_id)
        pending_kind = actions.pending_kind(run_id)
        facts = await catalog.facts(run)
        active = await asyncio.to_thread(run_active, run)
        if pending_kind is not None:
            control = RunControl(
                kind=pending_kind,
                enabled=False,
                reason=f"a {pending_kind} is already in progress",
                pending=True,
            )
        elif run.identity.kubernetes_start_mode or facts.result is not None:
            control = None
        elif active and await asyncio.to_thread(answers, member_socket_path(run.run_id)):
            control = RunControl(
                kind="pause", enabled=True, reason=None, pending=False
            )
        elif active or not facts.readable:
            control = None
        else:
            refusal = await asyncio.to_thread(run_launch.resume_refusal, run)
            control = RunControl(
                kind="resume",
                enabled=refusal is None,
                reason=refusal,
                pending=False,
            )
        return RunCapabilitiesResponse(
            control=control,
            failure=actions.failure(run_id),
        )

    @app.post(
        "/api/v1/runs/{run_id}/actions/pause",
        status_code=202,
        dependencies=[Depends(mutation_guard)],
    )
    async def pause_run(run_id: str = run_id_param) -> Response:
        actions.pause(await indexed(run_id))
        return Response(status_code=202)

    @app.post(
        "/api/v1/runs/{run_id}/actions/resume",
        status_code=202,
        dependencies=[Depends(mutation_guard)],
    )
    async def resume_run(run_id: str = run_id_param) -> Response:
        actions.resume(await indexed(run_id))
        return Response(status_code=202)

    async def endpoint(run_id: str) -> Path:
        """The member socket for one run route, or the typed refusal: 404 for an unknown run, 503 while it restores or when no provider can show it."""
        resolution: Resolution | None = await resolver.resolve(run_id)
        if resolution is None:
            raise HTTPException(status_code=404, detail=f"unknown run {run_id}")
        if resolution.endpoint is None:
            raise HTTPException(
                status_code=503,
                detail=resolution.diagnostic if resolution.availability == "unavailable" else RESTORING_DETAIL,
            )
        return resolution.endpoint

    @app.get("/api/v1/runs/{run_id}/run", response_model=RunResponse)
    async def run(run_id: str = run_id_param, at_s: float | None = Query(None, ge=0)) -> RunResponse:
        query = {} if at_s is None else {"at_s": str(at_s)}
        return await forward(await endpoint(run_id), "/api/v1/run", query, RunResponse)

    @app.get("/api/v1/runs/{run_id}/execution", response_model=ExecutionDetailResponse)
    async def execution(run_id: str = run_id_param, path: str = Query(min_length=1), revision: int | None = Query(None, ge=1)) -> ExecutionDetailResponse:
        query = {"path": path}
        if revision is not None:
            query["revision"] = str(revision)
        return await forward(await endpoint(run_id), "/api/v1/execution", query, ExecutionDetailResponse)

    @app.get("/api/v1/runs/{run_id}/metrics", response_model=MetricSeriesResponse)
    async def metrics(
        run_id: str = run_id_param, path: str = Query(min_length=1)
    ) -> MetricSeriesResponse:
        return await forward(await endpoint(run_id), "/api/v1/metrics", {"path": path}, MetricSeriesResponse)

    @app.get("/api/v1/runs/{run_id}/transcript", response_model=TranscriptResponse)
    async def transcript(run_id: str = run_id_param, path: str = Query(min_length=1), revision: int | None = Query(None, ge=1)) -> TranscriptResponse:
        query = {"path": path}
        if revision is not None:
            query["revision"] = str(revision)
        return await forward(await endpoint(run_id), "/api/v1/transcript", query, TranscriptResponse)

    @app.get("/api/v1/runs/{run_id}/workspace/entries", response_model=WorkspaceEntriesResponse)
    async def workspace_entries(run_id: str = run_id_param, path: str = Query("")) -> WorkspaceEntriesResponse:
        return await forward(await endpoint(run_id), "/api/v1/workspace/entries", {"path": path}, WorkspaceEntriesResponse)

    @app.get("/api/v1/runs/{run_id}/workspace/stat", response_model=WorkspaceStatResponse)
    async def workspace_stat(run_id: str = run_id_param, path: str = Query(min_length=1)) -> WorkspaceStatResponse:
        return await forward(await endpoint(run_id), "/api/v1/workspace/stat", {"path": path}, WorkspaceStatResponse)

    @app.get("/api/v1/runs/{run_id}/workspace/text", response_model=WorkspaceTextResponse)
    async def workspace_text(run_id: str = run_id_param, path: str = Query(min_length=1)) -> WorkspaceTextResponse:
        return await forward(await endpoint(run_id), "/api/v1/workspace/text", {"path": path}, WorkspaceTextResponse)

    # The byte routes: streamed, never read into memory or validated as JSON;
    # HEAD answers the same headers without a body.
    @app.get("/api/v1/runs/{run_id}/workspace/raw", response_class=Response)
    @app.head("/api/v1/runs/{run_id}/workspace/raw", include_in_schema=False)
    async def workspace_raw(request: Request, run_id: str = run_id_param, path: str = Query(min_length=1)) -> Response:
        return await stream(await endpoint(run_id), "/api/v1/workspace/raw", request, path)

    @app.get("/api/v1/runs/{run_id}/workspace/download", response_class=Response)
    @app.head("/api/v1/runs/{run_id}/workspace/download", include_in_schema=False)
    async def workspace_download(request: Request, run_id: str = run_id_param, path: str = Query(min_length=1)) -> Response:
        return await stream(await endpoint(run_id), "/api/v1/workspace/download", request, path)

    @app.get("/api/v1/theme.css")
    async def theme() -> Response:
        return Response(theme_css(), media_type="text/css")

    @app.get("/api/v1/system/resources")
    async def system_resources() -> dict:
        from output.web.system_resources import collect_resources
        return await asyncio.to_thread(collect_resources)

    @app.get("/api/v1/runs/{run_id}/resources/history")
    async def resource_history(run_id: str = run_id_param) -> list[dict]:
        from engine.paths import resource_history_path_for
        from output.web.system_resources import read_history_samples
        run = await indexed(run_id)
        path = resource_history_path_for(run.run_home)
        return await asyncio.to_thread(read_history_samples, path)

    if serve_frontend:
        # FastAPI's own SPA primitive: API routes match first, real files are
        # served as low-priority routes, browser navigations fall back to
        # index.html, and a missing asset stays a 404 instead of being masked
        # as a 200 HTML page.
        app.frontend("/", directory=verify_frontend_assets(), check_dir=True)

    return app


def schema_app() -> FastAPI:
    """The build-time schema export: the same routes with no frontend and no projection workers, built before any frontend build exists."""
    return create_service_app(
        catalog=RunCatalog(), workers=ProjectionWorkers("0"), build_id="schema", serve_frontend=False
    )

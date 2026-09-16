"""The single-run member app, served over one private Unix socket.

One projection worker builds it from the Run journal. Routes read one typed
head or historical frame. No WebSocket, no CORS, and no mutation route exists.
"""

from __future__ import annotations

import asyncio
import time
from email.utils import formatdate, parsedate_to_datetime
from pathlib import Path
from typing import TYPE_CHECKING, Awaitable, Callable, Literal, TypeVar
from urllib.parse import quote

from fastapi import FastAPI, HTTPException, Query, Request, Response
from fastapi.responses import StreamingResponse
from starlette.background import BackgroundTask

from output.web.metric_series import MetricSeriesTail
from output.web.live_registry import MEMBER_PROTOCOL_VERSION
from output.web.models import (
    ExecutionDetailResponse,
    MemberIdentity,
    MemberSummary,
    RunResponse,
    MetricSeriesResponse,
    TranscriptResponse,
    WorkspaceEntriesResponse,
    WorkspaceStatResponse,
    WorkspaceTextResponse,
)
from output.web.workspace import (
    INLINE_TYPES,
    OpenFile,
    WorkspaceError,
    WorkspaceService,
    etag_of,
)
from output.web.view import (
    WebViewStore,
    build_execution_detail,
    build_run_response,
    build_metric_series,
    build_transcript,
    metric_series_source,
)

if TYPE_CHECKING:
    from output.web.view import WebFrame

# A served workspace document: it may load nothing from external sources.
DOCUMENT_CSP = "default-src 'none'; frame-ancestors 'self'"

WorkT = TypeVar("WorkT")


def _require_frame(
    store: WebViewStore,
    *,
    revision: int | None = None,
    at_s: float | None = None,
) -> "WebFrame":
    try:
        return store.frame(revision=revision, at_s=at_s)
    except LookupError as exc:
        raise HTTPException(status_code=404, detail=str(exc)) from exc
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


def create_member_app(
    store: WebViewStore,
    *,
    run_id: str,
    on_request: Callable[[], None] = lambda: None,
) -> FastAPI:
    """Build the read-only app over one Run projection."""
    app = FastAPI(openapi_url=None, docs_url=None, redoc_url=None)
    metrics_lock = asyncio.Lock()

    @app.middleware("http")
    async def touched(request: Request, call_next: Callable[[Request], Awaitable[Response]]) -> Response:
        # The backend's own discovery probes are not use: only a browser's
        # request for this run keeps a projection worker alive.
        if request.url.path not in ("/api/v1/identity", "/api/v1/member"):
            on_request()
        return await call_next(request)

    def _run(frame: "WebFrame") -> RunResponse:
        head = store.head
        if head is None:
            raise HTTPException(status_code=503, detail="the Run has no head frame")
        return build_run_response(
            frame.state,
            head_revision=head.state.version,
            head_elapsed_s=head.now_relative,
            now_relative=frame.now_relative,
            now_absolute=frame.now_absolute,
            view_age_s=time.time() - head.now_absolute,
        )

    # Every state-reading handler is async ON PURPOSE: FastAPI runs a plain
    # `def` handler in a worker thread, and these handlers read the same
    # mutable RunState and Transcript objects the run mutates on the event
    # loop. Async handlers run on that one loop thread, so a read can never
    # overlap a mutation (the engine/hooks.py worker-thread race, avoided).
    # Answered from startup constants: a joining run reads this while this
    # run may not have rendered yet, and the answer must not wait on that.
    @app.get("/api/v1/identity", response_model=MemberIdentity)
    async def identity() -> MemberIdentity:
        return MemberIdentity(
            run_id=run_id,
            member_protocol_version=MEMBER_PROTOCOL_VERSION,
        )

    @app.get("/api/v1/member", response_model=MemberSummary)
    async def member() -> MemberSummary:
        run = _run(_require_frame(store))
        return MemberSummary(
            run_id=run.run_id,
            task_name=run.task_name,
            status=run.status,
            status_word=run.status_word,
            status_style_token=run.status_style_token,
            elapsed_s=run.elapsed_s,
            model=run.model,
            metric_name=run.metric_name,
            selected_metric=run.selected_metric,
            result_recorded=run.result_recorded,
            generated_at_unix=run.generated_at_unix,
            view_age_s=run.view_age_s,
        )

    @app.get("/api/v1/run", response_model=RunResponse)
    async def run(at_s: float | None = Query(None, ge=0)) -> RunResponse:
        return _run(_require_frame(store, at_s=at_s))

    @app.get("/api/v1/execution", response_model=ExecutionDetailResponse)
    async def execution(
        path: str = Query(min_length=1),
        revision: int | None = Query(None, ge=1),
    ) -> ExecutionDetailResponse:
        frame = _require_frame(store, revision=revision)
        detail = build_execution_detail(
            frame.state,
            path,
            now_relative=frame.now_relative,
        )
        if detail is None:
            raise HTTPException(
                status_code=404, detail=f"unknown execution path {path!r}"
            )
        return detail

    @app.get("/api/v1/metrics", response_model=MetricSeriesResponse)
    async def metrics(path: str = Query(min_length=1)) -> MetricSeriesResponse:
        source = metric_series_source(_require_frame(store).state, path)
        if source is None:
            raise HTTPException(
                status_code=404,
                detail=f"no WorkUnit execution at path {path!r}",
            )
        attempt_dir, status = source
        async with metrics_lock:
            tail = store.metric_tails.setdefault(path, MetricSeriesTail())
            work = asyncio.create_task(
                asyncio.to_thread(build_metric_series, path, attempt_dir, status, tail)
            )
            cancelled = False
            work_error: Exception | None = None
            while not work.done():
                try:
                    await asyncio.shield(work)
                except asyncio.CancelledError:
                    cancelled = True
                except Exception as exc:  # noqa: BLE001 — request boundary.
                    if not work.done():
                        raise AssertionError(
                            "metrics shield failed before its file read settled"
                        ) from exc
                    work_error = exc
            if work_error is None:
                try:
                    response = work.result()
                except Exception as exc:  # noqa: BLE001 — request boundary.
                    work_error = exc
            if cancelled:
                raise asyncio.CancelledError
            if work_error is not None:
                raise work_error
            return response

    @app.get("/api/v1/transcript", response_model=TranscriptResponse)
    async def transcript(
        path: str = Query(min_length=1),
        revision: int | None = Query(None, ge=1),
    ) -> TranscriptResponse:
        frame = _require_frame(store, revision=revision)
        response = build_transcript(
            frame.state, path, transcript_for=frame.transcript_for
        )
        if response is None:
            raise HTTPException(
                status_code=404,
                detail=f"no Agent transcript at execution path {path!r}",
            )
        return response

    def _workspace() -> WorkspaceService:
        # The root is copied off RunState on the event loop; the blocking
        # directory and file work then runs in a worker thread over that
        # immutable path and never touches RunState.
        return WorkspaceService(_require_frame(store).state.workspace_directory())

    async def _in_thread(work: Callable[[], WorkT]) -> WorkT:
        try:
            return await asyncio.to_thread(work)
        except WorkspaceError as exc:
            raise HTTPException(status_code=exc.status, detail=exc.detail) from exc

    @app.get("/api/v1/workspace/entries", response_model=WorkspaceEntriesResponse)
    async def workspace_entries(path: str = Query("")) -> WorkspaceEntriesResponse:
        service = _workspace()
        return await _in_thread(lambda: service.entries(path))

    @app.get("/api/v1/workspace/stat", response_model=WorkspaceStatResponse)
    async def workspace_stat(path: str = Query(min_length=1)) -> WorkspaceStatResponse:
        service = _workspace()
        return await _in_thread(lambda: service.stat(path))

    @app.get("/api/v1/workspace/text", response_model=WorkspaceTextResponse)
    async def workspace_text(path: str = Query(min_length=1)) -> WorkspaceTextResponse:
        service = _workspace()
        return await _in_thread(lambda: service.text(path))

    @app.get("/api/v1/workspace/raw", response_class=Response)
    @app.head("/api/v1/workspace/raw", include_in_schema=False)
    async def workspace_raw(request: Request, path: str = Query(min_length=1)) -> Response:
        """Inline bytes for the passive document types only (PDF, raster images); everything else is not served inline."""
        service = _workspace()
        opened = await _in_thread(lambda: service.open_file(path))
        media = INLINE_TYPES.get(Path(opened.entry.name).suffix.lower())
        if media is None:
            opened.close()
            raise HTTPException(status_code=415, detail=f"{opened.entry.name!r} is not an inline document type; download it")
        return _file_response(request, opened, media, "inline")

    @app.get("/api/v1/workspace/download", response_class=Response)
    @app.head("/api/v1/workspace/download", include_in_schema=False)
    async def workspace_download(request: Request, path: str = Query(min_length=1)) -> Response:
        service = _workspace()
        opened = await _in_thread(lambda: service.open_file(path))
        return _file_response(request, opened, "application/octet-stream", "attachment")

    return app

def _bounded_int(text: str) -> int | None:
    """The value of a range offset, or None when it is not a plain number a file offset could hold."""
    if not text.isdigit() or len(text) > 18:
        return None
    return int(text)


def _parse_range(header: str, size: int) -> tuple[int, int] | None:
    """One ``bytes=a-b`` / ``bytes=a-`` / ``bytes=-n`` range as inclusive offsets, or None when it is malformed or does not fit the file. Nothing fits an empty file."""
    if not header.startswith("bytes=") or "," in header:
        return None
    first, sep, last = header[len("bytes="):].partition("-")
    if sep == "" or size == 0:
        return None
    if first == "":
        length = _bounded_int(last)
        if length is None or length == 0:
            return None
        return max(size - length, 0), size - 1
    start = _bounded_int(first)
    end = _bounded_int(last) if last != "" else size - 1
    if start is None or end is None or start >= size or end < start:
        return None
    return start, min(end, size - 1)


def _modified_since_s(header: str) -> float | None:
    """The If-Modified-Since instant, or None for a date the header grammar does not allow — an invalid validator is ignored (RFC 9110), never an error."""
    try:
        return parsedate_to_datetime(header).timestamp()
    except (TypeError, ValueError):
        return None


def _file_response(request: Request, opened: OpenFile, media_type: str, disposition: Literal["inline", "attachment"]) -> Response:
    """Stream an already open regular file with validators and single-range support: 200, 206, 304, 416, and HEAD.

    This function owns ``opened`` until a response owns it: every path
    that does not hand the descriptor to a ``StreamingResponse`` closes
    it, including an exception while the request's headers are read.
    """
    try:
        return _built_file_response(request, opened, media_type, disposition)
    except BaseException:
        opened.close()
        raise


def _built_file_response(request: Request, opened: OpenFile, media_type: str, disposition: Literal["inline", "attachment"]) -> Response:
    st = opened.stat
    etag = etag_of(st)
    last_modified = formatdate(st.st_mtime, usegmt=True)
    filename = quote(opened.entry.name)
    headers = {
        "Accept-Ranges": "bytes",
        "ETag": etag,
        "Last-Modified": last_modified,
        "Content-Disposition": f"{disposition}; filename*=UTF-8''{filename}",
        "Content-Security-Policy": DOCUMENT_CSP,
    }
    if_none_match = request.headers.get("if-none-match")
    since = request.headers.get("if-modified-since")
    since_s = _modified_since_s(since) if since is not None else None
    unchanged = (
        etag in {tag.strip() for tag in if_none_match.split(",")}
        if if_none_match is not None
        else since_s is not None and int(st.st_mtime) <= since_s
    )
    if unchanged:
        opened.close()
        return Response(status_code=304, headers=headers)
    status = 200
    start, end = 0, st.st_size - 1
    range_header = request.headers.get("range")
    if_range = request.headers.get("if-range")
    if range_header is not None and (if_range is None or if_range in (etag, last_modified)):
        span = _parse_range(range_header, st.st_size)
        if span is None:
            opened.close()
            headers["Content-Range"] = f"bytes */{st.st_size}"
            return Response(status_code=416, headers=headers)
        start, end = span
        status = 206
        headers["Content-Range"] = f"bytes {start}-{end}/{st.st_size}"
    headers["Content-Length"] = str(max(end - start + 1, 0))
    if request.method == "HEAD" or st.st_size == 0:
        opened.close()
        return Response(status_code=status, headers=headers, media_type=media_type)
    # The descriptor is read in a worker thread one chunk at a time; a
    # disconnected browser ends the iteration and the task closes it.
    return StreamingResponse(
        opened.chunks(start, end),
        status_code=status,
        headers=headers,
        media_type=media_type,
        background=BackgroundTask(opened.close),
    )

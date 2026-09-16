"""Explicit forwarding from the private Workspace backend to one member socket.

Two shapes, never a generic reverse proxy: ``forward`` reads one typed
JSON route and re-validates it through the same Pydantic model the member
used; ``stream`` relays one byte route chunk by chunk with the range and
validator headers travelling both ways.
"""

from __future__ import annotations

from pathlib import Path
from typing import AsyncIterator, TypeVar

import httpx
from fastapi import HTTPException, Request, Response
from fastapi.responses import StreamingResponse
from pydantic import BaseModel
from starlette.background import BackgroundTask

from output.web.live_registry import socket_client

# Request headers the byte routes honour and the backend forwards verbatim.
_RANGE_REQUEST_HEADERS = ("range", "if-range", "if-none-match", "if-modified-since")
# Response headers the backend copies from the member's byte response.
_BYTE_RESPONSE_HEADERS = (
    "content-type",
    "content-length",
    "content-disposition",
    "accept-ranges",
    "content-range",
    "etag",
    "last-modified",
    "content-security-policy",
)

# A member answers a forwarded request within this window or the service
# reports it gone; detail routes may read files, so it is longer than the
# discovery probe.
MEMBER_TIMEOUT_S = 10.0

# The provider the request was routed to stopped answering between the
# resolution and the forward. The run itself is still indexed.
GONE_DETAIL = "the run's provider went away; retry"

ModelT = TypeVar("ModelT", bound=BaseModel)


def _detail(response: httpx.Response) -> str:
    """A member error's own words: the JSON ``detail`` of a typed refusal, or the plain text of an unexpected 500 (Starlette answers those with a text body)."""
    if response.headers.get("content-type", "").startswith("application/json"):
        return str(response.json().get("detail", response.text))
    return response.text


async def forward(
    endpoint: Path,
    route: str,
    params: dict[str, str],
    model: type[ModelT],
    *,
    method: str = "GET",
    json: "dict[str, float] | None" = None,
) -> ModelT:
    """One member route, sent over the endpoint socket and re-validated. A member that does not answer is a provider that just went away: 503, never a silent switch to another run; the next poll resolves the run again."""
    try:
        async with socket_client(endpoint, timeout=MEMBER_TIMEOUT_S) as client:
            response = await client.request(method, route, params=params, json=json)
    except (httpx.HTTPError, OSError) as exc:
        raise HTTPException(
            status_code=503, detail=GONE_DETAIL
        ) from exc
    if response.status_code != 200:
        raise HTTPException(status_code=response.status_code, detail=_detail(response))
    return model.model_validate_json(response.content)


async def stream(endpoint: Path, route: str, request: Request, path: str) -> Response:
    """One member byte route, relayed chunk by chunk: neither process holds the file, the range and validator headers travel both ways, and the upstream response closes on EVERY exit — normal end, browser disconnect, an upstream that dies mid-body, or an error while its answer is read."""
    client = socket_client(endpoint, timeout=httpx.Timeout(MEMBER_TIMEOUT_S, connect=2.0))
    forwarded = {k: v for k, v in request.headers.items() if k in _RANGE_REQUEST_HEADERS}
    try:
        upstream = await client.send(
            client.build_request(request.method, route, params={"path": path}, headers=forwarded),
            stream=True,
        )
    except (httpx.HTTPError, OSError) as exc:
        await client.aclose()
        raise HTTPException(status_code=503, detail=GONE_DETAIL) from exc
    except BaseException:
        await client.aclose()
        raise

    closed = False

    async def close() -> None:
        # Idempotent: the relay's finally, the error paths, and the
        # response's background task may each reach it once.
        nonlocal closed
        if closed:
            return
        closed = True
        await upstream.aclose()
        await client.aclose()

    async def relay() -> AsyncIterator[bytes]:
        # The relay owns the upstream while bytes flow: a member that
        # dies mid-body ends this generator through its finally instead
        # of leaking the response and client. The background task is a
        # second guard, not the owner.
        try:
            async for chunk in upstream.aiter_raw():
                yield chunk
        finally:
            await close()

    try:
        headers = {k: v for k, v in upstream.headers.items() if k in _BYTE_RESPONSE_HEADERS}
        if request.method == "HEAD" or upstream.status_code in (304, 416):
            await close()
            return Response(status_code=upstream.status_code, headers=headers)
        if upstream.status_code >= 400:
            try:
                await upstream.aread()
                detail = _detail(upstream)
            except (httpx.HTTPError, OSError) as exc:
                # The member answered an error status but could not even
                # deliver its own error body: treat it as gone.
                raise HTTPException(
                    status_code=503, detail=GONE_DETAIL
                ) from exc
            raise HTTPException(status_code=upstream.status_code, detail=detail)
    except BaseException:
        await close()
        raise
    return StreamingResponse(
        relay(), status_code=upstream.status_code, headers=headers, background=BackgroundTask(close)
    )

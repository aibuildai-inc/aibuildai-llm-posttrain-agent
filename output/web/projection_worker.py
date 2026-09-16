"""One Run journal, continuously projected and served over a private socket.

``aibuildai __web_projection <run_home> <socket>`` is started by the
Workspace backend's projection workers, one process per loaded Run. The same
process remains the provider from active work through Pause, Resume, and final
history. Restoring a Run activates its generated definitions and imports its
extension classes into the interpreter, which is why it never happens inside
the long-lived backend: two unrelated Runs never share one process.

The worker performs no model or backend call and runs no Search work. It catches
up once, subscribes to later PostgreSQL commits, and keeps one disposable set of
typed head and historical frames. It exits after ``IDLE_S`` without a request.
"""

from __future__ import annotations

import asyncio
import contextlib
import sys
import time
from collections.abc import Iterator
from pathlib import Path
from typing import TYPE_CHECKING, cast

import uvicorn
from eventsourcing.utils import get_topic

from engine.event import store as event_store
from engine.event.base import Event
from engine.event.events import GeneratedDefinitionPublished
from engine.builtin import load_run_definitions
from output.web.live_registry import bind_member_socket
from output.web.member_app import create_member_app
from output.web.view import WebViewStore
from startup.run_config_store import load_run_config

if TYPE_CHECKING:
    from eventsourcing.projection import ApplicationSubscription

IDLE_S = 600.0
_IDLE_CHECK_S = 5.0


class _QuietServer(uvicorn.Server):
    """Uvicorn without its own signal handlers: the service stops this worker with SIGTERM, and the default handler would turn that into a graceful drain this process does not need."""

    @contextlib.contextmanager
    def capture_signals(self) -> Iterator[None]:
        yield


def restore_view(
    run_home: Path,
) -> "tuple[WebViewStore, event_store.SearchApplication, str, int]":
    """Restore one Run head and capture the subscription start position."""
    config = load_run_config(str(run_home))
    load_run_definitions(config.search.kind)
    run_id = config.run.require_run_id()
    app = event_store.open_search_store(run_id, read_only=True)
    try:
        captured = app.head_position()
        if captured is None:
            raise AssertionError("the Run database has no journal head")
        captured_notification_id, captured_revision = captured
        run_state = event_store.restore_run_state(
            app, run_home, version=captured_revision
        )
        events = event_store.read_events(app, version=captured_revision)
    except BaseException:
        app.close()
        raise
    store = WebViewStore(
        run_home,
        lambda version: event_store.restore_run_state(app, run_home, version),
    )
    store.publish(run_state, events)
    return store, app, run_id, captured_notification_id


def _advance_subscription(
    subscription: "ApplicationSubscription",
) -> bool:
    try:
        next(subscription)
    except StopIteration:
        return False
    return True


async def _follow(
    run_home: Path,
    store: WebViewStore,
    app: event_store.SearchApplication,
    subscription: "ApplicationSubscription",
) -> None:
    while await asyncio.to_thread(_advance_subscription, subscription):
        frame = store.head
        if frame is None:
            continue
        stored_tail = app.select_stored_events(
            originator_id=frame.state.id,
            gt=frame.state.version,
        )
        if not stored_tail:
            continue
        tail: list[Event] = []
        definition_topic = get_topic(GeneratedDefinitionPublished)
        for stored in stored_tail:
            event = cast(Event, app.mapper.to_domain_event(stored))
            if stored.topic == definition_topic:
                if not isinstance(event, GeneratedDefinitionPublished):
                    raise TypeError(
                        f"stored generated Definition is {type(event).__name__}"
                    )
                from engine.generated_definitions import activate_generated_definition

                activate_generated_definition(
                    run_home,
                    module_name=event.module_name,
                    package_relpath=event.package_relpath,
                )
            tail.append(event)
        events = tuple(tail)
        frame.state.fold(events, notify=False)
        store.publish(frame.state, events)


async def _serve(run_home: Path, socket_path: Path) -> None:
    store, event_app, run_id, captured_head = restore_view(run_home)
    last_request = time.monotonic()

    def touched() -> None:
        nonlocal last_request
        last_request = time.monotonic()

    app = create_member_app(
        store,
        run_id=run_id,
        on_request=touched,
    )
    sock = bind_member_socket(socket_path)
    server = _QuietServer(uvicorn.Config(app, log_config=None, access_log=False))
    task = asyncio.ensure_future(server.serve(sockets=[sock]))
    subscription = event_app.subscription(captured_head)
    follow = asyncio.ensure_future(_follow(run_home, store, event_app, subscription))
    try:
        while not task.done() and not follow.done():
            await asyncio.sleep(_IDLE_CHECK_S)
            if time.monotonic() - last_request > IDLE_S:
                server.should_exit = True
                await task
        task.result()
        if follow.done():
            follow.result()
    finally:
        subscription.stop()
        subscription.__exit__(None, None, None)
        try:
            await follow
        finally:
            event_app.close()
            sock.close()
            socket_path.unlink(missing_ok=True)


def main(argv: list[str]) -> None:
    if len(argv) != 2:
        raise SystemExit("usage: aibuildai __web_projection RUN_HOME SOCKET")
    asyncio.run(_serve(Path(argv[0]), Path(argv[1])))


if __name__ == "__main__":
    main(sys.argv[1:])

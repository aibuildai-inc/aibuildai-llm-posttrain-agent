"""Store RunState events with the eventsourcing library."""

from __future__ import annotations

import time
from collections.abc import Callable
from contextlib import contextmanager
from contextvars import ContextVar
from pathlib import Path
from uuid import UUID

# Event classes and mapper results are selected at run time, so those boundaries use Any.
from typing import TYPE_CHECKING, Any, cast

import orjson
from dbos import SQLAlchemyDatasource
from eventsourcing.application import Application
from eventsourcing.persistence import (
    ApplicationRecorder,
    Mapper,
    StoredEvent,
    Subscription,
    Transcoder,
)
from eventsourcing.utils import get_topic
from eventsourcing.projection import ApplicationSubscription
from eventsourcing_sqlalchemy.datastore import SQLAlchemyDatastore
from eventsourcing_sqlalchemy.factory import SQLAlchemyFactory
from eventsourcing_sqlalchemy.recorders import (
    SQLAlchemyApplicationRecorder,
    SQLAlchemySubscription,
)
from pydantic import TypeAdapter
from pydantic_core import to_json
from sqlalchemy import create_engine, event as sqlalchemy_event, text
from sqlalchemy.engine import Connection, Engine
from sqlalchemy.orm import Session, scoped_session, sessionmaker

from engine.event.base import Event
from engine.event.events import (
    GeneratedDefinitionPublished,
    RunEpochOpened,
    RunOpened,
)
from engine.run_state import RunState, merge_generated_definition

if TYPE_CHECKING:
    from collections.abc import Iterator, Sequence
    from typing import NoReturn

    from engine.durable_execution import DurableExecution

# Stored journal schema for this build. Cross-version restore and resume are
# unsupported: readers require the exact APP_VERSION and SCHEMA_VERSION in
# RunOpened. Bump this whenever the persisted shape or semantics change.
# Historical schema changes live in Git history.
# Schema 92 adds Run-keyed aggregates and DBOS applications to the frozen
# Program environment and Input shapes introduced by schema 91.
# Schema 93 moves the run's own siblings under the run home and removes the
# run code directory. Schema 94 removes the publication numbering from the
# persisted Agent record and the title from the Writer's output; the bump is
# separate from 93 because journals recorded at 93 carry those fields, and
# ``AgentRuntimeRecord`` is a plain BaseModel that would ignore the extra
# silently rather than refuse it. Schema 95 drops the recorded checkpoint-path
# field from TrainingOutput, which no reader ever had (the name itself is now a
# retired identifier the gate refuses, so it is described here rather than
# written). The field is gone from the Output shape, so a journal written by a
# 94 build replays a payload the 95 TrainingOutput refuses; the bump is what
# turns that into the stated "finish this run on the old binary" refusal
# instead of a validation error in the middle of a resume. Schema 96
# dropped the backend-account and session-directory fields from RunState and
# the persisted Agent record, retired three event topics, added
# SessionReplaced, and put the structured candidate on TurnComplete. Schema 97
# makes the Action occurrence the durable execution unit: the identity record
# carries no lifecycle, status, workflow, fork, or recorded Failure of its
# own; ActionRecord owns workflow_id, Attempt timing, the rate limit pause,
# the recoverable Failure, and the result; ExecutionCreated names the exact
# owner Action; Attempt and resource facts name their Action. Schema 98
# retires the retry-ceiling event topic (the PermanentError raised at
# the ceiling is the one record of that condition), drops the never-produced
# fields of the conversation facts (the reasoning-token count on UsageDelta,
# the reasoning profile on SessionStarted, the sub-agent attribution on the
# facts that are never nested, the unread thinking signature), narrows
# SessionStarted to the mounted server names, SessionReplaced to the new
# session id, and records the turn's usage per model in the product's own
# vocabulary instead of the provider's; a 97 journal gets the stated refusal.
# Schema 102 removes the frozen Program environment archive: its event
# topic and the RunState field that recorded the archive's run-relative path
# and SHA-256 are both gone (their names are described rather than written,
# following schema 95), so a 101 journal replays a topic this build no longer
# knows. The run's one prepared environment directory is now an ordinary
# run-home resource with no durable lifecycle fact of its own.
# Schema 105 records SearchResultSelected with only SearchOutput.
SCHEMA_VERSION = 105
_JOURNAL_SCHEMA = f"journal_{SCHEMA_VERSION}"


class RunPinMismatch(RuntimeError):
    """The current process does not match the run that made this store."""


def raise_pin_mismatch(
    mismatches: "Sequence[tuple[str, object, object]]",
) -> "NoReturn":
    lines = [
        f"resume refused: {len(mismatches)} run-environment pin "
        f"mismatch{'es' if len(mismatches) != 1 else ''} against this run's "
        "stored start event:",
        *(
            f"  - {name}: stored={stored!r} current={current!r}"
            for name, stored, current in mismatches
        ),
    ]
    if {name for name, _, _ in mismatches} & {"app_version", "schema_version"}:
        lines.append(
            "app_version/schema_version differ from the stored values. "
            "Finish this run on the old binary, or start a new run."
        )
    raise RunPinMismatch("\n".join(lines))


def _validate_event(event: object) -> Any:
    """Validate one event as it crosses the stored-bytes boundary.

    Unknown keys are DROPPED rather than refused. This runs only on the read
    side -- ``_Mapper.to_domain_event`` is its one caller -- so the strictness
    it used to apply could never catch a bad writer; all it could do was decide
    whether an OLD journal is still legible to the CURRENT build. Under
    ``extra="forbid"`` the answer was no: every field ever deleted from an event
    made every journal written before that deletion unreadable, which took
    evidence export and ``memorize`` down with it. Four event types plus
    one nested record had already drifted that way.

    What still fails is what should: an unknown or unresolvable event topic, a
    field whose value no longer fits its declared type, and a missing required
    field. A stored key the current shape does not declare is the one case that
    is now read as history rather than as corruption, which is what it is.
    Deliberate forward-incompatibility remains the schema-version pin's job, not
    this function's.
    """
    from engine.builtin.aibuildai.io import SearchOutput
    from engine.run_state import RunConfigRecord

    adapter = TypeAdapter(type(event))
    adapter.rebuild(
        _types_namespace={
            "RunConfigRecord": RunConfigRecord,
            "RunState": RunState,
            "SearchOutput": SearchOutput,
        }
    )
    return adapter.validate_python(vars(event), extra="ignore")


class _Mapper(Mapper):
    def to_domain_event(self, stored_event: StoredEvent) -> Any:
        return _validate_event(super().to_domain_event(stored_event))


class _ApplicationRecorder(SQLAlchemyApplicationRecorder):
    """Use short owned Sessions in the subscription's dedicated reader thread."""

    def subscribe(
        self, gt: int | None = None, topics: "Sequence[str]" = ()
    ) -> "Subscription[ApplicationRecorder]":
        engine = self.datastore.engine
        if not isinstance(engine, Engine):
            raise AssertionError("the journal subscription needs an Engine")
        reader = SQLAlchemyApplicationRecorder(
            SQLAlchemyDatastore(session_maker=sessionmaker(bind=engine)),
            events_table_name=self.events_table_name,
            schema_name=self.schema_name,
        )
        return SQLAlchemySubscription(reader, gt=gt, topics=topics)


class _Factory(SQLAlchemyFactory):
    application_recorder_class = _ApplicationRecorder


class SearchTranscoder(Transcoder):
    """Encode product values as JSON data."""

    def encode(self, obj: Any) -> bytes:
        data = to_json(obj, inf_nan_mode="constants")
        try:
            orjson.loads(data)
        except orjson.JSONDecodeError as error:
            raise ValueError("stored value contains a non-finite float") from error
        return data

    def decode(self, data: bytes) -> Any:
        return orjson.loads(data)


_CONSTRUCTION_SESSION = ContextVar[scoped_session[Session]]("aibuildai_event_session")
_SESSION_SCOPE = ContextVar[object]("aibuildai_event_session_scope")


def _application_session() -> scoped_session[Session]:
    return _CONSTRUCTION_SESSION.get()


def _session_scope() -> object:
    return _SESSION_SCOPE.get()


class SearchApplication(Application):
    """Persist RunState through the eventsourcing SQLAlchemy adapter."""

    snapshotting_intervals = {RunState: 200}
    log_section_size = 200

    def __init__(
        self,
        engine: Engine,
        datasource: SQLAlchemyDatasource | None,
        *,
        run_id: str,
        originator_id: UUID,
        env: dict[str, str],
    ) -> None:
        self.engine = engine
        self.datasource = datasource
        self.run_id = run_id
        self.originator_id = originator_id
        self._sessions = scoped_session(
            sessionmaker(bind=engine), scopefunc=_session_scope
        )
        with self._session_operation():
            token = _CONSTRUCTION_SESSION.set(self._sessions)
            try:
                super().__init__(env=env)
            finally:
                _CONSTRUCTION_SESSION.reset(token)

    def close(self) -> None:
        Application.close(self)
        self.engine.dispose()

    @contextmanager
    def _session_operation(self) -> "Iterator[None]":
        token = _SESSION_SCOPE.set(object())
        try:
            yield
        finally:
            self._sessions.remove()
            _SESSION_SCOPE.reset(token)

    # eventsourcing selects query keywords and domain classes at run time, so this adapter keeps its callback, query, and result types generic.
    def _read(self, read: Callable[[], Any]) -> Any:
        with self._session_operation():
            try:
                return read()
            finally:
                self._sessions.rollback()

    def select_stored_events(self, **kwargs: Any) -> tuple[StoredEvent, ...]:
        return cast(
            "tuple[StoredEvent, ...]",
            self._read(lambda: tuple(self.recorder.select_events(**kwargs))),
        )

    def select_events(self, **kwargs: Any) -> tuple[Any, ...]:
        return self._read(lambda: tuple(self.events.get(**kwargs)))

    def head_position(self) -> "tuple[int, int] | None":
        """Return one atomic journal notification and aggregate revision."""
        recorder = cast(SQLAlchemyApplicationRecorder, self.recorder)
        # The adapter selects its mapped event record class at run time.
        record = cast(Any, recorder.events_record_cls)

        def select_head() -> "tuple[int, int] | None":
            row = (
                self._sessions.query(record.id, record.originator_version)
                .filter(record.originator_id == self.originator_id)
                .order_by(record.id.desc())
                .first()
            )
            return None if row is None else (int(row[0]), int(row[1]))

        return cast("tuple[int, int] | None", self._read(select_head))

    def subscription(self, gt: int) -> ApplicationSubscription:
        """Start after one head and require the PostgreSQL LISTEN boundary."""
        subscription = ApplicationSubscription(self, gt=gt)
        subscription.__enter__()
        channel = cast(SQLAlchemyApplicationRecorder, self.recorder).channel_name
        deadline = time.monotonic() + 5.0
        while time.monotonic() < deadline:
            with self.engine.connect() as connection:
                listening = connection.execute(
                    text(
                        "SELECT EXISTS (SELECT 1 FROM pg_stat_activity "
                        "WHERE datname = current_database() AND query = :query "
                        "AND pid <> pg_backend_pid())"
                    ),
                    {"query": f"LISTEN {channel};"},
                ).scalar_one()
            if listening:
                return subscription
            time.sleep(0.01)
        subscription.stop()
        subscription.__exit__(None, None, None)
        raise RuntimeError("the PostgreSQL journal subscription did not start")

    def restore(self, *, version: int | None = None) -> RunState:
        return cast(
            RunState,
            self._read(
                lambda: self.repository.get(self.originator_id, version=version)
            ),
        )

    def save_pending(
        self,
        build: Callable[[Callable[..., None]], None],
        *,
        state: RunState | None = None,
        ts: float,
        live: bool,
        workflow_path: str | None,
        workflow_step: int | None,
        durable: bool = False,
    ) -> tuple[tuple[Event, ...], int]:
        from datetime import UTC, datetime

        if durable and state is not None:
            raise AssertionError("a durable retry must restore its own RunState")

        def write() -> tuple[tuple[Event, ...], int]:
            with self._session_operation():
                if not durable:
                    with self._sessions.begin():
                        return save()
                if self.datasource is None:
                    raise AssertionError("a read-only event store cannot append")
                self._sessions.registry.set(self.datasource.sql_session())
                try:
                    return save()
                finally:
                    self._sessions.registry.clear()

        def save() -> tuple[tuple[Event, ...], int]:
            current = state or cast(RunState, self.repository.get(self.originator_id))
            timestamp = datetime.now(UTC)

            def emit(
                event_type: type[Event],
                target: "DurableExecution | str | None",
                **facts: object,
            ) -> None:
                target_path = (
                    target if target is None or isinstance(target, str) else target.path
                )
                current.trigger_event(
                    cast(Any, event_type),
                    target=target_path,
                    ts=ts,
                    live=live,
                    workflow_path=workflow_path,
                    workflow_step=workflow_step,
                    timestamp=timestamp,
                    **facts,
                )

            build(emit)
            if not current.pending_events:
                raise AssertionError("domain operation raised no pending event")
            recordings = Application.save(self, current)
            events = tuple(cast(Event, item.domain_event) for item in recordings)
            return events, current.version

        if durable:
            if self.datasource is None:
                raise AssertionError("a read-only event store has no DBOS datasource")
            result = self.datasource.transaction(write)()
        else:
            result = write()
        return result


def open_search_store(run_id: str, *, read_only: bool = False) -> SearchApplication:
    """Open one Run aggregate in the shared PostgreSQL journal."""
    from infra.postgresql import database_url

    db_url = database_url()
    engine = create_engine(db_url, pool_pre_ping=True)
    if read_only:

        @sqlalchemy_event.listens_for(engine, "begin")
        def configure_read_transaction(connection: Connection) -> None:
            connection.exec_driver_sql("SET TRANSACTION READ ONLY")

    datasource = (
        None
        if read_only
        else SQLAlchemyDatasource.create(db_url, engine=engine, schema=_JOURNAL_SCHEMA)
    )
    return SearchApplication(
        engine,
        datasource,
        run_id=run_id,
        originator_id=RunState.create_id(run_id),
        env={
            "PERSISTENCE_MODULE": f"{__name__}:_Factory",
            "SQLALCHEMY_URL": db_url,
            "SQLALCHEMY_SCOPED_SESSION_TOPIC": f"{__name__}:_application_session",
            "SQLALCHEMY_SCHEMA": _JOURNAL_SCHEMA,
            "MAPPER_TOPIC": f"{__name__}:_Mapper",
            "TRANSCODER_TOPIC": f"{__name__}:SearchTranscoder",
            "CREATE_TABLE": "no" if read_only else "yes",
            "AGGREGATE_CACHE_MAXSIZE": "1",
        },
    )


def iter_run_events(
    app: SearchApplication, *, reverse: bool = False
) -> "Iterator[Event | RunOpened]":
    cursor: int | None = None if reverse else 0
    while True:
        page = app.select_events(
            originator_id=app.originator_id,
            gt=None if reverse else cursor,
            lte=cursor if reverse else None,
            desc=reverse,
            limit=20,
        )
        if not page:
            return
        yield from cast("tuple[Event | RunOpened, ...]", page)
        cursor = page[-1].originator_version - reverse


def read_run_opened_fields(
    app: SearchApplication,
) -> "dict[str, object] | None":
    """Read stable start fields without a version-specific domain class."""
    # _read rolls the scoped session back after the query. Without it this
    # read leaves an open transaction, and the next save_pending on the same
    # Application fails with "A transaction is already begun on this
    # Session" -- which broke every fresh run's startup save.
    stored = app._read(lambda: app.recorder.select_events(app.originator_id, limit=1))
    if not stored:
        return None
    first = stored[0]
    if first.topic != get_topic(RunOpened):
        raise TypeError(f"first stored topic is {first.topic}, not RunOpened")
    fields = orjson.loads(first.state)
    if not isinstance(fields, dict):
        raise TypeError("stored RunOpened state is not an object")
    if first.originator_id != app.originator_id or fields.get("run_id") != app.run_id:
        raise ValueError("stored Run identity differs from the requested Run ID")
    return fields


def read_first_epoch(app: SearchApplication) -> "RunEpochOpened | None":
    """Read only the first process epoch from the RunState stream."""
    events = app.select_events(originator_id=app.originator_id, limit=2)
    event = events[1] if len(events) == 2 else None
    return event if isinstance(event, RunEpochOpened) else None


def read_generated_definitions(
    app: SearchApplication,
    *,
    version: int | None = None,
) -> "tuple[tuple[str, str], ...]":
    """Read package publication facts before RunState snapshot restore.

    A publication is recorded every time the Search reaches it, so the stream
    can hold the same module more than once; folding through the one repeat
    rule is what keeps this reader and the run state's own fold agreeing on
    what such a stream means."""
    published: list[tuple[str, str]] = []
    for event in app.select_events(originator_id=app.originator_id, lte=version):
        if isinstance(event, GeneratedDefinitionPublished):
            merge_generated_definition(
                published, event.module_name, event.package_relpath
            )
    return tuple(published)


def restore_run_state(
    app: SearchApplication, run_home: Path, version: int | None = None
) -> RunState:
    """Activate published Definitions before the repository restores RunState."""
    from engine.generated_definitions import activate_generated_definition

    for module_name, package_relpath in read_generated_definitions(
        app, version=version
    ):
        activate_generated_definition(
            run_home, module_name=module_name, package_relpath=package_relpath
        )
    return app.restore(version=version)


def read_events(
    app: SearchApplication, *, version: int | None = None
) -> "list[Event | RunOpened]":
    """Read only this application's bound Run aggregate."""
    return list(
        cast(
            "tuple[Event | RunOpened, ...]",
            app.select_events(originator_id=app.originator_id, lte=version),
        )
    )

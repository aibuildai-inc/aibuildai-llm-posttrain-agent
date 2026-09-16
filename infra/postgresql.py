"""Own one private PostgreSQL cluster and fixed AIBuildAI database."""

from __future__ import annotations

import fcntl
import os
import pwd
import subprocess
import time
from pathlib import Path

import psycopg
from psycopg import sql
from psycopg.conninfo import make_conninfo
from sqlalchemy import URL

from infra.owned_tools import (
    POSTGRESQL_MAJOR,
    POSTGRESQL_VERSION,
    postgresql_environment,
    postgresql_root,
)
from infra.process.transient_unit import (
    start_transient_user_unit,
    stop_transient_user_unit,
    unit_active,
)

_DATABASE = "aibuildai"
_TIMEOUT = "30"
# The cluster is one per user -- one data dir, one socket, reused by every
# caller -- so it gets a unit of its own rather than whichever caller happened
# to start it. Named for the major it serves, since that is what the data dir
# and socket are keyed on too.
_UNIT = f"aibuildai-postgresql-{POSTGRESQL_MAJOR}"


class PostgreSQLUnavailable(ValueError): ...


def _socket_dir() -> Path:
    runtime = os.environ.get("XDG_RUNTIME_DIR")
    if not runtime:
        raise PostgreSQLUnavailable("PostgreSQL needs XDG_RUNTIME_DIR")
    return Path(runtime) / "aibuildai" / "postgresql" / str(POSTGRESQL_MAJOR)


def _binary(name: str) -> Path:
    return postgresql_root() / "bin" / name


def _pg_ctl(*args: str, allowed: tuple[int, ...] = (0,)) -> int:
    result = subprocess.run(
        [str(_binary("pg_ctl")), *args],
        env={**os.environ, **postgresql_environment()},
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in allowed:
        detail = result.stderr.strip() or result.stdout.strip()
        raise PostgreSQLUnavailable(f"pg_ctl {args[0]} failed: {detail}")
    return result.returncode


def _server_version(data: Path, conninfo: str) -> int:
    try:
        with psycopg.connect(conninfo) as connection:
            row = connection.execute(
                "SELECT current_setting('server_version_num'), "
                "current_setting('data_directory')"
            ).fetchone()
    except psycopg.Error as error:
        raise PostgreSQLUnavailable(f"PostgreSQL cannot answer SQL: {error}") from error
    if row is None or not isinstance(row[0], str) or not row[0].isdigit():
        raise PostgreSQLUnavailable("PostgreSQL returned no numeric server version")
    if Path(str(row[1])).resolve() != data.resolve():
        raise PostgreSQLUnavailable("the socket does not serve the owned cluster")
    version = int(row[0])
    if version // 10000 != POSTGRESQL_MAJOR:
        raise PostgreSQLUnavailable(
            f"the server is major {version // 10000}; AIBuildAI needs "
            f"major {POSTGRESQL_MAJOR}. A migration is required"
        )
    return version


def _start(data: Path, socket: Path, log: Path) -> None:
    """Start the postmaster in its own user unit, and wait for it to accept connections.

    NOT ``pg_ctl start``. Every command that touches the journal runs inside its
    own systemd transient unit with ``KillMode=mixed``, and a postmaster forked
    from there joins that unit's cgroup. The cluster is shared -- one data dir,
    one socket, and ``ensure_postgresql`` hands the running server to whoever
    asks next -- so parenting it to its first caller means that caller's exit
    takes the database away from every other run still using it. A Run that
    finished would kill a Run that had hours left, and the survivor saw only
    ``connection is bad`` from whatever statement it happened to be running.

    A unit of its own decouples the two: the cluster outlives any one command and
    stops only when it is stopped. ``--service-type=exec`` needs the postmaster
    in the foreground, so this execs ``postgres`` directly and waits for
    readiness itself instead of letting ``pg_ctl -w`` daemonize and poll.
    """
    if unit_active(_UNIT):
        # The flock plus the pg_ctl status check above say no cluster is
        # serving this data dir, so an active unit here is a leftover that
        # cannot be reused under its own name.
        stop_transient_user_unit(_UNIT)
    start_transient_user_unit(
        unit=_UNIT,
        command=[
            str(_binary("postgres")),
            "-D",
            str(data),
            "-h",
            "",
            "-k",
            str(socket),
            "-c",
            "unix_socket_permissions=0700",
        ],
        environment=postgresql_environment(),
        # pg_ctl -l appended the postmaster's stderr to this file; keep writing
        # it so an operator reads startup and recovery where they always have,
        # not only in the journal.
        properties=(
            f"StandardOutput=append:{log}",
            f"StandardError=append:{log}",
            # The cluster is shared by every live run, and nothing in a run
            # survives losing it: one refused connection ends the run.
            # On a shared host it is also the largest cgroup under the user
            # manager, so systemd-oomd picks it first under memory pressure
            # (measured 2026-09-06: "systemd-oomd killed 17 process(es) in
            # this unit", then a run 40 s later died on the dead socket). Let
            # systemd bring it back: crash recovery takes seconds, pooled
            # connections re-ping, and the default start-rate limit still
            # stops a kill loop. A clean stop stays a stop.
            "Restart=on-failure",
            "RestartSec=2",
        ),
    )
    # pg_isready, not pg_ctl status: the caller connects the moment this
    # returns, and status only reports that a postmaster holds the pidfile --
    # true seconds before the socket answers, and true throughout crash
    # recovery. pg_ctl start -w tested the connection itself; this restores
    # that guarantee now that the postmaster is started by systemd instead.
    deadline = time.monotonic() + float(_TIMEOUT)
    while True:
        ready = subprocess.run(
            [str(_binary("pg_isready")), "-q", "-h", str(socket)],
            env={**os.environ, **postgresql_environment()},
            check=False,
            capture_output=True,
        )
        if ready.returncode == 0:
            return
        if not unit_active(_UNIT):
            raise PostgreSQLUnavailable(f"PostgreSQL exited during startup; see {log}")
        if time.monotonic() >= deadline:
            raise PostgreSQLUnavailable(
                f"PostgreSQL did not accept connections within {_TIMEOUT}s; see {log}"
            )
        time.sleep(0.1)


def ensure_postgresql() -> str:
    """Start the owned cluster when needed and return the fixed database URL."""
    home = Path.home()
    root = home / ".aibuildai" / "postgresql" / str(POSTGRESQL_MAJOR)
    data = root / "data"
    socket = _socket_dir()
    private_paths = (
        *reversed(root.parents[:2]),
        root,
        *reversed(socket.parents[:2]),
        socket,
    )
    for path in private_paths:
        if path.is_symlink():
            raise PostgreSQLUnavailable(f"private PostgreSQL symlink: {path}")
        path.mkdir(mode=0o700, parents=True, exist_ok=True)
        if path.is_symlink() or not path.is_dir():
            raise PostgreSQLUnavailable(
                f"private PostgreSQL path is not a directory: {path}"
            )
        os.chmod(path, 0o700)
    with (root / ".lock").open("w") as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if data.exists() or data.is_symlink():
            if data.is_symlink() or not (data / "PG_VERSION").is_file():
                raise PostgreSQLUnavailable(f"PostgreSQL data is not a cluster: {data}")
        else:
            _pg_ctl(
                "initdb",
                "-D",
                str(data),
                "-o",
                "--auth-local=peer --auth-host=reject --encoding=UTF8 --no-locale",
            )
        cluster_major = (data / "PG_VERSION").read_text().strip()
        if cluster_major != str(POSTGRESQL_MAJOR):
            raise PostgreSQLUnavailable(
                f"the cluster is major {cluster_major}; AIBuildAI needs major "
                f"{POSTGRESQL_MAJOR}. A migration is required"
            )
        status = _pg_ctl("status", "-D", str(data), allowed=(0, 3))
        if status == 3:
            _start(data, socket, root / "postgresql.log")
        admin = make_conninfo(
            dbname="postgres", user=pwd.getpwuid(os.getuid()).pw_name, host=str(socket)
        )
        version = _server_version(data, admin)
        pinned = POSTGRESQL_MAJOR * 10000 + int(POSTGRESQL_VERSION.split(".")[1])
        if version < pinned:
            _pg_ctl("stop", "-D", str(data), "-m", "fast", "-w", "-t", _TIMEOUT)
            _start(data, socket, root / "postgresql.log")
            _server_version(data, admin)
        with psycopg.connect(admin, autocommit=True) as connection:
            exists = connection.execute(
                "SELECT 1 FROM pg_database WHERE datname = %s", (_DATABASE,)
            ).fetchone()
            if exists is None:
                connection.execute(
                    sql.SQL("CREATE DATABASE {}").format(sql.Identifier(_DATABASE))
                )
        return database_url()


def database_url() -> str:
    return URL.create(
        "postgresql+psycopg",
        username=pwd.getpwuid(os.getuid()).pw_name,
        database=_DATABASE,
        query={"host": str(_socket_dir())},
    ).render_as_string(hide_password=False)

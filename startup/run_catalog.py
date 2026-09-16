"""The durable run catalog: every indexed run and its identity.

One owner for what the Web Workspace needs: the runs the XDG index points
at, read from the run's own durable files. Nothing here is stored a second
time, and no Run status state machine lives here. The final config owns the
run id, task name, and model; the journal owns the stop facts
(``engine.run_query.read_run_head``). A local run's systemd unit owns
process liveness. Kubernetes start mode is fresh-run-only, so its historical
Workspace operations are unavailable and never query systemd.
"""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from pydantic import ValidationError

from engine.run_query import RunHeadFacts, read_run_head
from infra.fs import run_index
from infra.host_resource.cgroup import run_unit_name
from infra.process.transient_unit import unit_active
from startup.run_config_store import load_run_config

logger = logging.getLogger(__name__)
KUBERNETES_CONTROL_UNAVAILABLE = (
    "Kubernetes start mode supports fresh runs only; "
    "Workspace control and replay are unavailable"
)


class RunConfigUnreadable(ValueError):
    """The run home has no final config this build can read."""


@dataclass(frozen=True)
class RunIdentity:
    run_id: str
    task_name: str
    model: str
    kubernetes_start_mode: bool


@dataclass(frozen=True)
class IndexedRun:
    identity: RunIdentity
    run_home: str

    @property
    def run_id(self) -> str:
        return self.identity.run_id


def run_identity(run_home: str) -> RunIdentity:
    try:
        config = load_run_config(run_home)
        return RunIdentity(
            run_id=config.run.require_run_id(),
            task_name=config.run.task_name,
            model=config.llm.default.model,
            kubernetes_start_mode=config.run.require_kubernetes_start_mode(),
        )
    except FileNotFoundError as exc:
        raise RunConfigUnreadable(f"run has no final config: {run_home}") from exc
    except (AssertionError, ValidationError) as exc:
        raise RunConfigUnreadable(
            f"run final config is not compatible with global Run ID lookup: {run_home}"
        ) from exc


def run_active(run: IndexedRun) -> bool:
    """Whether this local run's systemd process owner is active."""
    if run.identity.kubernetes_start_mode:
        return False
    return unit_active(run_unit_name(run.run_id))


def run_facts(run_id: str, run_home: str) -> RunHeadFacts:
    """The journal's stop facts for one run home."""
    return read_run_head(run_id, run_home)


def indexed_run_homes() -> list[str]:
    """Every indexed run home that still exists, newest first, each once."""
    homes: list[str] = []
    for row in run_index.read_runs():
        if row.run_home not in homes and os.path.isdir(row.run_home):
            homes.append(row.run_home)
    homes.sort(reverse=True)
    return homes


def indexed_runs() -> list[IndexedRun]:
    """Every indexed run this build can identify. A run whose final config cannot be read is logged and left out; it never fails the catalog."""
    runs: list[IndexedRun] = []
    seen: set[str] = set()
    for run_home in indexed_run_homes():
        try:
            identity = run_identity(run_home)
        except RunConfigUnreadable as exc:
            logger.info("run catalog skips %s: %s", run_home, exc)
            continue
        if identity.run_id in seen:
            continue
        seen.add(identity.run_id)
        runs.append(
            IndexedRun(
                identity=identity,
                run_home=run_home,
            )
        )
    return runs

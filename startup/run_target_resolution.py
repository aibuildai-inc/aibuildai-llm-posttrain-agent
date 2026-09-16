"""Resolve an explicit run target: a run id, a run home, or a run dir."""
from __future__ import annotations

import os
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING

from engine.paths import RunTimestamp
from startup.run_catalog import (
    RunConfigUnreadable,
    RunIdentity,
    indexed_runs,
    run_identity,
)
from startup.run_config_store import load_run_config

if TYPE_CHECKING:
    from config import AgentConfig, WorkUnitTimeConfig


@dataclass(frozen=True)
class ResolvedRunTarget:
    run_id: str
    run_home: str

    @property
    def run_timestamp(self) -> str:
        return Path(self.run_home).name


class RunTargetResolutionError(ValueError):
    """A run argument that cannot be resolved to a run."""


def _identity(run_home: str) -> RunIdentity:
    try:
        return run_identity(run_home)
    except RunConfigUnreadable as exc:
        raise RunTargetResolutionError(str(exc)) from exc


def _target_from_path(raw: Path) -> ResolvedRunTarget:
    path = raw.expanduser()
    run_home = path
    run_timestamp = path.name
    if not RunTimestamp.is_canonical(run_timestamp):
        raise RunTargetResolutionError(
            f"run path has no run directory timestamp: {path}")
    return ResolvedRunTarget(
        run_id=_identity(str(run_home)).run_id,
        run_home=str(run_home),
    )


def resolve_run_target(arg: str) -> ResolvedRunTarget:
    resolved = Path(arg).expanduser()
    if resolved.is_absolute() or resolved.exists():
        return _target_from_path(resolved)
    runs = indexed_runs()
    for run in runs:
        if run.run_id == arg:
            return ResolvedRunTarget(run_id=run.run_id, run_home=run.run_home)
    raise RunTargetResolutionError(
        f"run id {arg!r} not found in run index; available: {[r.run_id for r in runs]}"
    )


def recover_config_from_rundir(run_home: Path, *, review_writer: bool) -> "AgentConfig":
    """Rebuild the AgentConfig a finished run used, from its archived run config.

    Every run archives its complete resolved AgentConfig at ``<run_home>/run_config.json`` (``save_run_config``). a Resume reloads it, then production bootstrap puts the current run's journaled Run Budget and Work Unit time rules over the archive. This finished-run paper command reads the archive and changes only what paper writing needs. The run's LOCATION follows the dir the user pointed at, so a copied run dir writes its paper in place; every other SETTING — ``work_units`` / ``search`` / ``resources`` / ``llm`` (including ``llm.default.effort`` and by-role entries) — comes from the archive.

    Overrides: ``run.task_name`` + ``run.playground_root`` from the passed run home (``<playground_root>/<task_name>/<run_id>``); ``writer.enable`` on so the command always writes the paper; ``verifier.enable['writer']`` follows ``review_writer`` (the --review flag). A run that had the Writer off archived no ``work_units`` time entry for it (a switched-off role needs none), so this command, which is what switches the Writer on, supplies the Writer's entry, and its reviewer's under ``--review``, when the archive carries none: the documented default of a fixed 60 minutes and no retry, the same values ``example.yaml`` ships for both.
    """
    # Absolutize WITHOUT resolving symlinks, matching RunConfig._normalize_root_path
    #: the run journaled its playground_root as the user's path string (a
    # symlinked playground_root is kept verbatim, not its target). Resolving here
    # would derive a different playground_root than the run recorded and make the
    # resume archive_location pin (<run_home>/run_config.json) mismatch.
    run_home = Path(run_home)
    if not run_home.is_absolute():
        run_home = Path.cwd() / run_home
    run_home = Path(os.path.normpath(str(run_home)))
    config = load_run_config(str(run_home))
    return config.model_copy(
        update={
            "run": config.run.model_copy(
                update={
                    "task_name": run_home.parent.name,
                    "playground_root": str(run_home.parent.parent),
                }
            ),
            "writer": config.writer.model_copy(update={"enable": True}),
            "verifier": config.verifier.model_copy(
                update={"enable": {**config.verifier.enable, "writer": review_writer}}
            ),
            "work_units": _with_paper_time_entries(config, review_writer=review_writer),
        }
    )


def _with_paper_time_entries(
    config: "AgentConfig", *, review_writer: bool
) -> "list[WorkUnitTimeConfig]":
    """The archived ``work_units`` plus the entries the paper command's own Writer needs.

    The command switches the Writer on, so it owns the Writer's time entry (and the reviewer's when it asks for the review) whenever the archive has none; an entry the run did archive is kept as it is."""
    from config import FixedWorkUnitBudgetConfig, WorkUnitTimeConfig
    from engine.builtin.aibuildai.agents.writer.agent import WriterAgent, WriterVerifierAgent

    needed: list[type[WriterAgent] | type[WriterVerifierAgent]] = [WriterAgent]
    if review_writer:
        needed.append(WriterVerifierAgent)
    entries = list(config.work_units)
    present = {entry.identity for entry in entries}
    for unit_type in needed:
        if (unit_type.kind, unit_type.name) in present:
            continue
        entries.append(
            WorkUnitTimeConfig(
                kind=unit_type.kind,
                name=unit_type.name,
                budget=FixedWorkUnitBudgetConfig(kind="fixed", minutes=60),
                retry=0,
            )
        )
    return entries

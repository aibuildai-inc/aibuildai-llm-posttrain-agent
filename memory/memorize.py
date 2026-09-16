"""User memorize flow: one Agent reads the task's runs and writes its memory.

Orchestration only. It finds the task's finished runs, names the directories of
each run the Agent may open, hands them to one Memory Agent session, validates
what came back, and publishes it. Nothing is copied: what the Agent may see is
decided by filesystem permission on real run directories, so this module knows
no Search, Agent, Board, or package layout, and it names no provider. Produces
only the task-level user memory document -- no patterns, no INDEX.
"""
from __future__ import annotations

import json
import logging
from pathlib import Path

from memory.store import MemoryStore
from memory.task_memory import (
    build_task_memory,
    publish_task_memory,
    validate_task_memory,
)

logger = logging.getLogger(__name__)

# What a run shows a reader: the executions it materialized, the task data it
# was given, and what it handed back. Its private grading state, its Submitter
# records, and its library cache are not here, and that absence is the whole
# boundary -- there is no allowlist of product features to keep in step.
MEMORY_RUN_ROOTS = ("workspace", "public", "deliverable")


def read_run_identity(run_home: Path) -> dict[str, str]:
    """The task name and global Run ID of one run, from its final run config.

    The final ``run_config.json`` is the one authority for a run's identity, so a run home without it is not a finished run and is not discovered.
    """
    config = json.loads((Path(run_home) / "run_config.json").read_text())
    if not isinstance(config, dict) or not isinstance(config.get("run"), dict):
        raise ValueError(f"invalid final run config: {run_home}/run_config.json")
    run = config["run"]
    task_name = run.get("task_name")
    run_id = run.get("run_id")
    if not isinstance(task_name, str) or not task_name:
        raise ValueError("final run config has no task_name")
    if not isinstance(run_id, str) or not run_id:
        raise ValueError("final run config has no run_id")
    return {"task_name": task_name, "run_id": run_id}


def iter_run_homes(playground_root: Path, task_name: str | None = None) -> list[Path]:
    """Finished run homes under a playground root, by their recorded identity.

    Two run homes claiming the same identity means one was copied, which would make a later reader count the same run twice, so it raises instead.
    """
    playground_root = Path(playground_root)
    if not playground_root.exists():
        raise FileNotFoundError(f"playground_root does not exist: {playground_root}")
    runs: dict[tuple[str, str], Path] = {}
    for task_dir in sorted(playground_root.iterdir()):
        if not task_dir.is_dir():
            continue
        for run_home in sorted(task_dir.iterdir()):
            if not (run_home / "run_config.json").is_file():
                continue
            identity = read_run_identity(run_home)
            if task_name and identity["task_name"] != task_name:
                continue
            key = (identity["task_name"], identity["run_id"])
            prior = runs.get(key)
            if prior is not None:
                raise ValueError(
                    f"duplicate final run identity {key!r}: {prior} and {run_home}"
                )
            runs[key] = run_home
    return list(runs.values())


async def run_memorize(
    task_name: str, playground_root: str | Path, store: MemoryStore, model: str,
) -> dict:
    run_homes = iter_run_homes(Path(playground_root), task_name)
    if not run_homes:
        raise ValueError(
            f"no runs found for task '{task_name}' under {playground_root}; "
            "expected a run home with a final run_config.json"
        )
    # One unreadable run home must not cost the task its memory. A run written
    # by an older build can be unreadable for reasons that say nothing about the
    # runs beside it, and this loop is the only place that difference becomes
    # visible, so it is where the damage is contained. The failure is logged
    # with the run home that caused it; only an empty result raises.
    run_roots: list[tuple[str, tuple[Path, ...]]] = []
    skipped: list[tuple[Path, str]] = []
    for run_home in run_homes:
        try:
            roots = tuple(
                path
                for name in MEMORY_RUN_ROOTS
                for path in (run_home / name,)
                if path.is_dir()
            )
            if not roots:
                raise ValueError("the run home holds none of the readable run roots")
            run_roots.append((read_run_identity(run_home)["run_id"], roots))
        # Blind by intent: the set of ways an older run can be unreadable is
        # open, and every one of them is a fact about that run alone. Narrowing
        # this would re-arm the task-wide outage it exists to prevent.
        except Exception as error:  # noqa: BLE001
            skipped.append((run_home, f"{type(error).__name__}: {error}"))
            logger.warning("memorize: skipping unreadable run %s -- %s", run_home, error)
    if not run_roots:
        detail = "; ".join(f"{home}: {why}" for home, why in skipped)
        raise ValueError(
            f"no readable runs for task '{task_name}' under {playground_root}; "
            f"all {len(run_homes)} run home(s) failed: {detail}"
        )
    markdown = await build_task_memory(
        task_name,
        run_roots,
        model=model,
    )
    task_path = publish_task_memory(
        store.tasks_dir / f"{task_name}.md", validate_task_memory(markdown, task_name)
    )
    return {"runs_read": len(run_homes), "task_memory_path": task_path}

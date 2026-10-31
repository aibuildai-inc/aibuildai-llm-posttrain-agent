"""User memorize flow: one Agent reads the task's runs and writes its memory.

Orchestration only. It validates one caller-named Run against its archived
identity and authoritative terminal journal, names the directories the Agent
may open, hands them to one Memory Agent session under caller-declared cost and
time ceilings, validates what came back, and publishes it. Nothing is copied:
what the Agent may see is decided by filesystem permission on real run
directories. Produces only the task-level user memory document -- no patterns,
no INDEX.
"""
from __future__ import annotations

import asyncio
import json
import math
import os
from pathlib import Path

from memory.store import MemoryStore
from memory.task_memory import (
    MemoryCostObserver,
    build_task_memory,
    publish_task_memory,
    validate_task_memory,
)

# What a run shows a reader: the executions it materialized, the task data it
# was given, and what it handed back. Its private grading state, its Submitter
# records, and its library cache are not here, and that absence is the whole
# boundary -- there is no allowlist of product features to keep in step.
MEMORY_RUN_ROOTS = ("workspace", "public", "deliverable")


def eligible_source_run(
    *, playground_root: str | Path, run_home: str | Path,
    source_run_id: str, task_name: str,
) -> tuple[str, tuple[Path, ...]]:
    """Resolve one exact source run and refuse unless its journal says completed."""
    root = Path(playground_root).resolve(strict=True)
    home = Path(run_home)
    if home.is_symlink():
        raise ValueError("source run home must not be a symlink")
    home = home.resolve(strict=True)
    try:
        home.relative_to(root)
    except ValueError as exc:
        raise ValueError("source run home is outside the attempt playground root") from exc
    run_config_path = home / "run_config.json"
    if run_config_path.is_symlink() or not run_config_path.is_file():
        raise ValueError("source run config is missing or unsafe")
    config = json.loads(run_config_path.read_text(encoding="utf-8"))
    run = config.get("run") if isinstance(config, dict) else None
    if not isinstance(run, dict):
        raise ValueError("source run has no readable run identity")
    if run.get("run_id") != source_run_id:
        raise ValueError("source run ID differs from the expected completed Run")
    if run.get("task_name") != task_name:
        raise ValueError("source run belongs to a different task")

    # The archived config is written before execution. The journal's terminal
    # Run result, not that config or a directory timestamp, decides eligibility.
    from engine.event.store import open_search_store, read_run_opened_fields
    from engine.run_query import read_run_head

    app = open_search_store(source_run_id, read_only=True)
    try:
        opened = read_run_opened_fields(app)
    finally:
        app.close()
    if not isinstance(opened, dict):
        raise ValueError("source Run journal has no RunOpened identity")
    if Path(str(opened.get("run_home", ""))).resolve() != home:
        raise ValueError("source Run journal belongs to a different run home")
    if Path(str(opened.get("archive_location", ""))).resolve() != (home / "run_config.json"):
        raise ValueError("source Run journal pins a different archived config")

    facts = read_run_head(source_run_id, home)
    if not facts.readable or facts.incompatible or facts.result != "completed":
        raise ValueError(
            f"source Run {source_run_id} is not an eligible completed run "
            f"(readable={facts.readable}, result={facts.result}, "
            f"incompatible={facts.incompatible!r})"
        )
    paths_list: list[Path] = []
    for name in MEMORY_RUN_ROOTS:
        path = home / name
        if path.is_symlink():
            raise ValueError(f"source run root is a symlink: {path}")
        if path.is_dir():
            paths_list.append(path)
    paths = tuple(paths_list)
    if not paths:
        raise ValueError("completed source run has no readable memory roots")
    return source_run_id, paths


async def run_memorize(
    task_name: str, playground_root: str | Path, run_home: str | Path,
    source_run_id: str, store: MemoryStore, model: str,
    generation_budget_usd: float, timeout_seconds: float,
) -> dict:
    """Build memory from one verified completed run under caller-declared caps."""
    if not os.environ.get("AIBUILDAI_API_KEY"):
        raise ValueError("AIBUILDAI_API_KEY is required; refusing before provider launch")
    if (not isinstance(generation_budget_usd, int | float)
            or not math.isfinite(generation_budget_usd)
            or generation_budget_usd <= 0):
        raise ValueError("a positive explicit memory generation budget is required")
    if (not isinstance(timeout_seconds, int | float)
            or not math.isfinite(timeout_seconds) or timeout_seconds <= 0):
        raise ValueError("a positive explicit memory generation timeout is required")
    source = eligible_source_run(
        playground_root=playground_root, run_home=run_home,
        source_run_id=source_run_id, task_name=task_name,
    )
    observer = MemoryCostObserver(float(generation_budget_usd))
    async with asyncio.timeout(timeout_seconds):
        markdown = await build_task_memory(
            task_name, [source], model=model,
            max_budget_usd=float(generation_budget_usd), observer=observer,
        )
    provider_reported_cost = observer.total_cost_usd
    if provider_reported_cost is None:
        raise ValueError("provider did not return settled memory-generation cost; candidate refused")
    if not math.isfinite(provider_reported_cost) or provider_reported_cost < 0:
        raise ValueError("provider returned invalid memory-generation cost; candidate refused")
    if provider_reported_cost > generation_budget_usd:
        raise ValueError("provider-reported memory cost exceeded its admitted budget; candidate refused")
    local_ledger_charge = observer.local_ledger_charge_usd
    if local_ledger_charge is None:
        raise ValueError("local memory cost ledger is incomplete; candidate refused")
    if not math.isfinite(local_ledger_charge) or local_ledger_charge < 0:
        raise ValueError("local memory cost ledger is invalid; candidate refused")
    if local_ledger_charge > generation_budget_usd:
        raise ValueError("measured local memory cost exceeded its admitted budget; candidate refused")
    task_path = publish_task_memory(
        store.tasks_dir / f"{task_name}.md", validate_task_memory(markdown, task_name)
    )
    return {
        "runs_read": 1,
        "source_run_id": source_run_id,
        "task_memory_path": task_path,
        "generation_budget_usd": float(generation_budget_usd),
        "generation_cost_usd": local_ledger_charge,
        "generation_cost_label": "measured",
        "provider_reported_cost_usd": provider_reported_cost,
        "provider_reported_cost_label": "reported",
    }

"""XDG run index: an append-only JSONL pointer file listing every run's location, so the Web Workspace can find each final run config.

This does NOT move run output or copy its identity. It records only {run_home} per run under $XDG_DATA_HOME/aibuildai/runs.jsonl. The final config at run_home owns the global run ID. Liveness/terminal status is derived on read (the run's systemd unit and journal), never stored here.
"""
from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def index_path() -> Path:
    base = os.environ.get("XDG_DATA_HOME")
    root = Path(base) if base else Path.home() / ".local" / "share"
    return root / "aibuildai" / "runs.jsonl"


@dataclass(frozen=True)
class RunIndexEntry:
    run_home: str


def append_run(*, run_home: str) -> None:
    path = index_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    line = json.dumps({"run_home": run_home})
    with open(path, "a", encoding="utf-8") as f:
        f.write(line + "\n")


def read_runs() -> list[RunIndexEntry]:
    path = index_path()
    if not path.exists():
        return []
    out: list[RunIndexEntry] = []
    for line in path.read_text(encoding="utf-8").splitlines():
        line = line.strip()
        if not line:
            continue
        # Skip malformed JSON lines: the index tolerates them by design,
        # for an append-only log whose last line can be torn by a
        # crash mid-append. It is NOT a defensive fallback over unexpected
        # state -- a corrupt pointer line must never abort enumeration.
        try:
            obj = json.loads(line)
        except json.JSONDecodeError:
            continue
        out.append(RunIndexEntry(run_home=obj["run_home"]))
    return out

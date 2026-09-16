"""Detect whether THIS run was hit by an out-of-memory (OOM) kill, and WHERE.

When a cgroup in the run's resource tree exceeds its ``memory.max``, the kernel OOM killer kills a process inside it. On the orchestrator that surfaces as a generic "terminated by signal" — hiding the real cause (something blew its memory ceiling).

This module reads the cgroup v2 ``memory.events`` ``oom_kill`` counters across the run's own cgroup subtree (the ``aibuildai*`` ancestor of the current process — the delegated scope, which holds framework/ and the durable Execution tree) so cli.py can print an honest "killed by OOM" message instead.

It names the exact cgroup that recorded the kill and reads THAT cgroup's ``memory.max``. The tree is what makes this possible: a cgroup path mirrors the durable execution that owns it (``search_1/coder_candidate_1/coder_1``), and the kernel attributes the kill to it with no polling window. Before the tree there were two flat slices and the report could only quote the shared worker slice's cap, which is not the number anyone hit.

Pure-stdlib, try-guarded, never raises: a detection failure must never crash the exit summary. Returns "no OOM" on any non-cgroup-v2 host, when the process is not under an aibuildai scope (the auto-update installer child), or when the cgroup files are unreadable / already removed.
"""
from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from infra.host_resource.tree import own_cgroup_dir

_CGROUP_NAME_PREFIX = "aibuildai"


@dataclass(frozen=True)
class KilledCgroup:
    """One cgroup that the kernel OOM-killed something in."""

    cgroup: str            # path relative to the run's scope, e.g. "search_1/coder_candidate_1/coder_1"
    memory_max: "str | None"   # THAT cgroup's ceiling, in bytes; None if already reaped


@dataclass(frozen=True)
class OomReport:
    """Outcome of the OOM scan.

    ``killed`` is one entry per cgroup that actually recorded a kill: its path relative to the run's scope (``search_1/coder_candidate_1/coder_1``) and its OWN ``memory.max`` -- the ceiling that fired, which is the one number the operator can act on.

    EVERY killed cgroup, not one of them. With ``search.input.parallel: 4`` two WorkUnits can OOM in the same run, and naming only the first sends the operator to fix one WorkUnit and leaves the other to fail again. ``memory_max`` is None only when the cgroup was already reaped before the scan.

    There is no summed kill counter. There used to be, and nothing ever read it.
    """
    killed: "tuple[KilledCgroup, ...]"

    @property
    def oom_killed(self) -> bool:
        return bool(self.killed)


_NO_OOM = OomReport(killed=())


def _run_cgroup_root(start: Path) -> "Path | None":
    """Walk up from ``start`` to the topmost ancestor whose name starts with ``aibuildai`` (the run's cgroup subtree root — e.g. ``aibuildai.slice``).

    Returns None when neither ``start`` nor any ancestor is an aibuildai cgroup (the process is not under an aibuildai scope).
    """
    cur = start
    # Find the nearest aibuildai ancestor (inclusive of start).
    while cur != cur.parent and not cur.name.startswith(_CGROUP_NAME_PREFIX):
        cur = cur.parent
    if not cur.name.startswith(_CGROUP_NAME_PREFIX):
        return None
    # Climb to the TOPMOST contiguous aibuildai ancestor.
    while cur.parent.name.startswith(_CGROUP_NAME_PREFIX):
        cur = cur.parent
    return cur


def _read_oom_kill(memory_events: Path) -> int:
    """Sum the ``oom_kill`` + ``oom_group_kill`` counters from one cgroup-v2 ``memory.events`` file. Returns 0 on any read error."""
    total = 0
    try:
        for line in memory_events.read_text().splitlines():
            parts = line.split()
            if len(parts) == 2 and parts[0] in ("oom_kill", "oom_group_kill"):
                try:
                    total += int(parts[1])
                except ValueError:
                    continue
    except OSError:
        return 0
    return total


def scan_oom_subtree(root: Path) -> OomReport:
    """Every cgroup under ``root`` that recorded an OOM kill, with the ceiling each hit.

    ``memory.events`` is HIERARCHICAL: a kill inside ``search_1/coder_candidate_1/coder_1`` also counts in ``search_1/coder_candidate_1`` and in the run's scope. So an ancestor is reported only when it recorded a kill that none of its descendants did -- otherwise the run's scope would be listed as a victim of every kill in it, and the ceiling quoted would be one that never fired.

    Deepest first, because the deepest cgroup is the one whose ceiling actually bit and the one the operator can do something about.
    """
    killed: dict[Path, int] = {}
    try:
        for events in root.rglob("memory.events"):
            count = _read_oom_kill(events)
            if count > 0:
                killed[events.parent] = count
    except OSError:
        return _NO_OOM

    out: list[KilledCgroup] = []
    for cgroup_dir, count in sorted(
        killed.items(), key=lambda kv: len(kv[0].parts), reverse=True
    ):
        # Subtract what the descendants already account for. An ancestor whose whole
        # count came from below did not have a ceiling of its own fire, and naming it
        # would point the operator at a limit that never bit.
        from_below = sum(
            n for other, n in killed.items()
            if other != cgroup_dir and cgroup_dir in other.parents
        )
        if count <= from_below:
            continue
        try:
            memory_max: "str | None" = (cgroup_dir / "memory.max").read_text().strip()
        except OSError:
            memory_max = None
        try:
            name = str(cgroup_dir.relative_to(root)) or root.name
        except ValueError:
            name = cgroup_dir.name
        out.append(KilledCgroup(cgroup=name, memory_max=memory_max))
    return OomReport(killed=tuple(out))


def detect_run_oom() -> OomReport:
    """Return whether this run's cgroup subtree recorded any OOM kill.

    Never raises — any failure (cgroup v1, no scope, unreadable files) yields ``_NO_OOM``.
    """
    try:
        # One parse of /proc/self/cgroup: tree.own_cgroup_dir. Its raise on a
        # non-v2 host (and any OSError) is absorbed by this function's
        # never-raises contract below.
        own = own_cgroup_dir()
        if not own.is_dir():
            return _NO_OOM
        root = _run_cgroup_root(own)
        if root is None:
            return _NO_OOM
        return scan_oom_subtree(root)
    except Exception:  # noqa: BLE001 — diagnostic must never crash the exit summary
        return _NO_OOM

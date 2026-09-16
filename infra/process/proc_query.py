"""Pure /proc process queries: pid liveness, PPID, descendant enumeration.

Linux-only (the binary targets Linux). Single home for the /proc/PID/stat PPID parse and the os.kill(pid, 0) liveness check.
"""
from __future__ import annotations

import os


def pid_alive(pid: int) -> bool:
    """True iff a process with this PID currently exists (any owner)."""
    if pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True  # exists but owned by another user
    return True


def ppid_of(pid: int) -> int | None:
    """Parent PID from /proc/<pid>/stat, or None if unreadable/malformed. comm can contain spaces and parens, so split after the final ')'."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            stat = f.read().decode("latin1", errors="replace")
        rparen = stat.rindex(")")
        return int(stat[rparen + 2:].split()[1])
    except (OSError, ValueError, IndexError):
        return None


def proc_starttime(pid: int) -> int | None:
    """Process start time (field 22 of /proc/<pid>/stat), in clock ticks since boot; None if the pid is dead or its stat is unreadable/malformed.

    The bare PID recycles on a long-lived host, so it cannot by itself identify a process instance. start-time is fixed for the life of a process and does not recur for a reused PID within one boot, so (pid, start-time) is a stable instance identity that survives PID recycling. comm can contain spaces and parens, so the fields are split after the final ')': the split then starts at field 3 (state), making start-time field 22 index 22 - 3 = 19."""
    try:
        with open(f"/proc/{pid}/stat", "rb") as f:
            stat = f.read().decode("latin1", errors="replace")
        rparen = stat.rindex(")")
        return int(stat[rparen + 2:].split()[19])
    except (OSError, ValueError, IndexError):
        return None


def descendants_of(root_pid: int) -> list[int]:
    """All transitive descendants of root_pid via a /proc PPID walk (BFS). Excludes root_pid itself, so a caller can SIGKILL the result without killing itself. Order is BFS (parents before their children)."""
    children_by_parent: dict[int, list[int]] = {}
    try:
        entries = os.listdir("/proc")
    except OSError:
        return []
    for entry in entries:
        if not entry.isdigit():
            continue
        parent = ppid_of(int(entry))
        if parent is None:
            continue
        children_by_parent.setdefault(parent, []).append(int(entry))
    out: list[int] = []
    queue = [root_pid]
    while queue:
        p = queue.pop(0)
        for child in children_by_parent.get(p, []):
            out.append(child)
            queue.append(child)
    return out

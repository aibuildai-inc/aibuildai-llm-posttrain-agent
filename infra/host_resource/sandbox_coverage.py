"""Invariant: every host path the framework hands a sandboxed agent process must be covered by the bind plan ("told-to-use implies bound").

Checked where the Agent launch is assembled, with the binds and the env / add_dirs / cwd of the same launch. Three rules, each verified against the real per-role specs (probe, 2026-06-11):
  - env write-anchors (cache redirects) must land inside a WRITABLE (rw) bind or the /tmp tmpfs — anywhere else the write is read-only or ephemeral (the cache is silently discarded each session).
  - add_dirs must land inside some bind (ro/rw) or a sandbox-owned mount.
  - cwd must land inside a bind, OR be an ancestor of a bind (bwrap auto-creates parent dirs, so the dir exists to chdir into), OR a sandbox mount.

PATH is colon-joined and never path-checked. CLAUDE_CONFIG_DIR is owned by the Claude launcher, so it is absent from the env checked here.
"""
from __future__ import annotations

import os
from typing import TYPE_CHECKING

if TYPE_CHECKING:
    from infra.host_resource.confinement import Confinement

# build_bwrap_argv emits these unconditionally; /tmp is a writable tmpfs.
_SANDBOX_MOUNTS = ("/tmp", "/dev", "/proc")


def _norm(p: str) -> str:
    return os.path.normpath(p)


def _within(child: str, parent: str) -> bool:
    c, p = _norm(child), _norm(parent)
    return c == p or c.startswith(p.rstrip("/") + "/")


def _is_ancestor_of_any(ref: str, paths: tuple[str, ...]) -> bool:
    r = _norm(ref).rstrip("/") + "/"
    return any(_norm(p).startswith(r) for p in paths)


def assert_paths_covered(
    conf: "Confinement",
    *,
    env: "dict[str, str]",
    add_dirs: "tuple[str, ...]",
    cwd: "str | None",
) -> None:
    ro = tuple((*conf.system_read_paths, *conf.ro_paths, *conf.hard_ro))
    rw = tuple((*conf.system_write_paths, *conf.rw_paths))
    binds = ro + rw

    # env write-anchors -> writable bind or /tmp
    for key, val in env.items():
        if key == "PATH" or not isinstance(val, str):
            continue
        # MLFLOW_TRACKING_URI is a file:// URI; strip the scheme to a path.
        path = val[len("file://"):] if val.startswith("file://") else val
        if not path.startswith("/"):
            continue
        if _within(path, "/tmp") or any(_within(path, w) for w in rw):
            continue
        raise AssertionError(
            f"host confinement does not cover env[{key}]={val!r}: a write target "
            f"must be inside a writable (rw) bind. rw={list(rw)} "
            "(told-to-use-implies-bound)"
        )

    # add_dirs -> any bind or sandbox mount
    for d in add_dirs:
        if any(_within(d, m) for m in _SANDBOX_MOUNTS) or any(_within(d, b) for b in binds):
            continue
        raise AssertionError(
            f"host confinement does not cover add_dir={d!r}: it is handed to the "
            f"SDK as a read dir but bound nowhere. binds={list(binds)}"
        )

    # cwd -> bind, ancestor-of-bind, or sandbox mount
    if cwd:
        if not (
            any(_within(cwd, m) for m in _SANDBOX_MOUNTS)
            or any(_within(cwd, b) for b in binds)
            or _is_ancestor_of_any(cwd, binds)
        ):
            raise AssertionError(
                f"host confinement does not cover cwd={cwd!r}: the chdir "
                f"target neither is nor contains any bind. binds={list(binds)}"
            )

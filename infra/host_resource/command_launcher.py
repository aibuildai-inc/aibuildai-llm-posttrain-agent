"""Build command cgroup scripts and clean command cgroups.

``command_self_enroll_launcher`` is a pure string builder: given the original Bash command, the tool-use id that names the command's own cgroup, and the cgroup that holds this agent's commands, it returns a rewritten command that first creates and joins a dedicated ``command-<id>`` cgroup, then runs the original command as the trailing statement, unchanged.

The launcher is a pure string builder. The other helpers do cgroup file I/O, kill the cgroup, call ``rmdir``, and use a short async sleep while the kernel removes its processes. The tree creates the parent cgroup and puts the cap on it; a command needs no cap of its own.
"""
from __future__ import annotations

import asyncio
import errno
from pathlib import Path

from infra.host_resource.cgroup import safe_tag
from infra.host_resource.tree import kill_tree


def command_self_enroll_launcher(command: str, tool_use_id: str, commands_root: str) -> str:
    """Return ``command`` rewritten to self-enroll into its own cgroup first.

    The returned script (1) sets ``cg`` to the per-command cgroup path (``command-<safe_tag(tool_use_id)>`` under ``commands_root``), (2) creates that cgroup directory and joins the current Bash process through ``BASHPID``, failing loud with a clear stderr message and a non-zero exit if either write fails, and (3) splices ``command`` verbatim as the trailing statement so its exit code and multi-statement semantics (``a && b; c``) are preserved. No ``set -e`` is emitted, so shell options in effect for ``command`` are exactly what the caller already had.

    ``commands_root`` is the cgroup the tree created for this agent's commands, named in the coordinates the COMMAND will see: ``/sys/fs/cgroup`` under the sandbox, because bwrap binds that cgroup there and everything above it -- the ceiling included -- is outside the mount; the cgroup's real host path without the sandbox.

    It is a PARAMETER, and that is the point. Which cgroup bounds a command (the RESOURCE axis) and whether a namespace hides the filesystem (the FILE-ISOLATION axis) are independent, and a command must land in its own cgroup either way. Hard-coding ``/sys/fs/cgroup`` here coupled them silently: with the sandbox off there is no bind, so that string named the host's cgroup ROOT, the enrol failed, and every command instead ran under the agent SESSION's cap -- a tenth of the share it is owed.
    """
    tag = safe_tag(tool_use_id)
    enroll_guard = (
        'mkdir "$cg" && echo $BASHPID > "$cg/cgroup.procs" || '
        "{ echo 'aibuildai: command cgroup self-enroll failed' >&2; exit 1; }"
    )
    return f"cg={commands_root}/command-{tag}\n{enroll_guard}\n{command}"


def command_cgroup_dir(commands_cgroup: Path, tool_use_id: str) -> Path:
    """The HOST path of the cgroup ``command_self_enroll_launcher`` creates for one command. The command names it in ITS OWN coordinates (under the sandbox, that is ``/sys/fs/cgroup``); this is the same directory as the orchestrator sees it."""
    return commands_cgroup / f"command-{safe_tag(tool_use_id)}"


def reap_command_cgroup(commands_cgroup: Path, tool_use_id: str) -> None:
    """Remove a finished command cgroup from outside the sandbox."""
    command_cgroup_dir(commands_cgroup, tool_use_id).rmdir()


def command_cgroup_for_pid(commands_cgroup: Path, pid: int) -> Path | None:
    """Find the direct command cgroup that contains ``pid``."""
    for leaf in (
        path for path in commands_cgroup.iterdir() if path.name.startswith("command-")
    ):
        if pid in {int(value) for value in (leaf / "cgroup.procs").read_text().split()}:
            return leaf
    return None


async def stop_command_cgroups(commands_cgroup: Path, leaf: Path | None = None) -> None:
    """Atomically kill, wait for, and remove one or all command cgroups."""
    target = leaf or commands_cgroup
    kill_tree(target)
    for _ in range(200):
        leaves = [leaf] if leaf else [
            path
            for path in commands_cgroup.iterdir()
            if path.name.startswith("command-")
        ]
        removed_leaf = False
        for path in leaves:
            try:
                path.rmdir()
                removed_leaf = leaf is not None
            except OSError as exc:
                if exc.errno not in {errno.EBUSY, errno.ENOTEMPTY}:
                    raise
        if removed_leaf or (
            leaf is None
            and not any(
                path.name.startswith("command-")
                for path in commands_cgroup.iterdir()
            )
        ):
            return
        await asyncio.sleep(0.01)
    raise AssertionError(f"command cgroup stayed populated after kill: {target}")

"""Claude launcher generator for managed and standalone backend sessions.

The Claude backend tells the Claude SDK ``cli_path=<this launcher>`` instead of the real claude binary. The launcher reopens the conversation-owned anonymous prompt descriptor, then re-execs the real claude binary directly or inside the unit's host confinement. The confinement itself (bwrap binds + cgroup scope) is the neutral ``Confinement`` value the engine (``engine.work_unit.agent.spec``) computed from the unit's role policy and resources, and the environment the session runs in is the AgentSpec's; this module adds ONLY the irreducibly Claude-specific bits and hands them to ``Confinement.as_script`` as inner argv, setenv, and a process-generic descriptor reopen: the CLI's own binary directory, its config directory (bound writable and named to the subprocess), and the Node runtime directory when the host has one.

Layer: infra/backends/claude. It imports the ``Confinement`` value type, the owned-tool resolver of infra, and the claude_agent_sdk; it does NOT import engine, and is NOT imported by engine.
"""

from __future__ import annotations

import ctypes
import dataclasses
import os
import shlex
from pathlib import Path
from typing import TYPE_CHECKING

from infra.backends.claude import config_paths
from infra.owned_tools import owned

if (
    TYPE_CHECKING
):  # annotation-only: the backend never imports infra's confinement at runtime
    from infra.host_resource.confinement import Confinement


_PROMPT_FD = 9
_LIBC = ctypes.CDLL(None, use_errno=True)


def _create_memfd(name: str) -> int:
    fd = _LIBC.memfd_create(os.fsencode(name), 1)
    if fd < 0:
        error = ctypes.get_errno()
        raise OSError(error, os.strerror(error))
    return fd


def build_agent_launcher(
    conf: "Confinement | None",
    *,
    prompt_source: str,
    path: str | None,
) -> tuple[str, int | None]:
    """Build one descriptor-bridging Claude launcher; return its path and, for an unconfined launch, the memfd that owns it.

    ``path`` is the AgentSpec's PATH, the one its owner composed for this launch (the process's own when the spec sets none); inside bwrap the CLI's own binary directory is put in front of it, so the CLI resolves itself the way it does on an unconfined host."""
    real_claude = str(owned("claude"))
    inner_argv = [
        real_claude,
        "--append-system-prompt-file",
        f"/proc/self/fd/{_PROMPT_FD}",
    ]
    reopen_fds = {_PROMPT_FD: prompt_source}
    if conf is None:
        script = (
            "#!/usr/bin/env bash\nset -u\n"
            f"exec {_PROMPT_FD}<{shlex.quote(prompt_source)}\n"
            + "exec "
            + " ".join(shlex.quote(arg) for arg in inner_argv)
            + ' "$@"\n'
        )
        launcher_fd = _create_memfd("aibuildai-claude-launcher")
        try:
            with os.fdopen(os.dup(launcher_fd), "w") as stream:
                stream.write(script)
            os.fchmod(launcher_fd, 0o500)
        except OSError:
            os.close(launcher_fd)
            raise
        return f"/proc/{os.getpid()}/fd/{launcher_fd}", launcher_fd
    if not conf.apply_bwrap:
        return conf.as_script(inner_argv, reopen_fds=reopen_fds), None

    claude_bin = str(Path(real_claude).resolve().parent)
    # The config dir the launcher rw-binds and the in-sandbox claude resolves
    # to. Both read the operator environment, so the bind and the subprocess's
    # own CLAUDE_CONFIG_DIR resolution can never disagree. Left
    # unnamed, the CLI's state file is the TOP-LEVEL ~/.claude.json, which sits
    # OUTSIDE the dir bound here: the sandboxed CLI can then neither read nor
    # write its own state, and every session dies seconds after it starts
    # (measured: 91 restarts, no work done).
    config_dir = str(config_paths.config_dir(os.environ))
    setenv = {
        "PATH": os.pathsep.join(
            part for part in (claude_bin, path or os.environ.get("PATH", "")) if part
        ),
        "CLAUDE_CONFIG_DIR": config_dir,
    }
    # The CLI's own view, added to the role's exact one: its binary directory,
    # the Node runtime directory when the host has one, and its config
    # directory writable.
    own_read = [claude_bin]
    nvm = Path(os.environ["HOME"]) / ".nvm"
    if nvm.is_dir():
        own_read.append(str(nvm))
    conf = dataclasses.replace(
        conf,
        system_read_paths=tuple(dict.fromkeys((*conf.system_read_paths, *own_read))),
        system_write_paths=tuple(dict.fromkeys((*conf.system_write_paths, config_dir))),
    )
    for p in conf.rw_paths:
        Path(p).mkdir(parents=True, exist_ok=True)
    return conf.as_script(inner_argv, setenv=setenv, reopen_fds=reopen_fds), None

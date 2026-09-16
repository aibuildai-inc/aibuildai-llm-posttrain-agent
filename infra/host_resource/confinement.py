"""Place one unit in its cgroup and optional bubblewrap file view."""

from __future__ import annotations

import os
import shlex
import shutil
import stat
import subprocess
from collections.abc import Mapping
from dataclasses import dataclass
from pathlib import Path

from infra.host_resource.cgroup import make_killable_preamble
from infra.host_resource.tree import enter_cgroup_preamble, oom_kill_count
from infra.sandbox.bwrap import build_bwrap_argv
from infra.util.paths import is_within

_SHEBANG = "#!/usr/bin/env bash\nset -u\n"


def probe_unprivileged_userns() -> None:
    """Check the two host facts bwrap needs: the binary, and an unprivileged user namespace."""
    bwrap = shutil.which("bwrap")
    if bwrap is None:
        raise ValueError("bwrap is not installed")
    try:
        proc = subprocess.run(
            [str(bwrap), "--ro-bind", "/", "/", "--unshare-user", "--", "/bin/true"],
            capture_output=True,
            text=True,
            timeout=5,
        )
    except subprocess.TimeoutExpired as exc:
        raise ValueError(
            "the bwrap user namespace check did not finish in 5 seconds"
        ) from exc
    if proc.returncode != 0:
        raise ValueError(
            "unprivileged user namespaces are disabled on this host; "
            f"bwrap exited {proc.returncode}: {proc.stderr.strip()!r}"
        )


# The read-only view of the operating system every sandboxed process needs to
# run at all: its binaries and shared objects, its configuration, and its
# runtime sockets. An assembler adds it explicitly, beside the paths that are
# that launch's own; nothing else is granted by default.
OS_READ_BASELINE: tuple[str, ...] = ("/usr", "/etc", "/run")


@dataclass(frozen=True)
class Confinement:
    """The exact host confinement of one unit: its cgroups, and under bwrap the exact file and device view. Frozen so a call site cannot mutate it after the producer stamped it; every path here is one the producer named, and nothing is granted by default."""

    cgroup: Path
    apply_bwrap: bool

    # The resolved working directory of a subprocess launch, bound and used as
    # one path. An Agent launch starts where its AgentSpec says.
    cwd: str | None = None

    # The full file and device view for this local launch.
    system_read_paths: tuple[str, ...] = ()
    system_write_paths: tuple[str, ...] = ()
    ro_paths: tuple[str, ...] = ()
    hard_ro: tuple[str, ...] = ()
    rw_paths: tuple[str, ...] = ()
    dev_nodes: tuple[str, ...] = ()
    mask_paths: tuple[str, ...] = ()
    pinned_device_count: int = 0

    # Where an Agent launch keeps its launcher history, beside the model-owned
    # scratch directory.
    step_dir: str = ""

    commands_cgroup: Path | None = None

    def memory_kills(self) -> int:
        """Return the cumulative OOM kill count for this unit."""
        return oom_kill_count(self.cgroup)

    def delegates_commands(self) -> bool:
        """Return whether this Agent has a command cgroup boundary."""
        return self.commands_cgroup is not None

    def commands_root(self) -> str:
        """Return the command-visible path of the command cgroup boundary."""
        if self.commands_cgroup is None:
            raise AssertionError(
                "commands_root() on a Confinement with no commands cgroup; the caller "
                "must gate on delegates_commands()"
            )
        return "/sys/fs/cgroup" if self.apply_bwrap else str(self.commands_cgroup)

    def return_command_process(self, pid: int) -> None:
        """Move an idle Agent shell back to its session cgroup."""
        (self.cgroup / "cgroup.procs").write_text(str(pid))

    def _preamble(self) -> str:
        """Make the payload killable, then put it in its cgroup. In that order: a process that joins a cgroup while still OOM-immune is a process the kernel cannot reap when that cgroup fills."""
        return make_killable_preamble() + "\n" + enter_cgroup_preamble(self.cgroup)

    def require_readable(self, *paths: str, owner: str) -> None:
        """Fail when a required path is outside everything this confinement binds.

        The question is whether the launched process can read the path, so the
        answer is every bind it gets: the operator's system paths and the
        role's own resolved ro/rw binds alike. Asking only about the system
        paths refused a path the confinement itself binds -- a task Program
        environment under the run home is bound rw by the role and was still
        reported missing, which made a sandboxed SETUP impossible to launch.
        The subprocess shape puts its read binds in ``hard_ro`` rather than
        ``ro_paths``, so both are asked; leaving ``hard_ro`` out kept a
        sandboxed score program from ever running.
        """
        allowed = (
            *self.system_read_paths,
            *self.system_write_paths,
            *self.ro_paths,
            *self.hard_ro,
            *self.rw_paths,
        )
        missing = [
            path for path in paths if not any(is_within(path, root) for root in allowed)
        ]
        if missing:
            raise AssertionError(
                f"{owner} needs readable path(s) {missing!r}, which this "
                "confinement does not bind. The check reads the operator's "
                "system paths AND the role's own resolved binds (ro_paths, "
                "hard_ro, rw_paths), so a path outside every one of them is "
                "either an operator setting to widen "
                "(resources.sandbox.system_read_paths or "
                "system_write_paths) or a directory grant the role's "
                "RolePolicy never asked for -- and only the first is fixed by "
                "a setting"
            )

    def require_writable(self, *paths: str, owner: str) -> None:
        """Fail when a required path is outside everything this confinement binds writable.

        The same reasoning as ``require_readable``: a role's own resolved rw
        binds make a path writable just as the operator's system write paths
        do, so both are asked."""
        allowed = (*self.system_write_paths, *self.rw_paths)
        missing = [
            path for path in paths if not any(is_within(path, root) for root in allowed)
        ]
        if missing:
            raise AssertionError(
                f"{owner} needs writable path(s) {missing!r}, which this "
                "confinement does not bind. The check reads the operator's "
                "system write paths AND the role's own resolved rw_paths, so "
                "a path outside both is either an operator setting to widen "
                "(resources.sandbox.system_write_paths) or a directory grant "
                "the role's RolePolicy never asked for -- and only the first "
                "is fixed by a setting"
            )

    def _exec_argv(self, inner_argv: "list[str]", setenv: "dict[str, str]") -> list[str]:
        """The one argv a confined process is exec'd with: ``inner_argv`` inside bwrap with this view and ``setenv``, or ``inner_argv`` itself without bwrap."""
        if not self.apply_bwrap:
            return list(inner_argv)
        mask_dirs = tuple(path for path in self.mask_paths if os.path.isdir(path))
        mask_files = tuple(path for path in self.mask_paths if not os.path.isdir(path))
        binds = build_bwrap_argv(
            self.ro_paths,
            [*self.system_write_paths, *self.rw_paths],
            hard_ro=[*self.system_read_paths, *self.hard_ro],
            dev_nodes=self.dev_nodes,
            mask_files=mask_files,
            mask_dirs=mask_dirs,
        )
        if self.pinned_device_count > 0:
            # The K bound /dev/nvidia<idx> nodes are in-sandbox indices 0..K-1;
            # an inherited host CUDA_VISIBLE_DEVICES would point CUDA at devices
            # that are absent from, or renumbered inside, the sandbox.
            renumbered = ",".join(str(i) for i in range(self.pinned_device_count))
            binds += ["--setenv", "CUDA_VISIBLE_DEVICES", renumbered]
        for k, v in setenv.items():
            binds += ["--setenv", k, v]
        if self.commands_cgroup is not None:
            # The process sees ONLY its own commands subtree at /sys/fs/cgroup. It
            # can create a cgroup per command and join it -- which is how the
            # kernel later attributes a command's OOM to that exact command -- and
            # it cannot reach the ceiling, which is on the parent, outside the bind.
            binds += ["--bind", str(self.commands_cgroup), "/sys/fs/cgroup"]
        binds += ["--die-with-parent"]
        return ["bwrap", *binds, "--", *inner_argv]

    @staticmethod
    def _exports(setenv: "dict[str, str]") -> str:
        return "".join(f"export {k}={shlex.quote(v)}\n" for k, v in setenv.items())

    def wrap(
        self,
        inner_argv: "list[str]",
        *,
        setenv: "dict[str, str] | None" = None,
    ) -> list[str]:
        """The argv the call site spawns. A bash that joins the cgroup and then ``exec``s the confined argv, so no extra process lingers inside the unit's limits and the payload is bounded from its first instruction."""
        setenv = dict(setenv or {})
        exports = "" if self.apply_bwrap else self._exports(setenv)
        return [
            "bash",
            "-c",
            self._preamble() + "\n" + exports + 'exec "$@"',
            "bash",
            *self._exec_argv(inner_argv, setenv),
        ]

    def as_script(
        self,
        inner_argv: "list[str]",
        *,
        setenv: "dict[str, str] | None" = None,
        reopen_fds: "Mapping[int, str] | None" = None,
    ) -> str:
        """Write the same launch as an executable script and return its path: the preamble, the descriptor reopens, and ``exec`` of the confined argv with the script's own arguments appended.

        Launchers are immutable step history. Exclusive creation makes concurrent starts choose different monotonically increasing names instead of overwriting evidence from an earlier start.
        """
        if not self.step_dir:
            raise AssertionError("agent Confinement requires step_dir")
        launcher_dir = Path(self.step_dir) / "launcher"
        launcher_dir.mkdir(parents=True, exist_ok=True)
        setenv = dict(setenv or {})
        reopen = "".join(
            f"exec {fd}<{shlex.quote(source)}\n"
            for fd, source in (reopen_fds or {}).items()
        )
        exports = "" if self.apply_bwrap else self._exports(setenv)
        argv = self._exec_argv(inner_argv, setenv)
        script = (
            _SHEBANG
            + self._preamble()
            + "\n"
            + reopen
            + exports
            + "exec "
            + " \\\n  ".join(shlex.quote(a) for a in argv)
            + ' "$@"\n'
        )

        index = 1
        while True:
            out = launcher_dir / f"{index:04d}.sh"
            try:
                fd = os.open(out, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o755)
            except FileExistsError:
                index += 1
                continue
            with os.fdopen(fd, "w") as stream:
                stream.write(script)
            out.chmod(
                out.stat().st_mode
                | stat.S_IRWXU
                | stat.S_IRGRP
                | stat.S_IXGRP
                | stat.S_IROTH
                | stat.S_IXOTH
            )
            return str(out)

"""The cgroup-v2 hierarchy that mirrors durable execution ownership.

systemd delegates one subtree to the product and then stops being involved. Everything below the delegation root is written directly to cgroupfs by this module, because the kernel is the only thing that can actually enforce a limit and a delegated subtree needs no privilege to manage.

    <delegation root>          the Run: the scope systemd handed us, carrying the run ceiling
    |-- framework/             the orchestrator and shared sidecars
    |-- mcp-<number>-<name>/   one local MCP server
    |-- search_1/              the durable Search root
    |   |-- coder_candidate_1/       durable structure with no resource limit
    |   |   +-- coder_1/       one WorkUnit Scope
    |   |       |-- session/   the WorkUnit process
    |   |       |-- commands/  Bash commands for an Agent
    |   |       +-- verifier_1/ a child WorkUnit Scope
    |   +-- writer_1/          a Search-owned WorkUnit Scope
    +-- writer_1/              an external root WorkUnit Scope

Every Scope is named after the durable execution that owns it. A WorkUnit's process runs in its ``session/`` leaf. Its child WorkUnits are Scopes below it, so the kernel applies the parent's saved CPU and memory limits to their total use.

Each WorkUnit carries its declared CPU and memory limits. A Composite adds no CPU or memory split. An Agent also has ``commands/`` below its Scope, so its one limit covers the model process, Bash commands, and child WorkUnits.

Three properties the kernel gives us for free, and which the code this replaces had re-implemented in Python:

* A parent constrains the sum of its descendants. No admission arithmetic.
* ``memory.events``'s ``oom_kill`` counter attributes a kill to an exact cgroup, with no polling window. No attribution code.
* ``cgroup.kill`` is atomic against concurrent forks (``cgroup_post_fork()`` checks ``CGRP_KILL`` before userspace sees the child) and ``setsid()`` cannot escape it. No pid enumeration.

Every write is read back. Across ~20 surveyed cgroup implementations — runc, containerd, crun, BenchExec — not one verifies that a limit it wrote took effect, and systemd itself downgrades the ENOENT from a missing controller to a DEBUG log and returns success. That is how this product's CPU limits were dead for three months without a single error.
"""

from __future__ import annotations

import errno
import logging
import os
from dataclasses import dataclass, replace
from pathlib import Path

from infra.host_resource.cgroup import HostCapabilityUnavailable


CGROUP_MOUNT = Path("/sys/fs/cgroup")

# What the RUN may take from the host. Normal is ~2000 tasks (measured: 1957 in a
# live run); a fork bomb is tens of thousands and grows exponentially, so 8192 stops
# the bomb with 4x headroom and cannot misfire on real work.
#
# This ceiling alone is NOT enough, and believing it was is the mistake this comment
# exists to prevent. It bounds what the run costs the HOST while leaving the run
# itself defenceless: measured, a fork bomb in one WorkUnit's training leaf took 63 of
# the run's 64 processes, left the orchestrator holding 1, and the orchestrator's
# next fork() returned EAGAIN -- it could no longer even kill the thing eating it.
# So the framework's share is RESERVED and the WorkUnits divide the rest (see plan.py),
# exactly as memory is divided, and for the same reason: THE BLAST RADIUS MUST EQUAL
# THE UNIT THE PRODUCT CAN RETRY.
RUN_PIDS_MAX = 8192

# cpu.weight, not cpu.max. A weight cannot silently fail the way a quota on an
# undelegated controller does, and it lets one WorkUnit use an idle host fully.
# 100 is the kernel's own default.
DEFAULT_CPU_WEIGHT = 100


class CgroupWriteRejected(HostCapabilityUnavailable):
    """A cgroup limit was written and did not read back as written.

    Never swallowed. A limit that did not take is indistinguishable, at runtime, from no limit at all -- and the product's promise is that the kernel bounds the run.

    It is a HOST capability failure, not a product bug: what the host delegated is decided by root, outside this process. Saying so is the base class's whole job -- the cli boundary renders the message plainly and exits EX_UNAVAILABLE, so the person who hit it is sent to their sysadmin instead of to the issue tracker.
    """


@dataclass(frozen=True)
class Controllers:
    """Which cgroup controllers this host actually delegated to us.

    Read from the delegation root's ``cgroup.controllers``, which lists what the parent enabled for us -- NOT what the kernel supports. Ubuntu ships ``Delegate=pids memory`` in ``user@.service``, so ``cpu`` is routinely absent and ``cpu.max`` does not exist anywhere in the user tree.
    """

    memory: bool
    pids: bool
    cpu: bool

    @classmethod
    def read(cls, cgroup_dir: Path) -> "Controllers":
        available = set((cgroup_dir / "cgroup.controllers").read_text().split())
        return cls(
            memory="memory" in available,
            pids="pids" in available,
            cpu="cpu" in available,
        )


def own_cgroup_dir() -> Path:
    """This process's own cgroup directory, from the cgroup-v2 ``0::`` line of ``/proc/self/cgroup``. Inside the delegated scope this IS the delegation root, so the product never has to guess where systemd put it."""
    for line in Path("/proc/self/cgroup").read_text().splitlines():
        hierarchy_id, _, cgroup_path = line.split(":", 2)
        if hierarchy_id == "0":
            return CGROUP_MOUNT / cgroup_path.lstrip("/")
    raise AssertionError(
        "/proc/self/cgroup has no cgroup-v2 ('0::') line, so this host is not "
        "running the unified cgroup hierarchy"
    )


def _write_verify(cgroup_dir: Path, filename: str, value: str) -> None:
    """Write one cgroup interface file and assert it reads back as written.

    This is the step nobody does. The kernel accepts a write into a controller that was never delegated, or silently clamps a value, and the writer sees success either way. Reading it back is the only way to learn that the limit the product promised is actually in force.
    """
    path = cgroup_dir / filename
    path.write_text(value)
    got = path.read_text().strip()
    if got != value.strip():
        raise CgroupWriteRejected(
            f"wrote {value!r} to {path} but it reads back as {got!r}; the kernel "
            f"did not accept the limit, so this run would not be bounded"
        )


def _enable_subtree_controllers(cgroup_dir: Path, controllers: Controllers) -> None:
    """Enable, for this cgroup's CHILDREN, every controller we were delegated, and assert every one of them came on.

    A controller must be switched on in the parent's ``cgroup.subtree_control`` before any child can carry it, and a cgroup may only enable what its own ``cgroup.controllers`` lists -- delegation does not cascade by itself.

    Read back like every other write in this module. ``subtree_control`` cannot be compared byte-for-byte the way a limit can (it is written as ``+memory +pids`` and reads back as ``memory pids``, in the kernel's own order), so the check is on the SET: a controller that was asked for and is not enabled means every child below this point would silently have no such limit file at all.
    """
    wanted = {
        name
        for name, present in (
            ("memory", controllers.memory),
            ("pids", controllers.pids),
            ("cpu", controllers.cpu),
        )
        if present
    }
    if not wanted:
        return
    path = cgroup_dir / "cgroup.subtree_control"
    path.write_text(" ".join(f"+{name}" for name in sorted(wanted)))
    enabled = set(path.read_text().split())
    missing = wanted - enabled
    if missing:
        raise CgroupWriteRejected(
            f"enabled {sorted(wanted)} in {path} but it reads back as "
            f"{sorted(enabled)}; {sorted(missing)} did not take, so no child of this "
            f"cgroup would carry those limits"
        )


def _drain_into(root: Path, leaf: Path) -> None:
    """Move EVERY process out of the delegation root into ``leaf``, and assert the root is then empty.

    Draining only ``os.getpid()`` is not enough. The orchestrator spawns sidecars before the tree is bootstrapped -- a proxy sidecar would be the long-lived one, the GPU and sandbox capability probes are transient -- and each is born in the root, because a fork inherits its parent's cgroup. They are still sitting there when the root has to become a parent.

    Read the root back like every other write in this module. A process left behind is not a cosmetic miss: cgroup v2 lets a cgroup hold processes OR enable controllers for its children, never both, so the ``cgroup.subtree_control`` write that follows would be refused with EBUSY and the run would die before its first agent.
    """
    procs = root / "cgroup.procs"
    for pid in procs.read_text().split():
        try:
            (leaf / "cgroup.procs").write_text(pid)
        except OSError as exc:
            # The process exited between the read and the write. It is out of the
            # root, which is the whole point of moving it. Nothing else is tolerated.
            if exc.errno != errno.ESRCH:
                raise
    remaining = procs.read_text().split()
    if remaining:
        raise CgroupWriteRejected(
            f"drained the delegation root {root} into {leaf}, but pids {remaining} are "
            f"still in it; the kernel refuses to enable a controller for the children of "
            f"a cgroup that still holds processes, so this run could carry no limits"
        )


@dataclass(frozen=True)
class Limits:
    """What one cgroup in the tree enforces.

    ``memory_max_bytes`` None means this cgroup adds no memory limit.

    ``oom_group`` makes a breach kill the whole leaf atomically. Inner cgroups keep it off because they contain child cgroups.
    """

    memory_max_bytes: int | None
    oom_group: bool
    cpu_weight: int = DEFAULT_CPU_WEIGHT
    cpu_max: str | None = None
    pids_max: int | None = None


class ResourceTree:
    """Owns the run's cgroup hierarchy for the life of the process.

    Construct once, in the orchestrator, immediately after the delegated scope re-exec. ``bootstrap()`` must run before any subprocess is spawned.
    """

    def __init__(self, root: Path | None = None, *, enforce: bool = True) -> None:
        """``enforce=False`` is the Kubernetes start mode: ``root`` is a plain directory, the tree keeps its shape so every launch path still has a place to self-enroll into, but no kernel limit is written and no controller is read, because the Pod has no cgroup2 filesystem and the Pod boundary is the fence."""
        self._enforce = enforce
        if not enforce:
            if root is None:
                raise AssertionError("an unenforced tree needs an explicit root")
            root.mkdir(parents=True, exist_ok=True)
            self._root = root
            self._controllers = Controllers(memory=False, pids=False, cpu=False)
            self._framework: Path | None = None
            return
        self._root = root if root is not None else own_cgroup_dir()
        self._controllers = Controllers.read(self._root)
        self._framework = None

    @property
    def root(self) -> Path:
        """The delegation root == the Run's cgroup."""
        return self._root

    @property
    def controllers(self) -> Controllers:
        return self._controllers

    def bootstrap(self, *, run_limits: Limits, framework_limits: Limits) -> Path:
        """Create ``framework/``, move the root's processes into it, and open the root for children.

        The order is forced by cgroup v2's no-internal-process rule: a cgroup may hold processes OR have controllers enabled for its children, never both. The orchestrator is born in the delegation root, so the root is unusable as a parent until it has been emptied.

        Returns the framework leaf, which holds the orchestrator and shared sidecars such as the LLM adapter. A sidecar spawned AFTER this point inherits the framework leaf at fork and lands here for free unless its owner moves it to an exact leaf. One spawned BEFORE it was born in the root is moved here by ``_drain_into``.
        """
        if not self._enforce:
            framework = self._root / "framework"
            framework.mkdir(exist_ok=True)
            self._framework = framework
            logging.info(
                "resource tree: kubernetes start mode, no kernel limit is in force; "
                "the Pod bounds the run. scope directories under %s",
                self._root,
            )
            return framework
        if not self._controllers.memory or not self._controllers.pids:
            raise CgroupWriteRejected(
                f"the systemd user manager delegated {self._delegated_names()} to this "
                f"run, but 'memory' and 'pids' are required to bound the run against "
                f"out-of-memory and fork-bomb death. Without them a configured limit is "
                f"false. Delegation is set by root, so ask an administrator to create "
                f"/etc/systemd/system/user@{os.getuid()}.service.d/delegate.conf holding "
                f"the lines '[Service]' and 'Delegate=pids memory cpu', run "
                f"'systemctl daemon-reload', and log in again. That drop-in reaches this "
                f"one user; the same line on user@.service grants it to every user."
            )
        if not self._controllers.cpu:
            # cpu is routinely absent: stock Ubuntu ships 'Delegate=pids memory' in
            # user@.service, so requiring it would refuse to run there. memory and pids
            # -- the out-of-memory and fork-bomb bounds -- are enforced; cpu fairness is
            # the one axis this host cannot enforce. apply() already writes cpu limits
            # only where the controller is present, so the tree stays correct without
            # cpu; it just cannot cap cpu. Warn loudly rather than skip in silence.
            logging.warning(
                "resource tree: host delegated %s but not 'cpu'; this run enforces "
                "memory and pids but CANNOT cap cpu. Add 'Delegate=pids memory cpu' to "
                "the systemd user manager to bound cpu too.",
                self._delegated_names(),
            )

        framework = self._root / "framework"
        framework.mkdir(exist_ok=True)
        # Empty the root BEFORE enabling subtree_control: the kernel refuses to
        # enable a controller for the children of a cgroup that still holds
        # processes -- and the orchestrator is not alone in there.
        _drain_into(self._root, framework)
        _enable_subtree_controllers(self._root, self._controllers)

        self.apply(self._root, run_limits)
        # The framework leaf holds processes, so it never enables subtree_control,
        # and it never carries oom.group -- killing the orchestrator as a group
        # would kill the run itself. Its pid budget is RESERVED: because
        # framework + product execution scopes <= run, a WorkUnit that forks without bound exhausts
        # its own budget and stops, and can never leave the orchestrator unable to
        # fork. Measured before that reservation existed: a bomb in one WorkUnit took
        # 63 of the run's 64 processes and the orchestrator's next fork() got
        # EAGAIN -- it could no longer even kill the thing eating it.
        self.apply(framework, framework_limits)
        self._framework = framework
        logging.info(
            "resource tree: root=%s controllers=%s framework=%s",
            self._root,
            self._delegated_names(),
            framework,
        )
        return framework

    def inner(self, parent: Path, name: str, limits: Limits) -> Path:
        """Create a Scope that has child Scopes or process leaves and holds no process itself.

        cgroup v2 forbids a cgroup from holding processes AND enabling controllers for its children, so the two kinds of cgroup are genuinely different, and the code says which one it is building.

        ``oom.group`` is forced off here, whatever the caller passed. An inner cgroup has children, so its child leaves own atomic process-group death.
        """
        cgroup_dir = parent / name
        cgroup_dir.mkdir(exist_ok=True)
        if self._enforce:
            _enable_subtree_controllers(cgroup_dir, self._controllers)
            self.apply(cgroup_dir, replace(limits, oom_group=False))
        return cgroup_dir

    def leaf(self, parent: Path, name: str, limits: Limits) -> Path:
        """Create a cgroup that holds processes and has no children."""
        cgroup_dir = parent / name
        cgroup_dir.mkdir(exist_ok=True)
        if self._enforce:
            self.apply(cgroup_dir, limits)
        return cgroup_dir

    def apply(self, cgroup_dir: Path, limits: Limits) -> None:
        """Write one cgroup's limits, verifying every one of them.

        Which interface files exist here is decided by this cgroup's PARENT, not by the delegation root: a controller's files appear in a cgroup only if its parent listed that controller in ``cgroup.subtree_control``. So the available set is read from THIS cgroup's ``cgroup.controllers`` -- reading the root's would write ``cpu.weight`` into a cgroup whose parent never enabled cpu, and get EACCES.

        memory and pids are required and were checked at bootstrap; cpu may be absent (bootstrap warns), so its limits are written here only where the controller is present.
        """
        here = Controllers.read(cgroup_dir)

        if limits.memory_max_bytes is not None:
            _write_verify(cgroup_dir, "memory.max", str(limits.memory_max_bytes))
        # Swap is never a substitute for RAM here: a training process that swaps
        # is a training process that has already failed, just slowly.
        _write_verify(cgroup_dir, "memory.swap.max", "0")
        _write_verify(cgroup_dir, "memory.oom.group", "1" if limits.oom_group else "0")

        if limits.pids_max is not None:
            _write_verify(cgroup_dir, "pids.max", str(limits.pids_max))

        if here.cpu:
            _write_verify(cgroup_dir, "cpu.weight", str(limits.cpu_weight))
            if limits.cpu_max is not None:
                _write_verify(cgroup_dir, "cpu.max", limits.cpu_max)

    def _delegated_names(self) -> str:
        names = [n for n, on in (
            ("memory", self._controllers.memory),
            ("pids", self._controllers.pids),
            ("cpu", self._controllers.cpu),
        ) if on]
        return ", ".join(names) if names else "nothing"


def enter_cgroup_preamble(leaf_dir: Path) -> str:
    """Shell that puts the process into ``leaf_dir`` and then keeps running.

    A process joins a cgroup by writing its own pid to that cgroup's ``cgroup.procs``; it is inherited across fork and exec, so doing it in the launcher's shell -- before ``exec``ing the real payload -- means the payload is inside its leaf from its very first instruction, with no window in which it runs unbounded.

    NOT ``preexec_fn``: the product launches through ``asyncio.create_subprocess_exec`` from a multi-threaded parent, where ``preexec_fn`` is documented as deadlock-prone. The shell preamble is a path the product already has, on every launch route.

    The write is asserted, not best-effort. A payload that silently failed to enter its cgroup would run against the framework's limits instead of its own, and nothing downstream could tell.
    """
    return (
        f'echo $$ > "{leaf_dir}/cgroup.procs" || {{\n'
        f'  echo "aibuildai: could not join cgroup {leaf_dir}" >&2\n'
        f"  exit 1\n"
        f"}}"
    )


def oom_kill_count(cgroup_dir: Path) -> int:
    """How many times the kernel OOM-killed something in this cgroup.

    The kernel keeps this for free, per cgroup, with no polling window.

    Returns 0 for a cgroup that has been reaped -- a kill this process never observed is a kill that did not happen to a unit it is still tracking.
    """
    try:
        events = (cgroup_dir / "memory.events").read_text()
    except OSError:
        return 0
    for line in events.splitlines():
        key, _, value = line.partition(" ")
        if key == "oom_kill":
            return int(value)
    return 0


def kill_tree(cgroup_dir: Path) -> None:
    """Kill every process in this cgroup and everything below it, atomically.

    ``cgroup.kill`` is race-free against concurrent forks: a child always joins its parent's cgroup, and ``cgroup_post_fork()`` checks the ``CGRP_KILL`` flag before the child is ever visible to userspace. A process that forks in the middle of the kill dies with it. ``setsid()`` does not help it -- cgroup membership is not a session or a process group.

    Every "enumerate cgroup.procs and SIGKILL each pid" loop has a fork race that this one file closes.

    Kubernetes start mode is the one place that loop is accepted anyway: with no cgroup2 filesystem there is nothing atomic to write, so the fallback walks the recorded pids and their /proc descendants. Its fork race is real and stays open; the Pod's own death at run end is the backstop that reaps what the race misses.
    """
    from infra.host_resource.cgroup import kubernetes_start_mode
    if kubernetes_start_mode():
        _kill_tree_by_proc_walk(cgroup_dir)
        return
    try:
        (cgroup_dir / "cgroup.kill").write_text("1")
    except OSError as exc:
        raise CgroupWriteRejected(
            f"could not kill cgroup subtree {cgroup_dir}: {exc}"
        ) from exc


def _kill_tree_by_proc_walk(scope_dir: Path) -> None:
    """SIGKILL every pid recorded under this scope directory, then every live /proc descendant of those pids."""
    recorded: set[int] = set()
    for procs_file in scope_dir.rglob("cgroup.procs"):
        try:
            recorded.update(int(p) for p in procs_file.read_text().split())
        except (OSError, ValueError):
            continue
    if not recorded:
        return
    children: dict[int, list[int]] = {}
    for stat in Path("/proc").glob("[0-9]*/stat"):
        try:
            fields = stat.read_text().rsplit(")", 1)[1].split()
            children.setdefault(int(fields[1]), []).append(int(stat.parent.name))
        except (OSError, IndexError, ValueError):
            continue
    doomed = set(recorded)
    frontier = list(recorded)
    while frontier:
        for child in children.get(frontier.pop(), []):
            if child not in doomed:
                doomed.add(child)
                frontier.append(child)
    for pid in doomed:
        try:
            os.kill(pid, 9)
        except OSError:
            continue


def kill_tree_best_effort(cgroup_dir: Path) -> None:
    """Try to kill a subtree during emergency teardown without aborting later reaps."""
    try:
        kill_tree(cgroup_dir)
    except CgroupWriteRejected as exc:
        logging.warning("resource tree: %s", exc)

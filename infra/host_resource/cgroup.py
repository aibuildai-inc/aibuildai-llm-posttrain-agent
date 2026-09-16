"""What is left of systemd once the kernel owns the limits.

systemd's only job now is to hand the product ONE delegated cgroup subtree and then get out of the way. Everything inside that subtree -- the Run ceiling, each durable execution level, and each WorkUnit -- is written directly to cgroupfs by ``tree.py``, because a delegated subtree needs no privilege to manage and because systemd cannot be trusted to report whether a limit took: it downgrades the ENOENT from a missing controller to a DEBUG log and returns success, which is how this product's CPU limits were dead for three months without one error.

So this module keeps exactly three things:

* the check that a systemd user manager exists at all, and an actionable message when it does not (the run refuses to start -- resource governance is load-bearing);
* the name of the run's transient service;
* ``make_killable_preamble()``, which is the antidote to an OOM livelock and the single most dangerous line in the codebase to delete.

Everything else that used to live here -- two shared slices, a transient scope per subprocess, per-scope ``--property=`` caps, unit-name sequencing, an orphan-slice sweep -- is gone. A subprocess no longer needs a scope of its own: it is spawned by the orchestrator and writes its own pid into its WorkUnit's cgroup before it execs, so it is inside its limits from its first instruction.
"""
from __future__ import annotations

import os
from pathlib import Path


class HostCapabilityUnavailable(RuntimeError):
    """A host capability this run REQUIRES, which this host does not provide.

    Fail closed, always: every one of these makes the run unbounded or unkillable, and an unbounded run can take the host down. cli reports it as a user ENVIRONMENT condition -- an actionable message and a clean EX_UNAVAILABLE exit -- never as a crash.
    """


class WorkerCgroupUnavailable(HostCapabilityUnavailable):
    """systemd-run / the user systemd manager is not usable, so this run cannot be placed in a delegated cgroup service."""


class ProcessNotKillable(HostCapabilityUnavailable):
    """``/proc/self/oom_score_adj`` cannot be written, so this process -- and every process it forks -- stays OOM-INELIGIBLE.

    Nothing to do with systemd or cgroups: it is /proc, it needs no delegation, and a host can have flawless cgroup delegation and still refuse this write. But the consequence is the same shape and worse: a memory ceiling over a cgroup with no eligible victim does not kill, it LIVELOCKS.
    """


def kubernetes_start_mode() -> bool:
    """True inside a Kubernetes Pod: the kubelet sets ``KUBERNETES_SERVICE_HOST`` in every container, and nothing else does.

    In this mode the run starts WITHOUT the delegated systemd service and writes no kernel limit. An unprivileged Pod exposes no cgroup2 filesystem at all -- measured on a Kubernetes cluster, ``/sys/fs/cgroup`` inside the container is a plain directory -- so the delegated service can never exist there, and the Pod's own cgroup is the resource fence: the cluster bounds the run's memory, pids, and cpu at the Pod boundary. Per-WorkUnit kernel caps are the one thing given up, and bootstrap records that as a ``RunWarning`` instead of hiding it.
    """
    return bool(os.environ.get("KUBERNETES_SERVICE_HOST"))


def run_service_name(pid: int, starttime: int) -> str:
    """Name one transient service request without colliding with another: the name embeds the invoking process instance so two commands cannot collide before systemd starts their real main processes.

    Only a command with no Run ID (memorize) uses this. A run-owning command uses ``run_unit_name`` instead, so the unit name IS the single-writer boundary for that run."""
    return f"aibuildai-{pid}_{starttime}.service"


def run_unit_name(run_id: str) -> str:
    """The one deterministic transient user unit that owns run ``run_id``'s process.

    One name per Run ID, so systemd itself refuses a second process for the same active run: starting a unit whose name already exists fails at the supervisor boundary. This replaces the deleted run-directory ``flock``."""
    return f"aibuildai-run-{run_id}"


def own_starttime() -> int:
    """This process's start-time (clock ticks since boot). Raised, never None: a process can always read its own stat, so a None here is a real environment fault, not an expected absence."""
    # Deferred: infra.process.process_lifecycle imports this module to name and
    # delegate the run's service, so a module-top import of anything under
    # infra.process would close a real runtime cycle through infra/process/__init__.
    from infra.process.proc_query import proc_starttime
    st = proc_starttime(os.getpid())
    if st is None:
        raise AssertionError("cannot read this process's own /proc stat start-time")
    return st


def safe_tag(unit_tag: str) -> str:
    """A tag reduced to characters legal in a cgroup directory name."""
    return "".join(c if c.isalnum() or c in "-_" else "_" for c in unit_tag)


def _is_wsl() -> bool:
    """True on WSL2, detected via the interop env vars or ``/proc/version``. WSL2 is the one non-reference environment with a specific, high-value fix, so it is detected and given targeted guidance; every other host gets the generic-Linux and container alternatives."""
    if os.environ.get("WSL_DISTRO_NAME") or os.environ.get("WSL_INTEROP"):
        return True
    try:
        return "microsoft" in Path("/proc/version").read_text().lower()
    except OSError:
        return False


def systemd_unavailable_guidance() -> str:
    """Actionable, environment-appropriate guidance appended to every ``WorkerCgroupUnavailable`` message.

    aibuildai runs inside a delegated systemd service so the kernel enforces the run's memory and process limits, so a reachable systemd user manager is required and the run refuses to start without one. This states that requirement and how to satisfy it per environment.
    """
    lines = [
        "aibuildai runs inside a delegated systemd service so the kernel "
        "enforces this run's memory and process limits, so it requires a reachable "
        "systemd user manager and will not start without one. To provide one:",
    ]
    if _is_wsl():
        lines += [
            "  - WSL2: enable systemd. Add to /etc/wsl.conf:",
            "        [boot]",
            "        systemd=true",
            "    then run `wsl --shutdown` in Windows and reopen this distro.",
        ]
    else:
        lines += [
            "  - Linux: log in to a normal user session (systemd starts a user "
            "manager per session), or enable a persistent one with "
            "`sudo loginctl enable-linger $USER` and open a new login shell.",
            "  - Container / minimal image: a systemd user manager is required; "
            "run under an init that starts `systemd --user`, or use a base image "
            "with systemd enabled.",
        ]
    return "\n".join(lines)


def make_killable_preamble() -> str:
    """The one line that keeps a memory limit from hanging the machine.

    Every payload the product launches can inherit ``oom_score_adj = -1000`` from the ssh or VS Code Remote session that started the orchestrator. aibuildai never sets it; it is inherited. At -1000 the kernel's memcg OOM killer, on finding a cgroup over ``memory.max``, has NO eligible victim -- so instead of killing, it LIVELOCKS: it re-reports the OOM forever, pins single-threaded journald at 100% CPU, and takes the shared host down with it.

    ``echo 0 > /proc/self/oom_score_adj`` raises the inherited value back to 0. Raising toward 0 needs no capability, writes ``/proc/self`` so it needs no cgroup delegation, and is inherited across fork and exec -- so one line at the top of the launcher makes the payload killable on every launch path there is.

    Reproduced on this host while designing the tree: without it, a 150 MB process in a 100 MB cgroup hangs for 60 seconds and neither dies nor progresses. With it, the kernel kills it immediately. ``memory.oom.group`` does not save you -- killing a group still requires one killable member.

    An implementation that copies the tree and drops this line hangs the machine.

    The write is ASSERTED, never best-effort. It used to end in ``|| true``, which is the one thing this line must not do: a host where ``/proc/self/oom_score_adj`` is not writable is exactly the host where the payload stays OOM-immune, the memcg killer finds no victim, and the machine livelocks -- and it would have done so with no error anywhere. A launch that cannot make its payload killable must not start.

    What the assertion must test is the VALUE, not the write. The kernel refuses the write whenever 0 is below ``signal->oom_score_adj_min``, a floor a privileged writer set -- and a systemd user manager started with a POSITIVE ``OOMScoreAdjust`` propagates a positive floor to every service beneath it, this launcher included. A payload already at >= 0 is eligible for the OOM killer, which is the entire property asserted here: the ceiling has a victim and nothing can livelock. Failing the launch there rejects a host that is behaving correctly. So a refused write falls through to reading the value back, and only a negative (or unreadable) one aborts -- fail-closed on the case that actually hangs the machine.

    Emits no single quotes, so it embeds inside an outer ``bash -c '...'``.
    """
    return (
        'echo 0 > /proc/self/oom_score_adj 2>/dev/null || {\n'
        '  oom_cur=$(cat /proc/self/oom_score_adj 2>/dev/null)\n'
        '  case ${oom_cur:-unreadable} in\n'
        '    [0-9]*) ;;\n'
        '    *) echo "aibuildai: cannot reset oom_score_adj (it is '
        '${oom_cur:-unreadable}); this payload would be OOM-immune and would '
        'livelock the host instead of being killed" >&2\n'
        '       exit 1 ;;\n'
        '  esac\n'
        '}'
    )

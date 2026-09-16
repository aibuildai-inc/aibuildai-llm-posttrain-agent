"""HostResourceController — the one owner of this run's host resources.

It holds three things and nothing else:

* the **resource tree** (``tree.py``), the cgroup hierarchy the kernel enforces;
* the private **plan** (``plan.py``), which resolves the run's limits;
* the private GPU selector and the free-space reading (``disk_space.py``), the two resources cgroup cannot express.

What it no longer holds is the interesting part. There were two background threads polling process RSS and directory sizes and SIGKILLing whatever looked too big, a global singleton so deep call sites could ask them who they had killed, and an admission gate that re-summed every running unit's declared memory before each launch. All of it existed to do, in Python, three things the kernel already does: bound the sum of a subtree, attribute a kill to a cgroup, and kill a subtree atomically. They are gone, and nothing replaced them.
"""

from __future__ import annotations

import logging
import os
from collections.abc import Callable
from decimal import Decimal
from pathlib import Path
from typing import TYPE_CHECKING

from infra.host_resource.cgroup import kubernetes_start_mode, safe_tag
from infra.host_resource.disk_space import free_bytes
from infra.host_resource.gpu_alloc import GpuSelector
from infra.host_resource.plan import GIB, _ResourcePlan
from infra.host_resource.tree import Limits
from infra.host_resource.tree import ResourceTree, kill_tree, kill_tree_best_effort

if TYPE_CHECKING:
    from config import ResourcesConfig

_CPU_PERIOD_US = 100_000


class HostResourceController:
    """Construct once in bootstrap; ``acquire()`` at startup, ``release()`` at shutdown."""

    def __init__(
        self,
        resources: "ResourcesConfig",
    ) -> None:
        self._resources = resources
        self._gpu = GpuSelector(
            gpu_busy_util_threshold=resources.gpu.busy_util_threshold,
            min_free_mib=resources.gpu.min_free_mib,
            util_samples=resources.gpu.util_samples,
            visible_devices=resources.cuda_visible_devices,
        )
        self._tree: ResourceTree | None = None
        self._plan: _ResourcePlan | None = None

    def _require_tree(self) -> ResourceTree:
        if self._tree is None:
            raise AssertionError("HostResourceController.acquire() has not run")
        return self._tree

    def _require_plan(self) -> _ResourcePlan:
        if self._plan is None:
            raise AssertionError("HostResourceController.acquire() has not run")
        return self._plan

    def acquire(self, *, playground_root: Path) -> None:
        """Resolve this run's declared limits, build the tree, and move the orchestrator into its own leaf. Call once, before any work can start.

        Each execution writes its own sparse local ceilings when it claims its level; this builds only the run parent ceiling and the framework leaf.
        """
        res = self._resources
        self._plan = _ResourcePlan.build(
            run_memory_bytes=(
                res.run.memory_max_gb * GIB
                if res.run.memory_max_gb is not None
                else None
            ),
            run_cpu_max=(_cpu_max_string(res.run.cpu_max_cores)),
        )
        if kubernetes_start_mode():
            # The Pod bounds the run; the tree keeps its shape as
            # plain directories so every launch path still self-enrolls, and the
            # recorded pids are what kill_tree's /proc walk reaps.
            self._tree = ResourceTree(
                playground_root / f".scopes-{os.getpid()}", enforce=False
            )
        else:
            self._tree = ResourceTree()
        self._tree.bootstrap(
            run_limits=self._plan.run_limits(),
            framework_limits=self._plan.framework_limits(),
        )
        logging.info("%s", self._plan.describe())

    async def select_gpus(
        self,
        count: int,
        remaining_s: Callable[[], float],
        *,
        candidates: tuple[int, ...] | None = None,
    ) -> tuple[int, ...]:
        """Choose ``count`` physical cards for one Action about to be recorded.

        Nothing is reserved: the answer is a placement, and a later Action may
        legally be placed on the same card. ``candidates`` defaults to the
        whole run universe; a caller narrows it only as its own scheduling
        policy, and a narrowed pool is still checked against the run's own
        cards, because choosing among fewer must never reach more. An empty
        pool offers nothing and cannot answer a positive count, so it fails
        instead of quietly becoming CPU work."""
        what = f"a {count}-card selection"
        # The host's own answer is read BEFORE the caller's pool: a caller
        # that offers no candidate is making a scheduling choice, not
        # reporting that the machine has no cards, and the two must not end
        # in the same silent CPU placement.
        universe = self._run_cards(what)
        if universe is None:
            return ()
        pool = universe if candidates is None else self.placed_gpus(
            tuple(candidates), what=what
        )
        return await self._gpu.choose(count, pool, remaining_s)

    def _run_cards(self, what: str) -> tuple[int, ...] | None:
        """This run's cards, or None where the host has none to give.

        The one place that answers "can anything be placed at all". A host
        with no reachable card and no configured device list places the work
        un-pinned, exactly as it ran before cards were selected at all, and
        both doors into placement read that from here rather than each
        keeping its own copy of the rule."""
        universe = self._gpu.universe
        if not universe and self._resources.cuda_visible_devices is None:
            logging.info("no GPU visible; placing %s un-pinned", what)
            return None
        return universe

    def check_within_run(
        self,
        *,
        cpu_max_cores: float | None,
        memory_max_gb: float | None,
        what: str,
    ) -> None:
        """Refuse one Action that alone declares more than the whole Run may use.

        The run root is the one aggregate authority, so several Actions may
        declare more than it in total and the kernel throttles them; that is
        deliberate and is not refused here. A SINGLE Action above the run
        ceiling is different: no placement could ever satisfy it, so the call
        that starts it is told now instead of leaving an unplaceable level to
        be discovered later."""
        run = self._resources.run
        if (
            run.cpu_max_cores is not None
            and cpu_max_cores is not None
            and cpu_max_cores > run.cpu_max_cores
        ):
            raise ValueError(
                f"{what} declares {cpu_max_cores} CPU cores, which is more "
                f"than the whole run may use ({run.cpu_max_cores})"
            )
        if (
            run.memory_max_gb is not None
            and memory_max_gb is not None
            and memory_max_gb > run.memory_max_gb
        ):
            raise ValueError(
                f"{what} declares {memory_max_gb} GB of memory, which is more "
                f"than the whole run may use ({run.memory_max_gb})"
            )

    def placed_gpus(self, indices: tuple[int, ...], *, what: str) -> tuple[int, ...]:
        """Check exact cards against the same Run boundary the selector picks from.

        Naming cards skips the placement heuristic, which is the point of
        naming them. It does not skip the operator's device list: a card this
        run may not use is refused here, before any Action records it. On a
        host with no reachable card and no configured list the answer is the
        one the counted path already gives -- un-pinned, on the CPU."""
        universe = self._run_cards(what)
        if universe is None:
            return ()
        outside = [index for index in indices if index not in universe]
        if outside:
            raise ValueError(
                f"{what} names GPU(s) {outside} this run may not use; its "
                f"cards are {list(universe)}"
            )
        return indices

    def gpu_universe(self) -> tuple[int, ...]:
        """Every physical card this run may place work on."""
        return self._gpu.universe

    def host_visible_gpu_count(self) -> int:
        """Return how many cards Clock-bounded startup measured."""
        return self._gpu.host_visible_gpu_count()

    async def probe_host_visible_gpu_count(
        self, remaining_s: Callable[[], float]
    ) -> int:
        """Measure the visible cards within the live Run Budget."""
        return await self._gpu.probe_host_visible_gpu_count(remaining_s)

    def disk_free_bytes(self, path: Path) -> int:
        """Return free bytes on the filesystem that holds this path."""
        return free_bytes(path)

    def cpu_limits_available(self) -> bool:
        """Return whether this resource tree can enforce CPU limits."""
        return self._require_tree().controllers.cpu

    def claim_level(self, path: str) -> None:
        """Establish one Search's level in the resource tree when it starts.

        Every Search claims its own durable-path level, so a descendant placed below it always finds its parent directory. The level carries NO limit of its own. There are two physical authorities and no third: the run root bounds the whole run, and each WorkUnit Action bounds its own session and commands. A number written here would bound the whole subtree below it, which is the ancestor narrowing this product removed at the declaration layer -- keeping it in the kernel would leave the same rule enforced where nobody declared it. Idempotent, so a resumed process claims the same level again without effect.

        Not every segment of the path claims a level of its own. A Composite is a container that runs no process and asks for nothing, so it never claims one, and a Search under a Composite would otherwise find a hole where its parent should be. The missing segments are created here as plain structure carrying no limit, which is what they are: the kernel hierarchy mirrors the durable path, and a level that declares nothing bounds nothing.

        Claim has no matching release on purpose. The level is an empty directory once its work ends, so keeping it costs nothing, while releasing it on completion would race late descendants (a child cancelled mid-placement, a resumed replay re-walking the path). Run teardown removes the whole tree at once."""
        parts = path.split("/")
        tree = self._require_tree()
        parent = tree.root
        for segment in parts[:-1]:
            parent = tree.inner(parent, safe_tag(segment), _level_limits())
        tree.inner(parent, safe_tag(parts[-1]), _level_limits())

    def framework_subprocess_cgroup(self, unit_tag: str) -> Path:
        """The cgroup for one subprocess tree the orchestrator owns outside a WorkUnit, such as a score program or local MCP server.

        A leaf directly under the run ceiling, named after the unit tag the product already uses for it, so a reader of the cgroup tree maps it back onto the product with no translation step.

        These had no cgroup at all: as plain children of the orchestrator they inherited ``framework/`` and shared its ceiling with the orchestrator itself. On any host up to 64 GB that is 2.0 GB, for code the task itself wrote.
        """
        tree = self._require_tree()
        return tree.leaf(
            tree.root,
            safe_tag(unit_tag),
            self._require_plan().framework_subprocess_limits(),
        )

    def place_work_unit(
        self,
        path: str,
        kind: str,
        *,
        memory_max_bytes: int | None,
        cpu_max_millicores: int | None,
    ) -> tuple[Path, Path | None]:
        """Build one WorkUnit Scope below its durable parent.

        A Search claims its own level with its declared ceilings when it starts (``claim_level``). Every other intermediate level -- a Composite's -- is a plain directory mirroring the durable path, made here on first use and carrying no limit of its own, because a Composite owns no host resource."""
        parts = path.split("/")
        tree = self._require_tree()
        parent = tree.root
        for segment in parts[:-1]:
            child = parent / safe_tag(segment)
            if not child.is_dir():
                child = tree.inner(
                    parent,
                    safe_tag(segment),
                    Limits(memory_max_bytes=None, oom_group=False),
                )
            parent = child
        limits = Limits(
            memory_max_bytes=memory_max_bytes,
            oom_group=False,
            cpu_max=(
                None
                if cpu_max_millicores is None
                else f"{cpu_max_millicores * _CPU_PERIOD_US // 1000} {_CPU_PERIOD_US}"
            ),
        )
        scope = tree.inner(parent, safe_tag(parts[-1]), limits)
        process = tree.leaf(
            scope,
            "session",
            Limits(memory_max_bytes=None, oom_group=True),
        )
        commands = (
            tree.inner(
                scope,
                "commands",
                Limits(memory_max_bytes=None, oom_group=False),
            )
            if kind == "llm_agent"
            else None
        )
        return process, commands

    def release_work_unit(self, path: str) -> None:
        """Atomically kill exactly one WorkUnit and every process it started."""
        cgroup = self._require_tree().root.joinpath(
            *(safe_tag(part) for part in path.split("/"))
        )
        kill_tree(cgroup)

    def release(self) -> None:
        """Kill everything below the run ceiling except the orchestrator itself.

        The framework leaf is spared because this process lives in it. EVERYTHING else goes -- every Composite level, every WorkUnit, every Bash command, and every framework subprocess leaf. Enumerated by "every child that is not framework/" rather than by a name glob, because a glob only kills the shapes its author remembered.
        """
        if self._tree is None:
            return
        for child in sorted(self._tree.root.iterdir()):
            if child.is_dir() and child.name != "framework":
                kill_tree_best_effort(child)


def _level_limits(
    *,
    memory_max_bytes: int | None = None,
    cpu_max_millicores: int | None = None,
) -> Limits:
    """One execution level's sparse local ceilings as cgroup limits."""
    return Limits(
        memory_max_bytes=memory_max_bytes,
        oom_group=False,
        cpu_max=(
            None
            if cpu_max_millicores is None
            else f"{cpu_max_millicores * _CPU_PERIOD_US // 1000} {_CPU_PERIOD_US}"
        ),
    )


def _cpu_max_string(cores: float | None) -> str | None:
    """Turn an exact core count into cgroup v2's ``cpu.max`` grammar."""
    if cores is None:
        return None
    quota = int(Decimal(str(cores)) * _CPU_PERIOD_US)
    return f"{quota} {_CPU_PERIOD_US}"

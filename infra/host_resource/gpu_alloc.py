"""Choosing physical GPUs, and nothing else.

Cards are not owned in this product. Two Actions may deliberately share one
card, so there is no claim, no reservation, no release, and no waiting for a
card to become free. What remains is a placement heuristic with a closed
boundary: given how many cards an Action wants, which cards it may have, and
what the host currently looks like, return that many exact physical indices.

``select`` is pure and deterministic for one observation set, so the heuristic
inside it may grow as sophisticated as it likes -- utilisation tiers, free
VRAM, NVLink topology -- without any of that reaching execution semantics. A
caller that wants a different pool passes a narrower candidate set; that is
local scheduling policy and reserves nothing, so an unrelated Action may still
name a card this one excluded.
"""

from __future__ import annotations

import itertools
import logging
import os
import statistics
from collections.abc import Callable
from typing import NamedTuple

from infra.host_resource.nvml import (
    GpuStats,
    NvmlQueryError,
    query_gpu_stats,
    query_topology,
)

_logger = logging.getLogger(__name__)


class GpuObservations(NamedTuple):
    """What the host looked like when one selection was made.

    Optional by construction: a host with no reachable NVML yields empty
    stats, and selection then falls back to the candidate order rather than
    to a second recovery subsystem."""

    stats: tuple[GpuStats, ...] = ()
    topology: dict[frozenset[int], int] | None = None


def configure_visible_devices(
    allowed: tuple[int, ...] | None,
) -> None:
    """Check the declaration and set the GPUs visible to this run process."""
    external = os.environ.get("CUDA_VISIBLE_DEVICES")
    if external is not None:
        raise ValueError(
            f"CUDA_VISIBLE_DEVICES={external!r} is set in the environment. "
            "aibuildai does not read it: declare the run's GPUs in "
            "resources.cuda_visible_devices and unset the env var."
        )
    if allowed is None:
        return
    os.environ["CUDA_VISIBLE_DEVICES"] = ",".join(map(str, allowed))
    os.environ["CUDA_DEVICE_ORDER"] = "PCI_BUS_ID"


def _util_sort_key(stats: GpuStats) -> tuple[bool, int, int]:
    return stats.util_pct is None, stats.util_pct or 0, -stats.free_mib


def _launch_ready_tiers(
    stats: list[GpuStats],
    *,
    min_free_mib: int,
    busy_util_pct: int,
) -> tuple[list[GpuStats], list[GpuStats], list[GpuStats]]:
    ordered = sorted(
        (item for item in stats if item.free_mib >= min_free_mib),
        key=_util_sort_key,
    )
    idle, busy, unknown = [], [], []
    for item in ordered:
        if item.util_pct is None:
            unknown.append(item)
        elif item.util_pct < busy_util_pct:
            idle.append(item)
        else:
            busy.append(item)
    return idle, busy, unknown


def _select_connected_subset(
    candidates: list[GpuStats],
    k: int,
    topology: dict[frozenset[int], int] | None,
) -> list[GpuStats]:
    """Prefer the subset with the best weakest link, then aggregate link rank."""
    if topology is None or k <= 1 or len(candidates) <= k:
        return candidates[:k]
    position = {item.index: index for index, item in enumerate(candidates)}

    def score(subset: tuple[GpuStats, ...]) -> tuple:
        ranks = [
            topology.get(frozenset((left.index, right.index)), 0)
            for left, right in itertools.combinations(subset, 2)
        ]
        order = tuple(sorted(-position[item.index] for item in subset))
        return min(ranks), sum(ranks), order

    best = max(itertools.combinations(candidates, k), key=score)
    return sorted(best, key=lambda item: position[item.index])


def select(
    count: int,
    candidates: tuple[int, ...],
    observations: GpuObservations,
    *,
    min_free_mib: int = 1024,
    busy_util_pct: int = 50,
) -> tuple[int, ...]:
    """Choose ``count`` distinct physical cards from ``candidates``.

    The whole contract. It returns an exact tuple and is done: it records
    nothing, reserves nothing, and knows nothing about the Action asking. Two
    concurrent selections may return the same card, which is not a failure
    because sharing is legal."""
    if count <= 0:
        return ()
    # Distinct cards is the whole promise, so the offer is counted as a SET.
    # A pool that names one card twice offers one card: satisfying a two-card
    # request from it would return the same index twice, which is one card
    # wearing two hats rather than the two the caller asked for. Sharing stays
    # legal -- another Action may hold this card -- but not with itself.
    ordered = tuple(dict.fromkeys(candidates))
    if count > len(ordered):
        raise ValueError(
            f"cannot place {count} distinct GPU(s) on the {len(ordered)} "
            f"card(s) offered: {list(candidates)}"
        )
    candidates = ordered
    allowed = set(candidates)
    stats = [item for item in observations.stats if item.index in allowed]
    if len(stats) < count:
        # No telemetry, or not enough of it to rank by. The candidate order is
        # the deterministic answer rather than a second recovery path.
        return tuple(sorted(candidates)[:count])
    idle, busy, unknown = _launch_ready_tiers(
        stats, min_free_mib=min_free_mib, busy_util_pct=busy_util_pct
    )
    ranked = idle + busy + unknown
    if len(ranked) < count:
        # Every card is short on free VRAM. Sharing is legal, so fill from the
        # rest of the candidate set by the same order rather than refusing.
        picked = {item.index for item in ranked}
        ranked = ranked + sorted(
            (item for item in stats if item.index not in picked),
            key=_util_sort_key,
        )
    picks = _select_connected_subset(ranked, count, observations.topology)
    return tuple(item.index for item in picks[:count])


class GpuSelector:
    """Sample the host, then choose cards for one Action."""

    def __init__(
        self,
        *,
        gpu_busy_util_threshold: int,
        min_free_mib: int,
        util_samples: int = 1,
        visible_devices: tuple[int, ...] | None = None,
    ):
        self._visible = visible_devices
        self._min_free_mib = min_free_mib
        self._busy_util_pct = gpu_busy_util_threshold
        self._util_samples = util_samples
        self._topology_probed = False
        self._topology: dict[frozenset[int], int] | None = None
        # The run's whole GPU universe, measured once at startup. Its size is
        # the visible count, so there is no second number to keep in step.
        self._universe: tuple[int, ...] | None = None

    async def _query_once_for_run(
        self,
        remaining_s: Callable[[], float],
    ) -> list[GpuStats]:
        error: NvmlQueryError | None = None
        for _ in range(2):
            try:
                stats = await query_gpu_stats(remaining_s())
            except NvmlQueryError as exc:
                error = exc
                continue
            if stats:
                return stats
        if error is not None:
            _logger.warning("NVML GPU stats failed after 2 attempts: %s", error)
        return []

    async def _query_stats_for_run(
        self,
        remaining_s: Callable[[], float],
        *,
        util_samples: int,
    ) -> list[GpuStats]:
        base = await self._query_once_for_run(remaining_s)
        if util_samples <= 1 or not base:
            return base
        values = {
            item.index: [] if item.util_pct is None else [item.util_pct]
            for item in base
        }
        for _ in range(util_samples - 1):
            for item in await self._query_once_for_run(remaining_s):
                if item.index in values and item.util_pct is not None:
                    values[item.index].append(item.util_pct)
        return [
            item._replace(
                util_pct=(
                    int(statistics.median(values[item.index]))
                    if values[item.index]
                    else None
                )
            )
            for item in base
        ]

    async def _query_topology_for_run(
        self, remaining_s: Callable[[], float]
    ) -> dict[frozenset[int], int] | None:
        if not self._topology_probed:
            self._topology_probed = True
            try:
                self._topology = await query_topology(remaining_s())
            except NvmlQueryError as exc:
                _logger.warning("NVML topology query failed: %s", exc)
        return self._topology

    async def probe_host_visible_gpu_count(
        self,
        remaining_s: Callable[[], float],
    ) -> int:
        if self._universe is not None:
            return len(self._universe)
        if self._visible == ():
            self._universe = ()
            return 0
        stats = await self._query_stats_for_run(remaining_s, util_samples=1)
        present = {item.index for item in stats}
        if self._visible is not None:
            missing = [index for index in self._visible if index not in present]
            if missing:
                raise ValueError(
                    "resources.cuda_visible_devices names cards NVML does not "
                    f"report: {missing}; this host has {sorted(present)}"
                )
            present.intersection_update(self._visible)
        self._universe = tuple(sorted(present))
        return len(self._universe)

    def host_visible_gpu_count(self) -> int:
        return len(self.universe)

    @property
    def universe(self) -> tuple[int, ...]:
        """Every physical card this run may place work on."""
        if self._universe is None:
            raise AssertionError("startup has not measured the visible GPUs")
        return self._universe

    async def choose(
        self,
        count: int,
        candidates: tuple[int, ...],
        remaining_s: Callable[[], float],
    ) -> tuple[int, ...]:
        """Sample the host once, then select from ``candidates``."""
        if count <= 0:
            return ()
        stats = await self._query_stats_for_run(
            remaining_s, util_samples=self._util_samples
        )
        topology = (
            await self._query_topology_for_run(remaining_s) if count >= 2 else None
        )
        picks = select(
            count,
            candidates,
            GpuObservations(stats=tuple(stats), topology=topology),
            min_free_mib=self._min_free_mib,
            busy_util_pct=self._busy_util_pct,
        )
        _logger.info(
            "placed %d GPU(s) on %s of %s", count, list(picks), list(candidates)
        )
        return picks

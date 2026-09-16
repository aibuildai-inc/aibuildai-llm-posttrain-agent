"""Resolve the run-level limits used to build the resource tree.

Each execution level writes its OWN sparse local ceilings when it claims its place in the tree; this plan resolves only the run parent ceiling and the fixed process backstops. No level's limit is sized from a Search-local algorithm parameter.
"""
from __future__ import annotations

import os
from dataclasses import dataclass

from infra.host_resource.tree import DEFAULT_CPU_WEIGHT, RUN_PIDS_MAX, Limits


GIB = 1024 ** 3
FRAMEWORK_PIDS = 1024


def physical_memory_bytes() -> int:
    """Return the host's physical RAM without adding a runtime dependency."""
    return os.sysconf("SC_PAGE_SIZE") * os.sysconf("SC_PHYS_PAGES")


@dataclass(frozen=True)
class _ResourcePlan:
    """The exact limits to write into this run's resource tree."""

    run_memory_bytes: int | None
    run_cpu_max: str | None

    @classmethod
    def build(
        cls,
        *,
        run_memory_bytes: int | None,
        run_cpu_max: str | None,
    ) -> "_ResourcePlan":
        return cls(
            run_memory_bytes=run_memory_bytes,
            run_cpu_max=run_cpu_max,
        )

    def run_limits(self) -> Limits:
        """The optional run total plus the fixed local CPU weight."""
        return Limits(
            memory_max_bytes=self.run_memory_bytes,
            oom_group=False,
            cpu_weight=DEFAULT_CPU_WEIGHT,
            cpu_max=self.run_cpu_max,
            pids_max=RUN_PIDS_MAX,
        )

    def framework_limits(self) -> Limits:
        """Keep process space for the framework without an invented memory share."""
        return Limits(memory_max_bytes=None, oom_group=False, pids_max=FRAMEWORK_PIDS)

    def framework_subprocess_limits(self) -> Limits:
        """A setup grader adds no limit below the optional run total."""
        return Limits(memory_max_bytes=None, oom_group=True)

    def describe(self) -> str:
        run_memory = (
            "none" if self.run_memory_bytes is None else f"{self.run_memory_bytes // GIB}G"
        )
        run_cpu = self.run_cpu_max or "none"
        return f"resource plan: run={run_memory}/{run_cpu}"

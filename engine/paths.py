"""Canonical run directory names and paths.

This file owns the run timestamp format and every path inside one run.
"""

from __future__ import annotations

import os
import re
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

_RUN_TIMESTAMP_RE = re.compile(r"^\d{8}T\d{6}Z_[a-f0-9]{32}$")


@dataclass(frozen=True)
class RunTimestamp:
    """Canonical run directory timestamp. Once constructed, always valid."""

    value: str

    @classmethod
    def new(cls, run_id: str) -> "RunTimestamp":
        """Name a fresh run directory with the run's one global identity.

        The suffix IS the Run ID: the directory, the journal, the DBOS root
        workflow, the systemd unit, and the Web routes all share it, so
        nothing here mints a second identity."""
        ts = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%SZ")
        return cls.parse(f"{ts}_{run_id}")

    @classmethod
    def parse(cls, raw: str) -> "RunTimestamp":
        if not _RUN_TIMESTAMP_RE.match(raw):
            raise ValueError(
                f"not a canonical run directory timestamp: {raw!r} "
                f"(expected YYYYMMDDTHHMMSSZ plus the 32-hex Run ID)"
            )
        return cls(raw)

    @staticmethod
    def is_canonical(name: str) -> bool:
        """Pure predicate: does ``name`` match the run timestamp shape?

        No I/O — just the regex. Used by startup/run_target_resolution to decide whether a path or argument identifies a run.
        """
        return _RUN_TIMESTAMP_RE.match(name) is not None

    @property
    def run_id(self) -> str:
        """The 32-hex Run ID the directory name carries."""
        return self.value.split("_", 1)[1]

    def __str__(self) -> str:
        return self.value


# The run home's children, named once. A logical file reference to a run-owned
# resource carries one of these names, and the properties below resolve the
# same names to paths, so the two can never drift.
WORKSPACE_DIRNAME = "workspace"
PUBLIC_DIRNAME = "public"
PRIVATE_DIRNAME = "private"
DELIVERABLE_DIRNAME = "deliverable"
SUBMITTER_DIRNAME = "submitter"


@dataclass(frozen=True)
class RunPaths:
    """Every canonical path owned by one run home."""

    output_base_dir: Path
    timestamp: RunTimestamp

    @classmethod
    def from_output_base(
        cls,
        output_base_dir: "str | os.PathLike[str]",
        timestamp: RunTimestamp,
    ) -> "RunPaths":
        return cls(Path(output_base_dir), timestamp)

    @property
    def run_home(self) -> Path:
        return self.output_base_dir / str(self.timestamp)

    @property
    def cache_dir(self) -> str:
        """The run-scoped library cache root under ``<run home>/.cache``.

        Run-scoped on purpose: every design and Agent descendant shares one copy
        of the large Hugging Face, Torch, Triton and Numba caches without
        polluting a host-global location across runs."""
        return str(self.run_home / ".cache")

    @property
    def public_dir(self) -> str:
        """Composite-visible data under ``<run home>/public``."""
        return str(self.run_home / PUBLIC_DIRNAME)

    @property
    def private_dir(self) -> str:
        """Manager-only grading data under ``<run home>/private``."""
        return str(self.run_home / PRIVATE_DIRNAME)

    @property
    def score_program_path(self) -> str:
        return f"{self.private_dir}/score.py"

    @property
    def readme_path(self) -> str:
        return f"{self.public_dir}/README.md"

    @property
    def baseline_attempt_dir(self) -> str:
        return f"{self.private_dir}/baseline_attempt"

    @property
    def deliverable_dir(self) -> str:
        return str(self.run_home / DELIVERABLE_DIRNAME)

    @property
    def submitter_state_dir(self) -> str:
        """Submitter-owned run records under ``<run home>/submitter``.

        A run-home sibling of ``workspace/``, ``private/``, and ``deliverable/``, so nothing reaches inside it that was not granted it: the product grants this one resource to the SUBMITTER Action alone. The raw record therefore cannot reach the roles drafting the next composites; sharing goes only through the framework's typed context (``Search.external_scores()``, gated by ``submission.share_external_scores``)."""
        return str(self.run_home / SUBMITTER_DIRNAME)

    @property
    def external_scores_path(self) -> str:
        """The SUBMITTER's append-only external-result record."""
        return f"{self.submitter_state_dir}/external_scores.jsonl"

    @property
    def resource_history_path(self) -> Path:
        return resource_history_path_for(self.run_home)


RESOURCE_HISTORY_FILENAME = "resource_samples.jsonl"


def resource_history_path_for(run_home: "str | os.PathLike[str]") -> Path:
    return Path(run_home) / RESOURCE_HISTORY_FILENAME

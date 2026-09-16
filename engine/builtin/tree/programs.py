"""The built-in training Program."""

from __future__ import annotations

from engine.durable_execution import action

import os
import shutil
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pydantic import ConfigDict

from engine.execution_output import SuccessfulOutput
from engine.failure import Failure, FailureKind
from engine.work_unit.program import Policy, Program


@dataclass(frozen=True)
class TrainingInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    expected_full_training_minutes: int
    source_dir: str
    # The value of DATA_DIR for the task's own run.sh. Machine-consumed
    # business data: the environment this training runs under, not the reason
    # it may read anything.
    data_dir: str


class TrainingOutput(SuccessfulOutput):
    output_dir: str


def _produced_anything(output_dir: Path) -> bool:
    """Whether the training run left any non-empty file under OUTPUT_DIR.

    The postcondition this Program declares: a clean exit that filled nothing
    has not trained anything, whatever its exit code said. It names no
    checkpoint file, because the layout under OUTPUT_DIR belongs to the task
    README and to Score, which reads the DIRECTORY.
    """
    return any(
        path.is_file() and path.stat().st_size > 0 for path in output_dir.rglob("*")
    )


class TrainingProgram(Program[TrainingInput]):
    """Train one composite and return the attempt directory it filled."""

    name: ClassVar[str] = "training"
    policy = Policy(stop_grace_seconds=10.0, require_gpu_use=True)

    @action
    def run(self) -> TrainingOutput | Failure:
        source = Path(self.input.source_dir)
        run_sh = source / "run.sh"
        if not run_sh.is_file():
            return Failure(
                kind=FailureKind.ARTIFACT_MISSING,
                reason=f"run.sh not found at {run_sh}",
            )
        work_dir = Path(self.scratch_dir) / "source"
        if work_dir.exists():
            shutil.rmtree(work_dir)
        # The Coder's workspace carries the framework's own attempt_smoke /
        # attempt_full symlinks (infra.fs.install_attempt_symlinks). This
        # scratch copy is a Program write path, and the executor rejects any
        # symlink under a write path after the run, so the links stay behind:
        # run.sh reads DATA_DIR and OUTPUT_DIR, never those names.
        shutil.copytree(
            source,
            work_dir,
            symlinks=True,
            ignore=shutil.ignore_patterns(
                "attempt_smoke", "attempt_full", "__pycache__", ".pytest_cache"
            ),
        )
        output_dir = Path(self.artifacts_dir) / "full"
        output_dir.mkdir(parents=True, exist_ok=True)
        completed = subprocess.run(
            ["bash", "run.sh"],
            cwd=work_dir,
            env={
                **os.environ,
                "DATA_DIR": self.input.data_dir,
                "OUTPUT_DIR": str(output_dir),
            },
            text=True,
            check=False,
        )
        # A failed Action stays failed. Files the attempt left behind are kept
        # for inspection, but the harness does not read incidental filesystem
        # residue as a successful business result.
        if self.stop_requested:
            return Failure(
                kind=FailureKind.TIMEOUT,
                reason=f"training reached its {self.input.expected_full_training_minutes} min time limit",
            )
        if completed.returncode != 0:
            return Failure(
                kind=FailureKind.CRASH,
                reason=f"training crashed (exited {completed.returncode})",
            )
        if not _produced_anything(output_dir):
            return Failure(
                kind=FailureKind.ARTIFACT_MISSING,
                reason="training exited cleanly but wrote nothing under its output dir",
            )
        return TrainingOutput(output_dir=str(output_dir))

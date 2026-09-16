"""Run one frozen ablation cell to its terminal outcome; decide nothing."""

from __future__ import annotations

from engine.durable_execution import action

import json
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
class CellInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    cell_name: str
    config_json: str
    source_dir: str
    python_path: str
    data_dir: str
    metric_name: str


class CellOutput(SuccessfulOutput):
    cell_name: str
    output_dir: str
    metric: float


class CellProgram(Program[CellInput]):
    """Copy the fixed source, run train.py once, read the metric it wrote."""

    name: ClassVar[str] = "ablation_cell"
    policy = Policy(stop_grace_seconds=10.0)

    @action
    def run(self) -> CellOutput | Failure:
        work_dir = Path(self.scratch_dir) / "source"
        shutil.copytree(self.input.source_dir, work_dir, symlinks=True)
        config_path = work_dir / "config.json"
        config_path.write_text(self.input.config_json, encoding="utf-8")
        output_dir = Path(self.artifacts_dir) / self.input.cell_name
        output_dir.mkdir(parents=True)
        completed = subprocess.run(
            [
                self.input.python_path,
                "train.py",
                "--config",
                str(config_path),
                "--data-dir",
                self.input.data_dir,
                "--output-dir",
                str(output_dir),
            ],
            cwd=work_dir,
            check=False,
        )
        if self.stop_requested:
            return Failure(
                kind=FailureKind.TIMEOUT,
                reason=f"{self.input.cell_name} was stopped at its wall-clock boundary",
            )
        if completed.returncode != 0:
            return Failure(
                kind=FailureKind.CRASH,
                reason=f"{self.input.cell_name}: train.py exited {completed.returncode}",
            )
        metrics_path = output_dir / "metrics.json"
        if not metrics_path.is_file() or not (output_dir / "model.pt").is_file():
            return Failure(
                kind=FailureKind.ARTIFACT_MISSING,
                reason=f"{self.input.cell_name}: train.py wrote no metrics.json or model.pt",
            )
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        return CellOutput(
            cell_name=self.input.cell_name,
            output_dir=str(output_dir),
            metric=float(metrics[self.input.metric_name]),
        )

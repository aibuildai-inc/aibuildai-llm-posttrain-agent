"""Run one frozen individual to its terminal outcome; decide nothing about fitness."""

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
class EvaluateIndividualInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    individual_id: str
    config_json: str
    source_dir: str
    python_path: str
    data_dir: str
    metric_name: str


class EvaluateIndividualOutput(SuccessfulOutput):
    individual_id: str
    output_dir: str
    checkpoint_path: str
    score: float


class EvaluateIndividualProgram(Program[EvaluateIndividualInput]):
    """Copy the fixed source, run train.py once on this individual's config, read the score it wrote."""

    name: ClassVar[str] = "evaluate_individual"
    policy = Policy(stop_grace_seconds=10.0)

    @action
    def run(self) -> EvaluateIndividualOutput | Failure:
        work_dir = Path(self.scratch_dir) / "source"
        shutil.copytree(self.input.source_dir, work_dir, symlinks=True)
        config_path = work_dir / "config.json"
        config_path.write_text(self.input.config_json, encoding="utf-8")
        output_dir = Path(self.artifacts_dir) / self.input.individual_id
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
                reason=f"{self.input.individual_id} was stopped at its wall-clock boundary",
            )
        if completed.returncode != 0:
            return Failure(
                kind=FailureKind.CRASH,
                reason=f"{self.input.individual_id}: train.py exited {completed.returncode}",
            )
        metrics_path = output_dir / "metrics.json"
        checkpoint = output_dir / "model.pt"
        if not metrics_path.is_file() or not checkpoint.is_file():
            return Failure(
                kind=FailureKind.ARTIFACT_MISSING,
                reason=f"{self.input.individual_id}: train.py wrote no metrics.json or model.pt",
            )
        metrics = json.loads(metrics_path.read_text(encoding="utf-8"))
        return EvaluateIndividualOutput(
            individual_id=self.input.individual_id,
            output_dir=str(output_dir),
            checkpoint_path=str(checkpoint),
            score=float(metrics[self.input.metric_name]),
        )

"""Run one frozen (candidate, benchmark) target to its terminal outcome; decide nothing."""

from __future__ import annotations

from engine.durable_execution import action

import json
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pydantic import ConfigDict

from engine.execution_output import SuccessfulOutput
from engine.failure import Failure, FailureKind
from engine.work_unit.program import Policy, Program

from ..agents.io import SliceMetric


@dataclass(frozen=True)
class BenchmarkInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    evaluation_id: str
    candidate_id: str
    candidate_path: str
    benchmark_id: str
    dataset_path: str
    seed: int
    repetitions: int
    requested_slices: tuple[str, ...]
    required_examples: int


class BenchmarkOutput(SuccessfulOutput):
    evaluation_id: str
    candidate_id: str
    benchmark_id: str
    primary_score: float
    metric_components: tuple[tuple[str, float], ...]
    slices: tuple[SliceMetric, ...]
    artifact_dir: str
    predictions_path: str
    report_path: str
    evaluated_examples: int
    expected_examples: int
    wall_clock_seconds: float


class BenchmarkProgram(Program[BenchmarkInput]):
    """Run the fixed benchmark evaluator on one candidate and read its typed report."""

    name: ClassVar[str] = "fixed_benchmark_evaluation"
    policy = Policy(stop_grace_seconds=10.0)

    @action
    def run(self) -> BenchmarkOutput | Failure:
        output_dir = Path(self.artifacts_dir) / self.input.evaluation_id
        output_dir.mkdir(parents=True)
        completed = subprocess.run(
            [
                "python3",
                "-m",
                "benchmark_runner",
                "--candidate",
                self.input.candidate_path,
                "--dataset",
                self.input.dataset_path,
                "--benchmark-id",
                self.input.benchmark_id,
                "--seed",
                str(self.input.seed),
                "--repetitions",
                str(self.input.repetitions),
                "--slices",
                ",".join(self.input.requested_slices),
                "--output-dir",
                str(output_dir),
            ],
            check=False,
        )
        if self.stop_requested:
            return Failure(
                kind=FailureKind.TIMEOUT,
                reason=f"{self.input.evaluation_id} was stopped at its wall-clock boundary",
            )
        if completed.returncode != 0:
            return Failure(
                kind=FailureKind.CRASH,
                reason=f"{self.input.evaluation_id}: benchmark_runner exited {completed.returncode}",
            )
        report_path = output_dir / "report.json"
        predictions_path = output_dir / "predictions.jsonl"
        if not report_path.is_file() or not predictions_path.is_file():
            return Failure(
                kind=FailureKind.ARTIFACT_MISSING,
                reason=f"{self.input.evaluation_id}: benchmark_runner wrote no report.json or predictions.jsonl",
            )
        report = json.loads(report_path.read_text(encoding="utf-8"))
        evaluated_examples = int(report["evaluated_examples"])
        if evaluated_examples < self.input.required_examples:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=(
                    f"{self.input.evaluation_id}: coverage {evaluated_examples} below "
                    f"the required {self.input.required_examples}"
                ),
            )
        return BenchmarkOutput(
            evaluation_id=self.input.evaluation_id,
            candidate_id=self.input.candidate_id,
            benchmark_id=self.input.benchmark_id,
            primary_score=float(report["primary_score"]),
            metric_components=tuple(sorted(report["metric_components"].items())),
            slices=tuple(SliceMetric(**row) for row in report["slices"]),
            artifact_dir=str(output_dir),
            predictions_path=str(predictions_path),
            report_path=str(report_path),
            evaluated_examples=evaluated_examples,
            expected_examples=int(report["expected_examples"]),
            wall_clock_seconds=float(report["wall_clock_seconds"]),
        )

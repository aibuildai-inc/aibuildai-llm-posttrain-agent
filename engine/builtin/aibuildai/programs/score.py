"""The built-in structured score Program."""

from __future__ import annotations

from engine.durable_execution import (
    ExecutionContext,
    FileRef,
    Handle,
    action,
)
from engine.execution_output import ExecutionOutput

import json
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import ClassVar

from pydantic import ConfigDict, ValidationError

from engine.base import WorkflowBaseModel
from engine.builtin.aibuildai.io import (
    ScoredAttempt,
    grading_files,
    task_data,
    task_folder_files,
)
from engine.capability import ExecutionCapability
from engine.failure import Failure, FailureKind
from engine.work_unit.program import Program


class ScoreOutput(ScoredAttempt):
    """The score one attempt directory measured: ScoreProgram's own Output."""


@dataclass(frozen=True)
class ScoreProgramInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    output_dir: str
    score_program_path: str


_CALL_SCORE = """\
import importlib.util
import json
import pathlib
import sys

spec = importlib.util.spec_from_file_location("_aibuildai_score", sys.argv[1])
if spec is None or spec.loader is None:
    raise RuntimeError(f"score module cannot be loaded from {sys.argv[1]}")
module = importlib.util.module_from_spec(spec)
spec.loader.exec_module(module)
function = getattr(module, "score", None)
if not callable(function):
    raise RuntimeError("score module must define score(output_dir)")
value = function(sys.argv[2])
pathlib.Path(sys.argv[3]).write_text(json.dumps(value, allow_nan=False))
"""


def call_score_module(
    *, score_program_path: str, output_dir: str, result_path: Path
) -> "ScoreOutput | str":
    """Run the frozen module's ``score(output_dir)``, or say why it could not.

    The one caller-independent statement of what the run's score contract is:
    whoever needs a number for an attempt directory asks here, and whoever
    needs a verdict on the module itself reads the same sentence back."""
    completed = subprocess.run(
        [
            sys.executable,
            "-c",
            _CALL_SCORE,
            score_program_path,
            output_dir,
            str(result_path),
        ],
        capture_output=True,
        text=True,
        check=False,
    )
    if completed.returncode != 0:
        detail = (completed.stderr or completed.stdout)[-2000:]
        return f"score(output_dir) failed: {detail}"
    try:
        value = json.loads(result_path.read_text(encoding="utf-8"))
        return ScoreOutput.model_validate({**value, "output_dir": output_dir})
    except (OSError, ValueError, ValidationError) as exc:
        return str(exc)


class ScoreProgram(Program[ScoreProgramInput]):
    """Score one composite through the frozen Python callable."""

    name: ClassVar[str] = "score_program"

    @action
    def run(self) -> ScoreOutput | Failure:
        scored = call_score_module(
            score_program_path=self.input.score_program_path,
            output_dir=self.input.output_dir,
            result_path=Path(self.scratch_dir) / "score-result.json",
        )
        if isinstance(scored, str):
            return Failure(kind=FailureKind.NO_OUTPUT, reason=scored)
        return scored


class ScoreLaunch(WorkflowBaseModel):
    """The product's binding for this run's grading: all a Score run needs but the directory.

    The frozen score program and what one Score run may spend are the product's,
    settled once when the product froze the grading contract. A search method is
    handed this and decides only WHETHER to score, WHEN, and WHICH attempt
    directory; it never names the score program and cannot build a Score run
    that measures with a different one."""

    model_config = ConfigDict(frozen=True, extra="forbid")

    score_program_path: str
    capability: ExecutionCapability

    def grants(self, attempt: FileRef) -> tuple[FileRef, ...]:
        """Everything one Score Action reads: the attempt it measures, and the material the frozen module may consult.

        Stated once, here, because the grading resources are the product's and
        no search method should have to know them. A score module written
        against a sibling label file or the task's own evaluator reads them
        because this grant binds them, never because some string in its Input
        happened to exist on the host."""
        return (attempt, task_data(), task_folder_files(), grading_files())

    async def measure(
        self,
        ctx: ExecutionContext,
        producer: "Handle[ExecutionOutput[bool]]",
        *,
        output_dir: str,
        attempt: FileRef,
        gpus: tuple[int, ...] | None = None,
    ) -> "Handle[ScoreOutput | Failure]":
        """Start the Score Action that measures one attempt directory.

        The whole grading invocation in one place: the frozen program, what a
        Score run may spend, and what it may read. A search method decides
        only WHETHER to score, WHEN, and WHICH attempt, and holds the Handle
        the way it holds any other."""
        return await ctx.spawn(
            self.program(output_dir).run,
            upstream=(producer,),
            read=self.grants(attempt),
            capability=self.placed(gpus),
            capture_failure=True,
        )

    def placed(self, gpus: tuple[int, ...] | None = None) -> ExecutionCapability:
        """What one Score run may spend, on the cards it must measure.

        ``gpus`` is for the caller whose scored attempt needs the cards the
        producer ran on -- a checkpoint that must be loaded to be measured.
        Sharing them is legal, so this places the Score beside the producer
        rather than asking for cards of its own. It changes where the Score
        runs, never how it scores."""
        if gpus is None:
            return self.capability
        return self.capability.model_copy(update={"gpus": gpus})

    def program(self, output_dir: str) -> ScoreProgram:
        """The Score identity that measures one attempt directory."""
        return ScoreProgram(
            input=ScoreProgramInput(
                output_dir=output_dir,
                score_program_path=self.score_program_path,
            ),
        )

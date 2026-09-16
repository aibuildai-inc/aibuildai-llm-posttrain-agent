"""The environment preparation Agent and its verifier chain."""

from __future__ import annotations

import logging
import stat
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import ConfigDict

from engine.capability import ExecutionCapability
from engine.durable_execution import ActionInvocation, action, run_configs
from engine.execution_output import VerifierOutput
from engine.failure import Failure
from engine.setup_artifacts import read_public_dir
from engine.work_unit.agent.base import Agent, review_request
from engine.work_unit.agent.policy import (
    JUDGE_TOOLS_WITH_SCRATCH,
    RolePolicy,
    SETUP_TOOLS,
    SystemDir,
)
from engine.work_unit.program.base import Program
from engine.builtin.aibuildai.programs.score import call_score_module

if TYPE_CHECKING:
    from engine.paths import RunPaths
from engine.builtin.aibuildai.agents.setup.io import SetupInput, SetupOutput, SetupReviewInput

def _not_regular_file(path: Path) -> str | None:
    """Why ``path`` is not a plain regular file, or None when it is one.

    Uses ``lstat`` so a symlink is judged on the link itself, never on what it points at: ``is_file`` would follow a link and call a link-to-a-file a file. Returns a short reason phrase for the caller's message."""
    try:
        mode = path.lstat().st_mode
    except OSError:
        return "does not exist"
    if stat.S_ISLNK(mode):
        return "is a symlink"
    if not stat.S_ISREG(mode):
        return "is not a regular file"
    return None


@dataclass(frozen=True)
class SetupVerifierInput:
    """Every path SETUP's boundary is decided from.

    Every path here is one this check reads: the two frozen files it stats, the public dir it walks, the task folder each public link must resolve inside, and the baseline attempt it scores. What the check may READ at all is its Action's grant, not these fields."""

    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    score_program_path: str
    readme_path: str
    public_dir: str
    baseline_attempt_dir: str
    task_folder: str


class SetupVerifierProgram(Program[SetupVerifierInput]):
    """One check of the whole grading contract SETUP just established.

    The on-disk facts first -- the two frozen files, the design's public dir, the baseline attempt -- and then the one thing they exist for: running SETUP's own ``score(output_dir)`` over SETUP's own baseline attempt, through the same frozen callable the run uses for every design.

    It does not ask whether the Program environment interpreter exists. That was worth asking while this check ran inside the provider step. It cannot fail here: this check is an ordinary Program, so the Executor resolves the run's one environment interpreter before the check starts and a missing one fails the launch -- and telling SETUP to resubmit would never have fixed a missing framework interpreter anyway.

    It is one verification, not two, because the cheap file checks are exactly the preconditions of the scoring call: splitting them would put a second Program between SETUP and its own contract while the first one has already proved nothing can be scored."""

    name: ClassVar[str] = "setup_verifier"

    @action
    def run(self) -> VerifierOutput | Failure:
        program = Path(self.input.score_program_path)
        readme = Path(self.input.readme_path)
        # The two frozen files must each be a PLAIN file, never a symlink. A
        # link form would turn a frozen contract into a live reference -- the
        # README could change words mid-run, the program could become a
        # different program -- so lstat (no link following) proves each is a
        # regular file before anything reads through it.
        for path, what in ((program, "score program"), (readme, "frozen README")):
            not_regular = _not_regular_file(path)
            if not_regular is not None:
                return VerifierOutput(
                    passed=False,
                    reason=(
                        f"the {what} {path} {not_regular}. It must be a plain file "
                        f"you wrote, not a symlink: the run freezes it, and a link "
                        f"would let it change out from under the run. Write the "
                        f"{what} as a plain file and call StructuredOutput again."
                    ),
                )
        if not readme.read_text().strip():
            return VerifierOutput(
                passed=False,
                reason=(
                    f"the frozen README {readme} is empty. Write the "
                    f"user's own words there verbatim, then your own contract "
                    f"section — every later role reads only this file, so an empty "
                    f"one leaves the whole run with no task. Write the frozen "
                    f"README at {readme} and call StructuredOutput again."
                ),
            )
        # The public dir is proved HERE, in setup's own session, because it IS
        # the whole of what a design can read. A wrong one is either a
        # design with no data or a design with the answers, and both are
        # cheapest to fix while setup is still awake. read_public_dir is the
        # SAME public dir reader the design sandbox builder uses: every
        # symlink SETUP authored must have a real target inside the task
        # folder, or it raises ValueError with the exact link to fix.
        public_dir = Path(self.input.public_dir)
        try:
            read_public_dir(str(public_dir), self.input.task_folder)
        except ValueError as exc:
            return VerifierOutput(
                passed=False,
                reason=(
                    f"{exc} Fix the symlink target so it points inside the task "
                    "folder and call StructuredOutput again."
                ),
            )
        baseline = Path(self.input.baseline_attempt_dir)
        if not baseline.is_dir() or not any(baseline.iterdir()):
            return VerifierOutput(
                passed=False,
                reason=(
                    f"the baseline attempt {baseline} is missing or empty. Write a "
                    f"trivial attempt directory shaped like the output a real design "
                    f"will produce — worthless content is the point — so the score "
                    f"program can be proved against a plausible attempt before the "
                    f"search starts. Write a trivial baseline attempt at {baseline} "
                    "and call StructuredOutput again."
                ),
            )
        # The contract is only real once it has scored something. SETUP wrote a
        # plausible attempt; this runs the run's own scoring path over it.
        scoring = call_score_module(
            score_program_path=self.input.score_program_path,
            output_dir=self.input.baseline_attempt_dir,
            result_path=Path(self.scratch_dir) / "score-result.json",
        )
        if isinstance(scoring, str):
            return VerifierOutput(
                passed=False,
                reason=(
                    f"the score module failed its score(output_dir) check: "
                    f"{scoring}. Fix score(output_dir) so it returns a score "
                    "mapping, then call StructuredOutput again."
                ),
            )
        return VerifierOutput(
            passed=True,
            reason=f"score program accepted: baseline score {scoring.score}",
        )


logger = logging.getLogger(__name__)

SETUP_POLICY = RolePolicy(
    tools=tuple(SETUP_TOOLS) + ("Write", "Edit"),
    system_write=(SystemDir.CONDA_ROOT,),
    task_environment=True,
)


def _run_paths() -> "RunPaths":
    """This run's own paths, from the product shell that owns them."""
    # Imported here, not at module scope: the product shell imports this
    # role, so the arrow back to it exists only while a run is served.
    from engine.builtin.aibuildai.search import product_runtime

    return product_runtime().run_paths


def _run_resource_facts() -> dict[str, object]:
    """The run resources the two SETUP templates name, each named once."""
    run_paths = _run_paths()
    return {
        "public_dir": run_paths.public_dir,
        "private_dir": run_paths.private_dir,
        "task_folder": run_configs().task_folder,
        "baseline_attempt_dir": run_paths.baseline_attempt_dir,
        "output_base_dir": run_configs().output_base_dir,
    }


class SetupAgent(Agent[SetupInput, SetupOutput]):
    name: ClassVar[str] = "setup"
    prompt_template = "agent/setup.j2"
    policy = SETUP_POLICY

    def prompt_facts(self) -> dict[str, object]:
        """The run resources this role's own template names, from the product that owns them."""
        return _run_resource_facts()

    @classmethod
    def semantic_reviewer(cls) -> "type[Agent[Any, VerifierOutput]] | None":
        return SetupVerifierAgent

    async def verifier_invocations(
        self, candidate: SetupOutput
    ) -> tuple[ActionInvocation[Any, VerifierOutput], ...]:
        """SETUP's own contract check, then the reviewer that judges the contract.

        The chain needs no environment step of its own. SETUP's verifier is a
        normal Program, and a Program runs on the one environment the run
        prepared before SETUP started, so a candidate is scored against
        whatever SETUP has installed by the time the chain runs -- including a
        candidate that follows a rejected one."""
        run_paths = _run_paths()
        chain: list[ActionInvocation[Any, VerifierOutput]] = [
            (
                SetupVerifierProgram(
                    input=SetupVerifierInput(
                        score_program_path=run_paths.score_program_path,
                        readme_path=run_paths.readme_path,
                        public_dir=run_paths.public_dir,
                        baseline_attempt_dir=run_paths.baseline_attempt_dir,
                        task_folder=run_configs().task_folder,
                    ),
                    # Named, not counted: the score program starts a vLLM
                    # server, and a Program Action with no visible device
                    # either dies or escapes onto a card this run never
                    # named. It runs on SETUP's own cards, which is allowed
                    # because a card is a placement and not a possession.
                ).run,
                ExecutionCapability(
                    wall_clock_seconds=self._local_wall_clock,
                    gpus=self.capability.gpu_indices,
                ),
            )
        ]
        reviewer = type(self).semantic_reviewer()
        if reviewer is not None and self.reviewer_capability is not None:
            chain.append(
                (
                    reviewer(
                        input=SetupReviewInput(
                            task_prompt=self.input.task_prompt,
                        ),
                    ).run,
                    self.review_capability,
                    review_request(candidate),
                )
            )
        return tuple(chain)


# The SETUP review judges the run's grading contract, which lives in
# PRIVATE (the score program, the holdout answers, the baseline attempt) and
# is judged AGAINST the TASK_FOLDER those were carved from —
# reading them IS the review. Its verdict flows only to SETUP, which reads the
# task folder itself, so the answer-key leak channel the design reviews guard
# does not apply here. PUBLIC_READ serves its what-can-a-design-reach
# checks, RUN_WORKSPACE SETUP's execution trace in the run workspace tree.
# CONDA_ROOT/CONDA_PACKAGES let its installed-packages check read the real
# environment instead of SETUP's own summary. The citation-verification tools
# serve its is-this-the-named-metric/protocol checks; Write/Edit reach only SCRATCH, so
# every reviewed artifact stays immutable to it.
SETUP_REVIEW_POLICY = RolePolicy(
    tools=(
        *JUDGE_TOOLS_WITH_SCRATCH,
        "BashOutput",
        "KillBash",
        "StructuredOutput",
    ),
    system_read=(SystemDir.CONDA_ROOT, SystemDir.CONDA_PACKAGES),
    task_environment=True,
)


class SetupVerifierAgent(Agent[SetupReviewInput, VerifierOutput]):
    """The one Setup verifier whose subject is not a design: the grading contract it judges lives in the private dir, and it is judged against the task folder that contract was carved from."""

    name: ClassVar[str] = "setup_review"
    prompt_template = "agent/setup_review.j2"
    policy = SETUP_REVIEW_POLICY

    def prompt_facts(self) -> dict[str, object]:
        """The grading material this review reads, and the tree SETUP itself worked in."""
        setup = self.parent
        if setup is None:
            raise AssertionError("the setup review runs under the SETUP it judges")
        return {**_run_resource_facts(), "setup_dir": setup.directory}


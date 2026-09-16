from __future__ import annotations

import os
import subprocess
from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import ConfigDict

from engine.capability import ExecutionCapability
from engine.builtin.aibuildai.io import grading_files, task_folder_files
from engine.durable_execution import ActionInvocation, FileRef, action
from engine.execution_output import VerifierOutput
from engine.failure import Failure, FailureKind
from engine.work_unit.agent.base import Agent, review_request
from engine.builtin.meta.agents.meta.io import (
    MetaAgentInput,
    MetaAgentOutput,
    MetaReviewInput,
)
from engine.work_unit.agent.policy import (
    ALL_BUILTIN_TOOLS,
    JUDGE_TOOLS_WITH_SCRATCH,
    RolePolicy,
    SystemDir,
)
from engine.work_unit.program.base import Program
from infra.util.paths import is_within
from infra.owned_tools import owned

if TYPE_CHECKING:
    from config import AgentConfig

# The mechanical checks are meant to be quick, and a package that cannot be
# read in this long is one the author must make smaller.
_CHECK_SECONDS = 30
# The same ceiling the retired in-Agent check applied to a tool's output: a
# rejection reason goes back into a conversation, so it must stay readable.
_OUTPUT_LIMIT_BYTES = 65536


def _clipped(text: str) -> str:
    """One check's output, trimmed to what a conversation can be handed back."""
    stripped = text.strip()
    return (
        stripped
        if len(stripped) <= _OUTPUT_LIMIT_BYTES
        else stripped[:_OUTPUT_LIMIT_BYTES] + " ... (output truncated)"
    )


def _absent_paths(value: object, key: str, run_home: Path) -> list[str]:
    """Name every absolute path in the payload that exists neither on this host nor where a child may create it (under the run or /tmp)."""
    if isinstance(value, str):
        if not value.startswith("/"):
            return []
        path = Path(value)
        if path.exists() or is_within(path, run_home) or is_within(path, "/tmp"):
            return []
        return [f"{key} = {value}"]
    if isinstance(value, dict):
        return [
            hit
            for sub_key, sub_value in value.items()
            if isinstance(sub_key, str)
            for hit in _absent_paths(sub_value, f"{key}.{sub_key}" if key else sub_key, run_home)
        ]
    if isinstance(value, (list, tuple)):
        return [
            hit
            for index, item in enumerate(value)
            for hit in _absent_paths(item, f"{key}[{index}]", run_home)
        ]
    return []


@dataclass(frozen=True)
class MetaVerifierInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    run_home: str
    package_dir: str
    module_name: str
    ruff_path: str


class MetaVerifierProgram(Program[MetaVerifierInput]):
    """Check that the submitted package can actually be loaded and built.

    Only that. The design report beside it is checked by the common document
    verifier for existence and by the required Meta review for truth; whether
    some other file is a second design document is a judgement, not a filename
    test.

    Ruff and the package contract both run inside this Program's own worker
    process, which the run throws away after the Action: that is the isolation
    the old in-Agent execution machinery was rebuilding by hand, and Program
    already owns the process, the cgroup, and the sandbox that give it."""

    name: ClassVar[str] = "meta_verifier"

    @action
    def run(self, candidate: MetaAgentOutput) -> VerifierOutput | Failure:
        from engine.generated_definitions import _definition_package_dir
        from engine.builtin.meta.agents.meta.package_verifier import (
            PackageContractError,
            verify,
        )

        search_dir = Path(self.input.package_dir)
        if not search_dir.is_dir():
            return VerifierOutput(
                passed=False,
                reason=(
                    "no search/ directory found. Write the package under the "
                    "search/ directory."
                ),
            )
        run_home = Path(self.input.run_home)
        payload = candidate.input_payload
        absent = _absent_paths(payload, "", run_home)
        if absent:
            return VerifierOutput(
                passed=False,
                reason=(
                    "input_payload names a path that does not exist on this host: "
                    + "; ".join(absent)
                    + ". Name the run's real directories from the facts in your "
                    "prompt; the example packages' payload paths are placeholders."
                ),
            )
        try:
            package_relpath = search_dir.relative_to(run_home).as_posix()
            checked_dir = _definition_package_dir(run_home, package_relpath)
        except (TypeError, ValueError) as exc:
            return VerifierOutput(
                passed=False,
                reason=f"{exc} Write a real package under search/.",
            )
        try:
            completed = subprocess.run(
                [
                    self.input.ruff_path,
                    "check",
                    "--isolated",
                    "--no-cache",
                    "--select",
                    "F",
                    str(checked_dir),
                ],
                capture_output=True,
                text=True,
                check=False,
                cwd=self.scratch_dir,
                timeout=_CHECK_SECONDS,
            )
        except subprocess.TimeoutExpired:
            return VerifierOutput(
                passed=False,
                reason=(
                    f"Ruff did not finish in {_CHECK_SECONDS} seconds. Make the "
                    "package smaller, then submit again."
                ),
            )
        if completed.returncode == 1:
            return VerifierOutput(
                passed=False,
                reason=(
                    _clipped(completed.stdout or completed.stderr)
                    + " Fix every Ruff error, then submit again."
                ),
            )
        if completed.returncode != 0:
            return Failure(
                kind=FailureKind.INFRA,
                reason=_clipped(completed.stdout + completed.stderr)
                or "Ruff could not check the generated package",
            )
        # PYTHONDONTWRITEBYTECODE: the generated package is activated once, in
        # this throwaway worker, and must leave nothing beside the author's
        # source for the next Action to import instead.
        os.environ["PYTHONDONTWRITEBYTECODE"] = "1"
        try:
            verify(
                run_home,
                package_relpath=package_relpath,
                module_name=self.input.module_name,
                input_payload=payload,
            )
        except PackageContractError as exc:
            diagnostic = _clipped(f"PackageContractError: {exc}")
            instruction = (
                "Add capability= to every ctx.spawn(...): "
                "ExecutionCapability() for a child Search, and a finite "
                "positive one for an Agent or Program; keep upstream= on each "
                "execution."
                if "declares no capability=" in diagnostic
                else "Fix the package so the product can load it and build its "
                "Search Input, then submit again."
            )
            return VerifierOutput(passed=False, reason=f"{diagnostic} {instruction}")
        return VerifierOutput(passed=True, reason="")


META_AGENT_POLICY = RolePolicy(
    tools=tuple(ALL_BUILTIN_TOOLS),
)


class MetaAgent(Agent[MetaAgentInput, MetaAgentOutput]):
    """The Agent role that researches, designs, and writes up one Search.

    It produces two artifacts, in two sibling directories: ``search/``, the
    generated Search package the product runs, which holds importable Python
    and nothing else, and ``report/``, the research paper a person reads. Both
    are its own -- the package verifier below checks that the package can
    actually be loaded and built, the common document verifier checks that the
    report exists, and the required Meta review judges whether the two agree."""

    required_pdf: ClassVar[str] = "report/paper.pdf"
    name: ClassVar[str] = "meta"
    prompt_template = "agent/meta.j2"
    policy = META_AGENT_POLICY

    def launch_directory(self) -> str:
        """Its own artifacts: it writes the package and the report there, so that is where its session opens."""
        return self.artifacts_dir

    def reviewer_reads(self) -> tuple[FileRef, ...]:
        """The user's whole task folder and the run's grading material.

        Its reviewer audits the authored Search against what the run was asked
        to do and against the grading it must not manipulate, so it needs both.
        This role never does: a Search author that could read the answer key
        could write a Search that reads it."""
        return (task_folder_files(), grading_files())

    @classmethod
    def semantic_reviewer(cls) -> "type[Agent[Any, VerifierOutput]] | None":
        return MetaVerifierAgent

    @classmethod
    def composes_reviewer(cls, configs: "AgentConfig") -> bool:
        """Always. A generated package nobody read is not a design, so this one review is not the operator's to switch off."""
        del configs
        return True

    def required_pdf_for_run(self) -> str | None:
        """No document gate when the run switched the design report off (``search.input.report: false``)."""
        return type(self).required_pdf if self.input.report else None

    async def verifier_invocations(
        self, candidate: MetaAgentOutput
    ) -> tuple[ActionInvocation[Any, VerifierOutput], ...]:
        # Both deterministic gates before the review that reads them: a
        # package that cannot load and a report that was never compiled are
        # both facts a reviewer would only spend a model call to rediscover.
        # The load check goes first because it is the cheaper of the two.
        from engine.builtin.meta.authoring import _module_name, _package_dir

        chain: list[ActionInvocation[Any, VerifierOutput]] = [
            (
                MetaVerifierProgram(
                    input=MetaVerifierInput(
                        run_home=self.input.run_home,
                        package_dir=str(_package_dir(self)),
                        module_name=_module_name(self, self.input.run_home),
                        ruff_path=str(owned("ruff")),
                    ),
                ).run,
                ExecutionCapability(
                    wall_clock_seconds=self._local_wall_clock,
                    gpus=0
                ),
                candidate,
            )
        ]
        chain.extend(self.document_verifier())
        reviewer = type(self).semantic_reviewer()
        if reviewer is None:
            raise AssertionError("the Meta review is required and must be declared")
        chain.append(
            (
                reviewer(
                    input=meta_review_context(self),
                ).run,
                self.review_capability,
                # The submission, and the request the author was given: both
                # are per-candidate facts, so both travel as this Action's own
                # argument rather than as a second typed copy of them.
                review_request(candidate)
                + (
                    f"\n\nRuntime request the author was given:\n\n{handoff}"
                    if (handoff := self._action.request)
                    else ""
                ),
            )
        )
        return tuple(chain)


# The reviewer's mandate includes failing an unsupported or invented source, so
# it carries the citation-verification set every other review role carries. Its
# hardest claims are about third-party libraries the package will call: an
# author that rejects a method because trl "does not implement" something is
# making a claim only the installed package or that library's own documentation
# for the installed version can settle, and a reviewer with Read, Glob and Grep
# alone could reach neither. The task environment is bound read-only here, the
# way it is for every role that is not the environment builder, because reading
# the version the package will actually import is the point.
META_REVIEW_POLICY = RolePolicy(
    tools=(
        *JUDGE_TOOLS_WITH_SCRATCH,
        "BashOutput",
        "KillBash",
        "StructuredOutput",
    ),
    system_read=(SystemDir.CONDA_ROOT, SystemDir.CONDA_PACKAGES),
    task_environment=True,
)


class MetaVerifierAgent(Agent[MetaReviewInput, VerifierOutput]):
    """Required reviewer of every generated Search package."""

    name: ClassVar[str] = "meta_review"
    prompt_template = "agent/meta_review.j2"
    policy = META_REVIEW_POLICY


def meta_review_context(producer: Agent) -> MetaReviewInput:
    """Build the stable side of the Meta review from the reviewed invocation.

    The package under review does not appear here. What the reviewer judges is
    the request of its own review Action; these are the run facts that stay
    true across every candidate the same invocation submits. Every one of them
    was already frozen onto the producer's own ``MetaAgentInput`` by the Search
    that spawned it, out of that Search's own immutable Input, so this reads
    only ``producer.input`` and asks the run nothing."""
    from engine.builtin.meta.authoring import _package_dir

    payload = producer.input
    if not isinstance(payload, MetaAgentInput):
        raise AssertionError("meta_review_context requires a MetaAgent")
    # Derived from this invocation's own identity, beside the two facts above
    # that already are. It was a field of MetaAgentInput once, and that made it
    # the single field of a copied re-entry grant that had to be overwritten:
    # an author who forgot the one line shipped a payload that was valid,
    # passed the verifier, and pointed this review at another Search's
    # workspace with nothing to say so.
    #
    # The owner is whoever spawned this MetaAgent. On every path that exists
    # that is the Search itself, and on the top-level one this is the same
    # value the deleted Input field carried. An author who nests a replan
    # inside a Composite of that Search would name the Composite's directory
    # instead, which still sits inside the owning Search's own subtree; the
    # deleted field could name a different Search entirely.
    owner = producer.parent
    if owner is None:
        raise AssertionError(
            "a MetaAgent is spawned by another execution, so it has a parent"
        )
    return MetaReviewInput(
        package_dir=str(_package_dir(producer)),
        agent_path=producer.path,
        owning_search_workspace=owner.directory,
        metric_direction=payload.metric_direction,
        resources=payload.resources,
        report=payload.report,
    )

"""ReviserAgent — revision-proposal generation role.

The Reviser produces ``ReviserOutput`` (a list of RevisionProposals — diffs against the completed design it revises). Its concrete class owns its prompt and Output.
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import TYPE_CHECKING, Any, ClassVar

from pydantic import ConfigDict

from engine.capability import ExecutionCapability
from engine.durable_execution import ActionInvocation, action
from engine.execution_output import VerifierOutput
from engine.failure import Failure
from engine.work_unit.agent.base import Agent, AgentInput, review_request
from engine.work_unit.agent.policy import (
    ALL_BUILTIN_TOOLS,
    JUDGE_TOOLS_WITH_SCRATCH,
    RolePolicy,
)
from engine.builtin.tree.agents.reviser.io import (
    ReviserInput,
    ReviserOutput,
)
from engine.work_unit.program.base import Program

if TYPE_CHECKING:
    pass


@dataclass(frozen=True)
class ReviserVerifierInput:
    __pydantic_config__: ClassVar[ConfigDict] = ConfigDict(extra="forbid")

    # The revised design's own facts: the tree a patch must match, and the id
    # that names the design in the sentence the Reviser reads back.
    revised_uid: str
    revised_directory: str


class ReviserVerifierProgram(Program[ReviserVerifierInput]):
    """Match every proposed patch against the revised design's real source.

    Each ``code_patch.find`` must occur EXACTLY ONCE in the named file at
    submit time. The downstream coder's edit tool reports 'string not found'
    for 0 occurrences and 'old_string not unique' for more than 1 (``CodePatch``
    has no replace_all field, so the contract is exactly-one). Denying here
    lets the Reviser correct the find before a child design is spawned.

    The files are the whole subject, which is why this is durable work rather
    than an Output rule: the answer lives in a tree the Reviser does not own
    and can change under it."""

    name: ClassVar[str] = "reviser_verifier"

    @action
    def run(self, candidate: ReviserOutput) -> VerifierOutput | Failure:
        revised_dir = Path(self.input.revised_directory)
        problems: list[str] = []
        for proposal in candidate.proposals:
            for patch in proposal.code_patches:
                target = revised_dir / patch.file
                if not target.is_file():
                    problems.append(
                        f"code_patch file {patch.file!r} not found under the"
                        f" revised {self.input.revised_uid} source"
                    )
                    continue
                count = target.read_text().count(patch.find)
                if count == 0:
                    problems.append(
                        f"code_patch.find absent in {patch.file!r} (the coder's"
                        f" edit will fail with 'string not found'); quote the"
                        f" EXACT substring from the revised design's source"
                    )
                elif count > 1:
                    problems.append(
                        f"code_patch.find occurs {count}x in {patch.file!r} (not"
                        f" unique; the coder's edit cannot apply a non-unique"
                        f" find); quote a longer substring unique to the one site"
                    )
        if problems:
            return VerifierOutput(
                passed=False,
                reason=(
                    "Reviser find-string contract: "
                    + "; ".join(problems)
                    + ". Fix the code_patch find strings to match the revised "
                    "design's source exactly and call StructuredOutput again."
                ),
            )
        return VerifierOutput(passed=True, reason="")


REVISER_POLICY = RolePolicy(
    tools=tuple(ALL_BUILTIN_TOOLS),
)


class ReviserAgent(Agent[ReviserInput, ReviserOutput]):
    """Emits ``ReviserOutput`` — a list of diff-shaped ``RevisionProposal`` against the effective design plan of the completed design it revises."""

    name: ClassVar[str] = "reviser"
    prompt_template = "agent/reviser.j2"

    policy = REVISER_POLICY

    @classmethod
    def semantic_reviewer(cls) -> "type[Agent[Any, VerifierOutput]] | None":
        return ReviserVerifierAgent

    async def verifier_invocations(
        self, candidate: ReviserOutput
    ) -> tuple[ActionInvocation[Any, VerifierOutput], ...]:
        # The find strings are matched against the real source first, because
        # that is mechanical; the reviewer that has to read the design to have
        # an opinion runs only on a batch that could be applied at all.
        chain: list[ActionInvocation[Any, VerifierOutput]] = [
            (
                ReviserVerifierProgram(
                    input=ReviserVerifierInput(
                        revised_uid=self.input.target_uid,
                        revised_directory=self.input.target.source_dir,
                    ),
                ).run,
                ExecutionCapability(
                    wall_clock_seconds=self._local_wall_clock,
                    gpus=0
                ),
                candidate,
            )
        ]
        reviewer = type(self).semantic_reviewer()
        if reviewer is not None and self.reviewer_capability is not None:
            chain.append(
                (
                    reviewer(
                        input=AgentInput(),
                    ).run,
                    self.review_capability,
                    review_request(candidate),
                )
            )
        return tuple(chain)


# The REVISER review checks the Reviser's diagnosis and proposed changes
# against the revised design's files. Its verdict
# reason is shown to the Reviser. TASK_FOLDER and PRIVATE stay
# unbound; Write/Edit reach only SCRATCH.
REVISER_REVIEW_POLICY = RolePolicy(
    tools=(
        *JUDGE_TOOLS_WITH_SCRATCH,
        "BashOutput",
        "KillBash",
        "StructuredOutput",
    ),
)


class ReviserVerifierAgent(Agent[AgentInput, VerifierOutput]):
    """The Reviser's semantic reviewer, included when the operator asks for it.

    The batch it judges is the request of its own review Action, so it reads
    the proposals in its first user turn and the design they target at the
    runtime paths. Nothing about the batch is copied to disk for it."""

    policy = REVISER_REVIEW_POLICY
    name: ClassVar[str] = "reviser_review"
    prompt_template = "agent/reviser_review.j2"


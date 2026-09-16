"""WriterAgent writes and compiles the run's one paper.

The Writer owns the whole document: it writes ``paper/paper.tex`` under its own
artifacts, compiles it with the tectonic on its PATH, reads the compiler's
errors, and fixes them itself. The framework asks only that ``paper/paper.pdf``
is there -- the common document verifier. There is no second copy of the paper
anywhere.
"""

from __future__ import annotations

from typing import TYPE_CHECKING, Any, ClassVar

from engine.work_unit.agent.base import Agent, review_request
from engine.work_unit.agent.policy import (
    JUDGE_TOOLS_WITH_SCRATCH,
    RolePolicy,
    SystemDir,
)
from engine.durable_execution import ActionInvocation
from engine.execution_output import VerifierOutput
from engine.builtin.aibuildai.agents.writer.io import (
    WriterInput,
    WriterOutput,
    WriterReviewInput,
)

if TYPE_CHECKING:
    from config import AgentConfig

# Where the Writer keeps its paper, relative to its artifacts. It is the
# document's only address.
PAPER_DIR = "paper"
# Writer reads the run, public data, and delivery paths, and can use the whole
# finished run. Write/Edit reach only its scratch directory.
WRITER_POLICY = RolePolicy(
    tools=(
        *JUDGE_TOOLS_WITH_SCRATCH,
        "BashOutput",
        "KillBash",
        "StructuredOutput",
    ),
    system_read=(SystemDir.CONDA_PACKAGES,),
)


class WriterAgent(Agent[WriterInput, WriterOutput]):
    required_pdf: ClassVar[str] = f"{PAPER_DIR}/paper.pdf"

    name: ClassVar[str] = "writer"
    prompt_template = "agent/writer.j2"
    policy = WRITER_POLICY

    @classmethod
    def semantic_reviewer(cls) -> "type[Agent[Any, VerifierOutput]] | None":
        return WriterVerifierAgent

    @classmethod
    def enabled_in(cls, configs: "AgentConfig") -> bool:
        return configs.writer.enable

    async def verifier_invocations(
        self, candidate: WriterOutput
    ) -> tuple[ActionInvocation[Any, VerifierOutput], ...]:
        # The document must exist before anyone can be asked whether it is
        # true, so this role puts that gate first in its own order.
        chain: list[ActionInvocation[Any, VerifierOutput]] = [
            *self.document_verifier()
        ]
        reviewer = type(self).semantic_reviewer()
        if reviewer is not None and self.reviewer_capability is not None:
            chain.append(
                (
                    reviewer(
                        input=WriterReviewInput(
                            # The paper to read, named like any other stable
                            # fact a role is given. Its policy already grants
                            # the run workspace this sits in, so there is
                            # nothing for the launch path to work out.
                            paper_dir=f"{self.artifacts_dir}/{PAPER_DIR}",
                        ),
                    ).run,
                    self.review_capability,
                    review_request(candidate),
                )
            )
        return tuple(chain)



# The Writer review reads the run paths needed to check one paper. It can
# write only check scripts in scratch.
WRITER_REVIEW_POLICY = RolePolicy(
    tools=(
        *JUDGE_TOOLS_WITH_SCRATCH,
        "BashOutput",
        "KillBash",
        "StructuredOutput",
    ),
)


class WriterVerifierAgent(Agent[WriterReviewInput, VerifierOutput]):
    """The Writer's semantic reviewer, included when the operator asks for it."""

    name: ClassVar[str] = "writer_review"
    prompt_template = "agent/writer_review.j2"
    policy = WRITER_REVIEW_POLICY


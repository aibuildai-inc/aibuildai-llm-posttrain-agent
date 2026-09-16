"""WorkerDesignerAgent -- the role that proposes designs for complete-Worker candidates.

Copied from the tree package's own designer role and trimmed to this
package's own needs: this package composes only the Worker-candidate
Designer, with no semantic reviewer and no plain (non-Worker) Designer
form."""

from __future__ import annotations

from typing import ClassVar

from engine.work_unit.agent.base import Agent
from engine.work_unit.agent.policy import ALL_BUILTIN_TOOLS, RolePolicy
from engine.builtin.nb.agents.designer.io import DesignerInput, WorkerDesignerOutput

# The DESIGNER's sandbox writes only its own scratch: its designs travel as the
# typed terminal Output (journaled), so the agent itself needs no run-dir
# write at all.
DESIGNER_POLICY = RolePolicy(
    tools=tuple(ALL_BUILTIN_TOOLS),
)


class WorkerDesignerAgent(Agent[DesignerInput, WorkerDesignerOutput]):
    """Designer for complete-Worker candidates."""

    prompt_template = "agent/designer.j2"
    name: ClassVar[str] = "designer"
    policy = DESIGNER_POLICY

"""JudgeAgent — scores one batch of proposed designs, fresh plans and revisions alike, against the completed parent's context when the batch has one.

Reads the run; the only place it may write is its own scratch dir.
"""

from __future__ import annotations

from typing import ClassVar

from engine.work_unit.agent.base import Agent
from engine.work_unit.agent.policy import JUDGE_TOOLS_WITH_SCRATCH, RolePolicy
from engine.builtin.tree.agents.judge.io import JudgeInput, JudgeOutput

JUDGE_POLICY = RolePolicy(
    tools=tuple(JUDGE_TOOLS_WITH_SCRATCH),
)


class JudgeAgent(Agent[JudgeInput, JudgeOutput]):
    """Scores one batch of proposed designs, and nothing stands in for it.

    A Judge that ran and said nothing records its Failure, so the Search
    retries it: a neutral score invented here would enter the run as a
    judgement this Judge never made, and every reader downstream -- the
    Selector first -- would treat it as one."""

    name: ClassVar[str] = "judge"
    prompt_template = "agent/judge.j2"
    policy = JUDGE_POLICY

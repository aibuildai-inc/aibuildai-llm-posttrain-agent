"""Resolve what the AIBuildAI product's OWN children may spend, once, at startup.

This module answers for the product's fixed roles only -- Setup, Submitter,
Finalizer, Writer and the Score Program -- because their jobs are fixed and the
product owns them. There is deliberately no resolver here that takes an
arbitrary role name: a selected package resolves its own roles itself, so no
shared layer stands between any package and the run's configuration.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import TYPE_CHECKING

from engine.builtin.aibuildai.programs.score import ScoreLaunch, ScoreProgram
from engine.capability import ExecutionCapability

if TYPE_CHECKING:
    from config import AgentConfig
    from engine.paths import RunPaths
    from engine.work_unit.base import WorkUnit


def _resolved(
    configs: "AgentConfig",
    unit_type: "type[WorkUnit]",
    *,
    gpus: int | tuple[int, ...] = 0,
) -> ExecutionCapability:
    """What one of the product's own fixed children may spend."""
    kind, name = unit_type.kind, unit_type.name
    if name is None:
        raise AssertionError(f"{unit_type.__name__} declares no configured name")
    time_config = configs.work_unit_time_for(kind, name)
    limit = configs.resources.work_unit.limit_for(kind, name)
    return ExecutionCapability(
        wall_clock_seconds=time_config.cap_seconds(
            expected_minutes=None,
            pipeline_minutes=configs.run.budget.wall_clock_minutes,
        ),
        cpu_max_cores=limit.cpu_max_cores,
        memory_max_gb=float(limit.memory_max_gb),
        gpus=gpus,
        retry=time_config.retry,
    )


@dataclass(frozen=True)
class DurationBudgetRule:
    """One child's wall clock when the child's own work states how long it takes.

    The whole rule is resolved at startup and travels on the Search's immutable
    Input. At the spawn site the Search applies it to a typed runtime fact --
    a Coder's ``expected_full_training_minutes`` -- and gets the capability
    without reading configuration or asking any resolver. ``slack_fraction`` is
    None when the operator asked for a fixed or pipeline-wide budget instead,
    and then the expected duration changes nothing."""

    base: ExecutionCapability
    slack_fraction: float | None
    minimum_minutes: float
    maximum_minutes: float

    @property
    def uses_expected_duration(self) -> bool:
        """Whether a child's own expected duration decides its wall clock."""
        return self.slack_fraction is not None

    def capability(
        self, expected_minutes: float, *, gpus: int | tuple[int, ...] = 0
    ) -> ExecutionCapability:
        """This child's capability for one expected duration."""
        if self.slack_fraction is None:
            return self.base.model_copy(update={"gpus": gpus})
        if expected_minutes <= 0:
            raise ValueError("an expected budget requires a positive duration")
        proposed = expected_minutes * (1 + self.slack_fraction)
        minutes = max(self.minimum_minutes, min(proposed, self.maximum_minutes))
        return self.base.model_copy(
            update={"wall_clock_seconds": minutes * 60.0, "gpus": gpus}
        )


def score_launch(configs: "AgentConfig", run_paths: "RunPaths") -> ScoreLaunch:
    """Bind this run's grading for the selected package's immutable Input.

    Score is a fixed product role with a fixed job, so the product settles both
    halves of it here: the frozen program the run grades with, and what one
    Score run may spend. A search method receives the binding and never names
    either half."""
    return ScoreLaunch(
        score_program_path=run_paths.score_program_path,
        capability=_resolved(configs, ScoreProgram),
    )


@dataclass(frozen=True)
class ProductCapabilities:
    """What each fixed AIBuildAI product role may spend.

    A role that a configuration switches off (``enabled_in`` is False: the
    Submitter under ``submission.max_versions`` 1, the Writer under
    ``writer.enable`` false) has a None entry, so its ``work_units`` time entry
    is required exactly when ``validate_config`` requires it, and never for a
    role the run never launches. A ``_review`` entry is None when this
    configuration composes no reviewer for that role, which is the same answer
    the role's own frozen ``reviewer_capability`` gives at runtime."""

    setup: ExecutionCapability
    setup_review: ExecutionCapability | None
    submitter: ExecutionCapability | None
    finalizer: ExecutionCapability
    writer: ExecutionCapability | None
    writer_review: ExecutionCapability | None


def _product_reviewer(
    configs: "AgentConfig", producer: "type[WorkUnit]"
) -> ExecutionCapability | None:
    """What a fixed product role's own semantic review may spend, when it has one."""
    reviewer = producer.semantic_reviewer()  # pyright: ignore[reportAttributeAccessIssue] -- every fixed product role here is an Agent.
    if reviewer is None or not producer.composes_reviewer(configs):  # pyright: ignore[reportAttributeAccessIssue] -- same.
        return None
    return _resolved(configs, reviewer)


def product_capabilities(configs: "AgentConfig") -> ProductCapabilities:
    """Resolve the fixed product roles' capabilities for one run."""
    from engine.builtin.aibuildai.agents.finalizer.agent import FinalizerAgent
    from engine.builtin.aibuildai.agents.setup.agent import SetupAgent
    from engine.builtin.aibuildai.agents.submitter.agent import SubmitterAgent
    from engine.builtin.aibuildai.agents.writer.agent import WriterAgent

    submitter_on = SubmitterAgent.enabled_in(configs)
    writer_on = WriterAgent.enabled_in(configs)
    return ProductCapabilities(
        setup=_resolved(configs, SetupAgent),
        setup_review=_product_reviewer(configs, SetupAgent),
        submitter=_resolved(configs, SubmitterAgent) if submitter_on else None,
        finalizer=_resolved(configs, FinalizerAgent),
        writer=_resolved(configs, WriterAgent) if writer_on else None,
        writer_review=_product_reviewer(configs, WriterAgent) if writer_on else None,
    )


def granted(capability: ExecutionCapability | None, role: str) -> ExecutionCapability:
    """The capability of a role this run launches; a switched-off role has none."""
    if capability is None:
        raise AssertionError(
            f"the {role} role is switched off in this run and has no capability to spend"
        )
    return capability

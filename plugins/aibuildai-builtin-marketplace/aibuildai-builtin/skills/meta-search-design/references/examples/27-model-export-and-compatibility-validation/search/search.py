"""Plan one bounded export, run it, validate every environment in parallel, review; repeat or stop."""

from __future__ import annotations

from engine.durable_execution import Handle
from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import (
    CompatibilityReport,
    PackagingReviewInput,
    PackagingStrategyInput,
    PackagingStrategyOutput,
    RoundRecord,
)
from .agents.export import ExportAgent, ValidationAgent
from .agents.io import ExportInput, ValidationInput
from .agents.reviewer import PackagingReviewAgent
from .agents.strategist import PackagingStrategyAgent
from .io import ExportCompatibilitySearchInput


def _plan_problems(
    plan: PackagingStrategyOutput, search_input: ExportCompatibilitySearchInput
) -> tuple[str, ...]:
    problems: list[str] = []
    if plan.target_profile not in search_input.offered_targets:
        problems.append(f"target_profile {plan.target_profile} is not offered")
    if plan.precision_profile not in search_input.offered_precision_profiles:
        problems.append(f"precision_profile {plan.precision_profile} is not offered")
    if plan.shape_profile not in search_input.offered_shape_profiles:
        problems.append(f"shape_profile {plan.shape_profile} is not offered")
    if set(plan.converter_options) - set(search_input.offered_converter_options):
        problems.append("plan uses a converter option outside the offered set")
    if set(plan.compatibility_profiles) - set(search_input.offered_compatibility_profiles):
        problems.append("plan schedules a compatibility profile outside the offered set")
    if plan.tolerance_profile not in search_input.offered_tolerance_profiles:
        problems.append(f"tolerance_profile {plan.tolerance_profile} is not offered")
    return tuple(problems)


def _hard_gate_passed(mandatory: tuple[str, ...], reports: tuple[CompatibilityReport, ...]) -> bool:
    passed = {report.environment_profile for report in reports if report.hard_passed}
    return all(profile in passed for profile in mandatory)


class ExportCompatibilitySearch(Search[ExportCompatibilitySearchInput, SearchOutput]):
    """The source checkpoint never changes; only the plan and the export artifact do."""

    async def explore(self) -> SearchOutput | Failure:
        prior_rounds: list[RoundRecord] = []
        reviewed_by: tuple[Handle, ...] = ()

        for round_index in range(1, self.input.max_rounds + 1):
            rounds_remaining = self.input.max_rounds - round_index
            strategist = PackagingStrategyAgent(
                input=PackagingStrategyInput(
                    objective=self.input.objective,
                    source_checkpoint_path=self.input.source_checkpoint_path,
                    offered_targets=self.input.offered_targets,
                    offered_precision_profiles=self.input.offered_precision_profiles,
                    offered_shape_profiles=self.input.offered_shape_profiles,
                    offered_converter_options=self.input.offered_converter_options,
                    offered_compatibility_profiles=self.input.offered_compatibility_profiles,
                    offered_tolerance_profiles=self.input.offered_tolerance_profiles,
                    prior_rounds=tuple(prior_rounds),
                ),
            )
            strategist_handle = await self.ctx.spawn(
                strategist.run,
                "Start the assigned work.",
                upstream=reviewed_by,
                read=(task_data(), *(h.files() for h in reviewed_by),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.strategy_wall_clock_seconds,
                    gpus=0,
                ),
            )
            plan = await strategist_handle.result()

            problems = _plan_problems(plan, self.input)
            if problems:
                return Failure(kind=FailureKind.PERMANENT, reason="; ".join(problems))

            export_agent = ExportAgent(
                input=ExportInput(
                    objective=self.input.objective,
                    source_checkpoint_path=self.input.source_checkpoint_path,
                    toolchain_dir=self.input.toolchain_dir,
                    target_profile=plan.target_profile,
                    precision_profile=plan.precision_profile,
                    shape_profile=plan.shape_profile,
                    converter_options=plan.converter_options,
                ),
            )
            export_handle = await self.ctx.spawn(
                export_agent.run,
                "Start the assigned work.",
                upstream=(strategist_handle,),
                capture_failure=True,
                read=(task_data(), strategist_handle.files(),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.export_wall_clock_seconds, gpus=1
                ),
            )
            export_outcome = await export_handle.result()

            reports: list[CompatibilityReport] = []
            failures: list[str] = []
            handles = []

            if isinstance(export_outcome, Failure):
                failures.append(f"export: {export_outcome.reason}")
            else:
                # One fresh validation role per environment, all at once; each is
                # handed the exported path and digest, so none can read anything
                # but the artifact the export role committed.
                handles = [
                    await self.ctx.spawn(
                        ValidationAgent(
                            input=ValidationInput(
                                objective=self.input.objective,
                                toolchain_dir=self.input.toolchain_dir,
                                artifact_dir=export_outcome.artifact_dir,
                                artifact_digest=export_outcome.artifact_digest,
                                environment_profile=environment_profile,
                                tolerance_profile=plan.tolerance_profile,
                            ),
                        ).run,
                        "Start the assigned work.",
                        upstream=(export_handle,),
                        capture_failure=True,
                        read=(task_data(), export_handle.files(),),
                        capability=ExecutionCapability(
                            wall_clock_seconds=self.input.validation_wall_clock_seconds,
                            gpus=0 if environment_profile.startswith("cpu") else 1,
                        ),
                    )
                    for environment_profile in plan.compatibility_profiles
                ]
                await self.ctx.wait(handles)
                for environment_profile, handle in zip(plan.compatibility_profiles, handles, strict=True):
                    outcome = await handle.result()
                    if isinstance(outcome, Failure):
                        failures.append(f"{environment_profile}: {outcome.reason}")
                    else:
                        reports.append(
                            CompatibilityReport(
                                environment_profile=outcome.environment_profile,
                                loaded=outcome.loaded,
                                hard_passed=outcome.hard_passed,
                                maximum_relative_error=outcome.maximum_relative_error,
                            )
                        )

            hard_gate_passed = _hard_gate_passed(self.input.mandatory_environment_profiles, tuple(reports))

            reviewer = PackagingReviewAgent(
                input=PackagingReviewInput(
                    objective=self.input.objective,
                    target_profile=plan.target_profile,
                    compatibility_reports=tuple(reports),
                    compatibility_failures=tuple(failures),
                    mandatory_environment_profiles=self.input.mandatory_environment_profiles,
                    rounds_remaining=rounds_remaining,
                ),
            )
            verdict_handle = await self.ctx.spawn(
                reviewer.run,
                "Start the assigned work.",
                upstream=(strategist_handle, export_handle, *handles),
                read=(
task_data(),
strategist_handle.files(),
export_handle.files(),
*(h.files() for h in handles),
),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.review_wall_clock_seconds,
                    gpus=0,
                ),
            )
            verdict = await verdict_handle.result()
            reviewed_by = (verdict_handle,)

            if verdict.accepted and hard_gate_passed and not isinstance(export_outcome, Failure):
                return SearchOutput(
                    output_dir=export_outcome.artifact_dir,
                    score=verdict.score,
                )
            prior_rounds.append(
                RoundRecord(target_profile=plan.target_profile, accepted=False, reason=verdict.reason)
            )
            if not verdict.retry or rounds_remaining == 0:
                return Failure(kind=FailureKind.NO_OUTPUT, reason=verdict.reason)

        return Failure(
            kind=FailureKind.NO_OUTPUT,
            reason=f"export rounds exhausted at {self.input.max_rounds} without an accepted package",
        )

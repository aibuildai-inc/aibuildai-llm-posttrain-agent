"""Freeze one benchmark plan, run every target, let the analyst interpret the evidence."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.analyst import ErrorAnalysisAgent
from .agents.io import (
    ErrorAnalysisInput,
    EvaluationFailureRecord,
    EvaluationPlanningInput,
    EvaluationPlanOutput,
    EvaluationRecord,
)
from .agents.planner import EvaluationPlanningAgent
from .io import BenchmarkAnalysisSearchInput
from .programs.benchmark import BenchmarkInput, BenchmarkOutput, BenchmarkProgram


class BenchmarkAnalysisSearch(Search[BenchmarkAnalysisSearchInput, SearchOutput]):
    """The plan is frozen before any Program starts; a running Program never revises it."""

    def _plan_problems(self, plan: EvaluationPlanOutput) -> tuple[str, ...]:
        problems = []
        if len(plan.targets) > self.input.max_evaluations:
            problems.append(f"plan proposes {len(plan.targets)} evaluations; at most {self.input.max_evaluations}")
        evaluation_ids = [target.evaluation_id for target in plan.targets]
        if len(set(evaluation_ids)) != len(evaluation_ids):
            problems.append("plan reuses an evaluation_id")
        if set(plan.requested_slices) - set(self.input.offered_slices):
            problems.append("plan requests a slice outside the offered set")
        candidate_paths = {candidate.candidate_id: candidate.candidate_path for candidate in self.input.candidates}
        benchmark_ids = {profile.benchmark_id for profile in self.input.offered_benchmarks}
        for target in plan.targets:
            if target.candidate_id not in candidate_paths:
                problems.append(f"target {target.evaluation_id} names unknown candidate {target.candidate_id}")
            elif target.candidate_path != candidate_paths[target.candidate_id]:
                problems.append(f"target {target.evaluation_id} rewrote the path of candidate {target.candidate_id}")
            if target.benchmark_id not in benchmark_ids:
                problems.append(f"target {target.evaluation_id} names unknown benchmark {target.benchmark_id}")
        return tuple(problems)

    async def explore(self) -> SearchOutput | Failure:
        planner = EvaluationPlanningAgent(
            input=EvaluationPlanningInput(
                objective=self.input.objective,
                candidates=self.input.candidates,
                offered_benchmarks=self.input.offered_benchmarks,
                offered_slices=self.input.offered_slices,
                max_evaluations=self.input.max_evaluations,
            ),
        )
        planner_handle = await self.ctx.spawn(
            planner.run, "Start the assigned work.", read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.planner_wall_clock_seconds, gpus=0),
        )
        plan = await planner_handle.result()
        planned_by = (planner_handle,)

        problems = self._plan_problems(plan)
        if problems:
            return Failure(kind=FailureKind.PERMANENT, reason="; ".join(problems))

        profiles = {profile.benchmark_id: profile for profile in self.input.offered_benchmarks}
        handles = {}
        for target in plan.targets:
            profile = profiles[target.benchmark_id]
            program = BenchmarkProgram(
                input=BenchmarkInput(
                    evaluation_id=target.evaluation_id,
                    candidate_id=target.candidate_id,
                    candidate_path=target.candidate_path,
                    benchmark_id=target.benchmark_id,
                    dataset_path=profile.dataset_path,
                    seed=target.seed,
                    repetitions=target.repetitions,
                    requested_slices=plan.requested_slices,
                    required_examples=profile.required_examples,
                ),
            )
            handles[target.evaluation_id] = await self.ctx.spawn(
                program.run,
                upstream=planned_by,
                capture_failure=True,
                read=(task_data(), *(h.files() for h in planned_by),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.benchmark_wall_clock_seconds,
                    gpus=0,
                ),
            )
        await self.ctx.wait(set(handles.values()))

        outputs: dict[str, BenchmarkOutput] = {}
        evaluations: list[EvaluationRecord] = []
        failures: list[EvaluationFailureRecord] = []
        for target in plan.targets:
            outcome = await handles[target.evaluation_id].result()
            if isinstance(outcome, Failure):
                failures.append(
                    EvaluationFailureRecord(
                        evaluation_id=target.evaluation_id,
                        candidate_id=target.candidate_id,
                        benchmark_id=target.benchmark_id,
                        expected_examples=profiles[target.benchmark_id].required_examples,
                        reason=outcome.reason,
                    )
                )
                continue
            outputs[target.evaluation_id] = outcome
            evaluations.append(
                EvaluationRecord(
                    evaluation_id=outcome.evaluation_id,
                    candidate_id=outcome.candidate_id,
                    benchmark_id=outcome.benchmark_id,
                    primary_score=outcome.primary_score,
                    metric_components=outcome.metric_components,
                    slices=outcome.slices,
                    evaluated_examples=outcome.evaluated_examples,
                    expected_examples=outcome.expected_examples,
                    report_path=outcome.report_path,
                    predictions_path=outcome.predictions_path,
                )
            )

        if len(outputs) < self.input.min_successful:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=(
                    f"{len(outputs)} of {len(plan.targets)} evaluations produced valid evidence; "
                    f"at least {self.input.min_successful} required"
                ),
            )

        analyst = ErrorAnalysisAgent(
            input=ErrorAnalysisInput(
                objective=self.input.objective,
                primary_metric=plan.primary_metric,
                metric_direction=plan.metric_direction,
                evaluations=tuple(evaluations),
                failures=tuple(failures),
            ),
        )
        analysis_handle = await self.ctx.spawn(
            analyst.run, "Start the assigned work.", upstream=tuple(handles.values()),
            read=(task_data(), *(h.files() for h in handles.values())),
            capability=ExecutionCapability(wall_clock_seconds=self.input.analyst_wall_clock_seconds, gpus=0),
        )
        analysis = await analysis_handle.result()

        if analysis.selected_evaluation_id not in outputs:
            return Failure(
                kind=FailureKind.UNEXPECTED,
                reason="analysis selected an unknown or failed evaluation",
            )
        selected = outputs[analysis.selected_evaluation_id]

        return SearchOutput(
            output_dir=selected.artifact_dir,
            score=selected.primary_score,
            components=selected.metric_components,
        )

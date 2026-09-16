"""Plan a wave, run its assignments in parallel, extend the evidence, repeat or finalize."""

from __future__ import annotations

from engine.durable_execution import Handle
from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import (
    Assignment,
    AssignmentFailure,
    AssignmentResult,
    OrchestratorInput,
    OrchestratorOutput,
    Role,
    SynthesizerInput,
    WorkerInput,
    WorkerOutput,
)
from .agents.orchestrator import OrchestratorAgent
from .agents.synthesizer import SynthesizerAgent
from .agents.worker import WorkerAgent
from .io import OrchestratorWorkersSearchInput

AVAILABLE_ROLES: tuple[Role, ...] = ("data_check", "config_check", "eval_check")


class OrchestratorWorkersSearch(Search[OrchestratorWorkersSearchInput, SearchOutput]):
    """The orchestrator plans; workers execute one bounded assignment each."""

    def _validate_wave(
        self,
        wave: int,
        assignments: tuple[Assignment, ...],
        known_ids: set[str],
        remaining_budget: int,
    ) -> str | None:
        if len(assignments) > self.input.max_assignments_per_wave:
            return f"wave {wave} proposes {len(assignments)} assignments; at most {self.input.max_assignments_per_wave}"
        if len(assignments) > remaining_budget:
            return f"wave {wave} proposes {len(assignments)} assignments; only {remaining_budget} remain in budget"
        ids = [assignment.assignment_id for assignment in assignments]
        if len(set(ids)) != len(ids) or known_ids & set(ids):
            return f"wave {wave} reuses an assignment_id"
        return None

    async def explore(self) -> SearchOutput | Failure:
        completed: list[AssignmentResult] = []
        failed: list[AssignmentFailure] = []
        orchestrator = OrchestratorAgent(
            input=OrchestratorInput(
                objective=self.input.objective,
                repo_dir=self.input.repo_dir,
                available_roles=AVAILABLE_ROLES,
            ),
        )
        request = "Plan the first wave."
        worked: tuple[Handle[WorkerOutput | Failure], ...] = ()
        orchestrator_handle: Handle[OrchestratorOutput] | None = None

        for wave in range(1, self.input.max_waves + 1):
            total_so_far = len(completed) + len(failed)
            remaining_budget = self.input.max_total_assignments - total_so_far
            wave_handle = await self.ctx.spawn(
                orchestrator.run,
                f"{request} Wave {wave}; {self.input.max_waves - wave} wave(s) remain after it. At most {min(self.input.max_assignments_per_wave, remaining_budget)} assignment(s) this wave, {remaining_budget} in total budget.",
                upstream=worked, read=(task_data(), *(h.files() for h in worked),),
                capability=ExecutionCapability(
                wall_clock_seconds=self.input.orchestrator_wall_clock_seconds,
                gpus=0,
            ),
            )
            orchestrator_handle = wave_handle
            plan = await wave_handle.result()

            if plan.ready_to_finalize or not plan.assignments:
                break

            known_ids = {result.assignment_id for result in completed} | {
                failure.assignment_id for failure in failed
            }
            problem = self._validate_wave(wave, plan.assignments, known_ids, remaining_budget)
            if problem is not None:
                return Failure(kind=FailureKind.UNEXPECTED, reason=problem)

            handles = {
                assignment.assignment_id: await self.ctx.spawn(
                    WorkerAgent(
                        input=WorkerInput(
                            assignment_id=assignment.assignment_id,
                            role=assignment.role,
                            task_objective=assignment.task_objective,
                            repo_dir=self.input.repo_dir,
                        ),
                    ).run,
                    "Complete the assigned check and report your finding.",
                    upstream=(wave_handle,),
                    capture_failure=True, read=(task_data(), wave_handle.files(),),
                    capability=ExecutionCapability(
                            wall_clock_seconds=self.input.worker_wall_clock_seconds,
                            gpus=0,
                        ),
                )
                for assignment in plan.assignments
            }
            await self.ctx.wait(list(handles.values()))
            worked = tuple(handles.values())

            for assignment in plan.assignments:
                outcome = await handles[assignment.assignment_id].result()
                if isinstance(outcome, Failure):
                    failed.append(
                        AssignmentFailure(
                            assignment_id=assignment.assignment_id,
                            role=assignment.role,
                            reason=outcome.reason,
                        )
                    )
                else:
                    completed.append(
                        AssignmentResult(
                            assignment_id=assignment.assignment_id,
                            role=assignment.role,
                            finding=outcome.finding,
                            evidence_path=outcome.evidence_path,
                        )
                    )
            request = "Plan the next wave from the worker results now in the run."

        if not completed:
            return Failure(kind=FailureKind.NO_OUTPUT, reason="no worker assignment reached a finding")

        synthesizer = SynthesizerAgent(
            input=SynthesizerInput(
                objective=self.input.objective,
                completed=tuple(completed),
                failed=tuple(failed),
            ),
        )
        if orchestrator_handle is None:
            raise AssertionError("no orchestration wave ran")
        synthesizer_handle = await self.ctx.spawn(
            synthesizer.run,
            "Synthesize the wave results into a root-cause report.",
            upstream=(orchestrator_handle,), read=(task_data(), orchestrator_handle.files(),),
            capability=ExecutionCapability(
                wall_clock_seconds=self.input.synthesizer_wall_clock_seconds,
                gpus=0,
            ),
        )
        report = await synthesizer_handle.result()
        return SearchOutput(
            output_dir=report.output_dir,
            score=report.confidence,
        )

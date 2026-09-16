"""One worker action, one iterator decision, repeat; stop, fail, or exhaust the budget."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import IteratorInput, ObservationSummary, WorkItem, WorkerInput
from .agents.iterator import IteratorAgent
from .agents.worker import WorkerAgent
from .io import WorkerIteratorSearchInput


class WorkerIteratorSearch(Search[WorkerIteratorSearchInput, SearchOutput]):
    """The next action changes category or target; the iterator alone decides it."""

    async def explore(self) -> SearchOutput | Failure:
        iterator = IteratorAgent(
            input=IteratorInput(
                objective=self.input.objective,
                available_work_kinds=self.input.available_work_kinds,
            ),
        )
        seen_ids: set[str] = set()
        next_work = WorkItem(
            work_id="w1",
            kind=self.input.available_work_kinds[0],
            target=self.input.data_dir,
            rationale="No observation yet; start with the first available diagnostic action.",
        )
        previous_iterator: tuple[object, ...] = ()

        for iteration_index in range(self.input.max_iterations):
            work = next_work
            seen_ids.add(work.work_id)

            worker = WorkerAgent(
                input=WorkerInput(
                    objective=self.input.objective,
                    data_dir=self.input.data_dir,
                    work=work,
                ),
            )
            worker_handle = await self.ctx.spawn(
                worker.run,
                "Perform the assigned work item.",
                upstream=previous_iterator,
                read=(task_data(), *(h.files() for h in previous_iterator),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.worker_wall_clock_seconds, gpus=0
                ),
            )
            outcome = await worker_handle.result()
            observation = ObservationSummary(
                work=work, finding=outcome.finding, artifact_ref=outcome.artifact_ref
            )

            iterator_handle = await self.ctx.spawn(
                iterator.run,
                f"Iteration {iteration_index}, {self.input.max_iterations - iteration_index - 1} left after this one. The worker reports:\n\n{observation.model_dump_json(indent=2)}",
                upstream=(worker_handle,),
                read=(task_data(), worker_handle.files(),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.iterator_wall_clock_seconds,
                    gpus=0,
                ),
            )
            directive = await iterator_handle.result()
            previous_iterator = (iterator_handle,)

            if directive.directive == "stop":
                if directive.report_dir is None or directive.confidence is None:
                    return Failure(
                        kind=FailureKind.UNEXPECTED,
                        reason="iterator stopped without both report_dir and confidence",
                    )
                return SearchOutput(
                    output_dir=directive.report_dir,
                    score=directive.confidence,
                )

            if directive.directive == "fail":
                if directive.stop_reason is None:
                    return Failure(kind=FailureKind.UNEXPECTED, reason="iterator failed without a stop_reason")
                return Failure(kind=FailureKind.NO_OUTPUT, reason=directive.stop_reason)

            if directive.directive != "continue" or directive.next_work is None:
                return Failure(
                    kind=FailureKind.UNEXPECTED,
                    reason="iterator returned an unusable directive",
                )

            if directive.next_work.work_id in seen_ids:
                return Failure(
                    kind=FailureKind.UNEXPECTED,
                    reason=f"iterator repeated work_id {directive.next_work.work_id}",
                )
            next_work = directive.next_work

        return Failure(
            kind=FailureKind.NO_OUTPUT,
            reason=f"worker-iterator exhausted {self.input.max_iterations} iterations without a diagnosis",
        )

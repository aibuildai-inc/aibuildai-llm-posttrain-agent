"""Design once, train every configuration in parallel, select deterministically."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.designer import DesignerAgent
from .agents.io import DesignerInput
from .io import BatchTrainingSearchInput
from .programs.train_config import TrainConfigInput, TrainConfigOutput, TrainConfigProgram


class BatchTrainingSearch(Search[BatchTrainingSearchInput, SearchOutput]):
    """The designer finishes before any Program starts; no Program revises itself."""

    async def explore(self) -> SearchOutput | Failure:
        designer = DesignerAgent(
            input=DesignerInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                max_configurations=self.input.max_configurations,
            ),
        )
        designer_handle = await self.ctx.spawn(
            designer.run, "Start the assigned work.", read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.designer_wall_clock_seconds, gpus=0),
        )
        batch = await designer_handle.result()

        handles = [
            await self.ctx.spawn(
                TrainConfigProgram(
                    input=TrainConfigInput(
                        spec_name=spec.name,
                        config_json=spec.config_json,
                        source_dir=batch.source_dir,
                        python_path=batch.python_path,
                        data_dir=self.input.data_dir,
                        metric_name=batch.metric_name,
                    ),
                ).run,
                upstream=(designer_handle,),
                capture_failure=True,
                read=(task_data(), designer_handle.files(),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.training_wall_clock_seconds,
                    gpus=1,
                ),
            )
            for spec in batch.specs
        ]
        await self.ctx.wait(handles)

        successes: list[TrainConfigOutput] = []
        for handle in handles:
            outcome = await handle.result()
            if not isinstance(outcome, Failure):
                successes.append(outcome)
        if len(successes) < self.input.min_successful:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=(
                    f"{len(successes)} of {len(handles)} configurations reached a "
                    f"checkpoint; at least {self.input.min_successful} required"
                ),
            )
        best = max(successes, key=lambda item: item.metric)
        return SearchOutput(
            output_dir=best.output_dir,
            score=best.metric,
            components=((batch.metric_name, best.metric),),
        )

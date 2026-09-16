"""Produce, evaluate, revise; stop on acceptance or at the round budget."""

from __future__ import annotations

from engine.failure import Failure
from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.evaluator import EvaluatorAgent
from .agents.io import EvaluatorInput, ProducerInput
from .agents.producer import ProducerAgent
from .io import EvaluatorOptimizerSearchInput


class EvaluatorOptimizerSearch(Search[EvaluatorOptimizerSearchInput, SearchOutput]):
    """One lineage, refined in place; the rubric never changes between rounds."""

    async def explore(self) -> SearchOutput | Failure:
        producer = ProducerAgent(
            input=ProducerInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
            ),
        )
        request = "Produce the first version."
        judged_by: tuple[Handle, ...] = ()
        round_index = 0

        while True:
            round_index += 1
            draft_handle = await self.ctx.spawn(
                producer.run,
                request,
                upstream=judged_by,
                read=(task_data(), *(h.files() for h in judged_by),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.producer_wall_clock_seconds,
                    gpus=0,
                ),
            )
            version = await draft_handle.result()

            evaluator = EvaluatorAgent(
                input=EvaluatorInput(
                    objective=self.input.objective,
                    rubric=self.input.rubric,
                    output_dir=version.output_dir,
                    score=version.score,
                    changes=version.changes,
                ),
            )
            evaluator_handle = await self.ctx.spawn(
                evaluator.run,
                "Review this version against the rubric.",
                upstream=(draft_handle,),
                read=(task_data(), draft_handle.files(),),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.evaluator_wall_clock_seconds,
                    gpus=0,
                ),
            )
            review = await evaluator_handle.result()
            if review.accepted or round_index == self.input.max_rounds:
                return SearchOutput(
                    output_dir=version.output_dir,
                    score=version.score,
                )
            request = f"The rubric review rejected that version:\n\n{review.feedback}"
            judged_by = (evaluator_handle,)

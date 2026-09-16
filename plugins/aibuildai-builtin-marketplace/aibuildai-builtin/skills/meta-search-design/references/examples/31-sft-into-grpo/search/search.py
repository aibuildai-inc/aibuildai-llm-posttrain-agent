"""SFT teaches the output format; GRPO then concentrates mass onto the verifiable reward."""

from __future__ import annotations

from engine.capability import ExecutionCapability
from engine.failure import Failure
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import TrainerInput
from .agents.trainer import TrainerAgent
from .io import SFTIntoGRPOSearchInput


class SFTIntoGRPOSearch(Search[SFTIntoGRPOSearchInput, SearchOutput]):
    """One trainer role, called twice on the same identity: its conversation carries the lineage."""

    async def explore(self) -> SearchOutput | Failure:
        trainer = TrainerAgent(
            input=TrainerInput(objective=self.input.objective, data_dir=self.input.data_dir),
        )
        # One declaration per call. Both stages of this identity happen to want
        # the same card and the same clock, so they are written once and passed
        # twice; a Search that wanted a shorter second stage would simply write
        # a second declaration here.
        stage = ExecutionCapability(
            wall_clock_seconds=self.input.trainer_wall_clock_seconds, gpus=1
        )
        sft_handle = await self.ctx.spawn(
            trainer.run, "Run stage sft.", read=(task_data(),), capability=stage
        )
        sft = await sft_handle.result()

        budget = await self.budget.snapshot()
        if budget.wall_clock_remaining_s < self.input.grpo_round_seconds:
            return SearchOutput(
                output_dir=sft.checkpoint_dir,
                score=sft.score,
                components=((sft.metric_name, sft.score),),
            )

        # The same identity: the second call continues the conversation that
        # wrote the scripts and trained the SFT checkpoint, so nothing is handed
        # over by Input. A GRPO that fails leaves the SFT result standing.
        grpo_handle = await self.ctx.spawn(
            trainer.run, "Run stage grpo.", upstream=(sft_handle,), capture_failure=True,
            read=(task_data(), sft_handle.files(),), capability=stage,
        )
        grpo = await grpo_handle.result()
        if isinstance(grpo, Failure) or grpo.score <= sft.score:
            best_dir, best_value = sft.checkpoint_dir, sft.score
        else:
            best_dir, best_value = grpo.checkpoint_dir, grpo.score

        return SearchOutput(
            output_dir=best_dir,
            score=best_value,
            components=((sft.metric_name, best_value),),
        )

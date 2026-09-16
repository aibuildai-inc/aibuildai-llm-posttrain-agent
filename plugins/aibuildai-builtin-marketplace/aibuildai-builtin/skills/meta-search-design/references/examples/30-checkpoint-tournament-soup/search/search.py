"""Recon once, then one round role per round while the budget pays; keep the best winner."""

from __future__ import annotations

from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.failure import Failure, FailureKind
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import ReconInput, RoundInput
from .agents.recon import ReconAgent
from .agents.round import RoundAgent
from .io import CheckpointTournamentSearchInput


class CheckpointTournamentSearch(Search[CheckpointTournamentSearchInput, SearchOutput]):
    """Each round is one Agent on the card; the Search only loops, gates on the budget, and keeps the best."""

    async def explore(self) -> SearchOutput | Failure:
        recon = ReconAgent(
            input=ReconInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                eval_script=self.input.eval_script,
            ),
        )
        recon_handle = await self.ctx.spawn(
            recon.run, "Start the assigned work.", read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.recon_wall_clock_seconds, gpus=0),
        )
        data_spec = await recon_handle.result()

        base_model = data_spec.base_model_or_checkpoint
        upstream: tuple[Handle, ...] = (recon_handle,)
        best_score = -float("inf")
        best_dir = ""

        for round_idx in range(1, self.input.max_rounds + 1):
            budget = await self.budget.snapshot()
            if budget.wall_clock_remaining_s < self.input.min_round_seconds:
                break

            # A fresh identity per round: the round needs no earlier round's
            # conversation, only the winning checkpoint it is told to start from.
            round_agent = RoundAgent(
                input=RoundInput(
                    objective=self.input.objective,
                    data_dir=self.input.data_dir,
                    eval_script=self.input.eval_script,
                    soup_script=self.input.soup_script,
                    sft_module=self.input.sft_module,
                    round_index=round_idx,
                    base_model_or_checkpoint=base_model,
                    prepared_data_dir=data_spec.prepared_data_dir,
                    train_config_json=data_spec.train_config_json,
                    save_steps=data_spec.save_steps,
                    soup_k=self.input.soup_k,
                ),
            )
            round_handle = await self.ctx.spawn(
                round_agent.run,
                "Start the assigned work.",
                upstream=upstream,
                capture_failure=True,
                # Two different facts, declared separately. The round WAITS
                # for the round before it, which is what upstream says. It
                # READS the training data Recon prepared -- every round, not
                # just the first -- and the checkpoint it must continue from.
                # Deriving the grant from upstream would have taken Recon's
                # data away the moment round 2 began.
                read=(
                    task_data(),
                    recon_handle.files(),
                    *(h.files() for h in upstream),
                ),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.round_wall_clock_seconds, gpus=1
                ),
            )
            outcome = await round_handle.result()
            upstream = (round_handle,)
            if isinstance(outcome, Failure):
                if not best_dir:
                    return outcome
                break

            if outcome.best_score > best_score:
                best_score, best_dir = outcome.best_score, outcome.best_dir
            base_model = outcome.best_dir

        if not best_dir:
            return Failure(kind=FailureKind.NO_OUTPUT, reason="no round produced a checkpoint")
        return SearchOutput(output_dir=best_dir, score=best_score)

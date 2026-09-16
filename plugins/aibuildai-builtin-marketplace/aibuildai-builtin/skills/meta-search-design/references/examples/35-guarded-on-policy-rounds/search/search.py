"""Seed once, then sample-train-guard rounds where a losing round is undone."""

from __future__ import annotations

from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.failure import Failure, FailureKind
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import RoundInput, SeedInput
from .agents.round import RoundAgent
from .agents.seed import SeedAgent
from .io import GuardedOnPolicySearchInput

_OTHER_METHOD = {"rft": "opd", "opd": "rft"}


class GuardedOnPolicySearch(Search[GuardedOnPolicySearchInput, SearchOutput]):
    """Each round is one Agent on the card; the Search holds the standing checkpoint and the guard."""

    async def explore(self) -> SearchOutput | Failure:
        seed = SeedAgent(
            input=SeedInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                eval_script=self.input.eval_script,
                sample_script=self.input.sample_script,
            ),
        )
        seed_handle = await self.ctx.spawn(
            seed.run, "Start the assigned work.", read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.seed_wall_clock_seconds, gpus=1),
        )
        data_spec = await seed_handle.result()

        # The guard compares against a measured number: the seed role scored
        # its own checkpoint, so round 1 has something to lose against.
        accepted_dir = data_spec.seed_checkpoint
        accepted_score = data_spec.seed_score
        # The producer of the checkpoint that is currently standing. A
        # rejected round does not advance it, so this is not always the round
        # this one waits for, and the files it must read are not always the
        # files of its upstream.
        accepted_handle: Handle = seed_handle
        upstream: tuple[Handle, ...] = (seed_handle,)
        forced_method = ""
        rejected_streak = 0
        accepted_rounds = 0
        history: list[str] = []

        for round_idx in range(1, self.input.max_rounds + 1):
            budget = await self.budget.snapshot()
            if budget.wall_clock_remaining_s < self.input.min_round_seconds:
                break

            # A fresh identity per round, always pointed at the STANDING
            # checkpoint: a rejected round never advances it.
            round_agent = RoundAgent(
                input=RoundInput(
                    objective=self.input.objective,
                    data_dir=self.input.data_dir,
                    eval_script=self.input.eval_script,
                    sample_script=self.input.sample_script,
                    rft_module=self.input.rft_module,
                    opd_module=self.input.opd_module,
                    teacher_model=self.input.teacher_model,
                    round_index=round_idx,
                    parent_checkpoint=accepted_dir,
                    prompt_set_path=data_spec.prompt_set_path,
                    train_config_json=data_spec.train_config_json,
                    samples_per_prompt=self.input.samples_per_prompt,
                    rft_min_yield=self.input.rft_min_yield,
                    forced_method=forced_method,
                ),
            )
            round_handle = await self.ctx.spawn(
                round_agent.run,
                "Start the assigned work.",
                upstream=upstream,
                capture_failure=True,
                # The seed's prompt set and train config, which every round
                # samples, and the standing checkpoint this round starts
                # from. The round it waits for may be a rejected one whose
                # checkpoint nothing continues.
                read=(task_data(), seed_handle.files(), accepted_handle.files()),
                capability=ExecutionCapability(
                    wall_clock_seconds=self.input.round_wall_clock_seconds, gpus=1
                ),
            )
            outcome = await round_handle.result()
            upstream = (round_handle,)
            if isinstance(outcome, Failure):
                history.append(f"round {round_idx}: failed: {outcome.reason}")
                rejected_streak += 1
                if rejected_streak >= self.input.max_rejected_rounds:
                    break
                continue

            # The guard. An on-policy round trains the model on its own output,
            # so a round that loses has taught the student its own mistakes.
            # Rejecting it is the whole point of the pattern.
            gain = outcome.score - accepted_score
            if gain > self.input.guard_margin:
                accepted_dir = outcome.checkpoint_dir
                accepted_score = outcome.score
                accepted_handle = round_handle
                accepted_rounds += 1
                rejected_streak = 0
                forced_method = ""
                history.append(f"round {round_idx} ({outcome.method}): accepted, +{gain:.4f}")
                continue

            rejected_streak += 1
            history.append(f"round {round_idx} ({outcome.method}): rejected, {gain:+.4f}")
            # One retry with the other method before giving up: a yield just
            # under or over the threshold picks a method the round then
            # disproves. The retry samples the standing checkpoint again.
            forced_method = _OTHER_METHOD[outcome.method] if not forced_method else ""
            if rejected_streak >= self.input.max_rejected_rounds:
                break

        if accepted_rounds == 0:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=(
                    "no on-policy round beat the seed checkpoint by more than the guard "
                    f"margin: {'; '.join(history) or 'no round completed'}"
                ),
            )
        return SearchOutput(output_dir=accepted_dir, score=accepted_score)

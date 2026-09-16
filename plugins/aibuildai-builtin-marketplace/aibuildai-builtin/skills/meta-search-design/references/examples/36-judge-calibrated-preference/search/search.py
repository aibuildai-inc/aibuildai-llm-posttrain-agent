"""Measure the judge first, then let the measurement pick the preference method."""

from __future__ import annotations

from engine.capability import ExecutionCapability
from engine.failure import Failure
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.io import CalibrationInput, ParentInput, RankingInput, TrainerInput
from .agents.judge import CalibrationAgent, RankingAgent
from .agents.parent import ParentAgent
from .agents.trainer import TrainerAgent
from .io import JudgeCalibratedPreferenceSearchInput


class JudgeCalibratedPreferenceSearch(Search[JudgeCalibratedPreferenceSearchInput, SearchOutput]):
    """Calibrate the judge against the grader, branch on the measured margin, soup with the parent."""

    async def explore(self) -> SearchOutput | Failure:
        parent = ParentAgent(
            input=ParentInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                eval_script=self.input.eval_script,
                judge_script=self.input.judge_script,
            ),
        )
        parent_handle = await self.ctx.spawn(
            parent.run, "Start the assigned work.", read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.parent_wall_clock_seconds, gpus=1),
        )
        data_spec = await parent_handle.result()

        def keep_parent() -> SearchOutput:
            """Return the supervised parent unchanged; the branch did not beat it."""
            return SearchOutput(
                output_dir=data_spec.parent_checkpoint,
                score=data_spec.parent_score,
            )

        # A fresh judge role that sees only the graded pairs; the Search's call
        # order, not a shared state, is what guarantees ranking waits for it.
        calibration = CalibrationAgent(
            input=CalibrationInput(
                objective=self.input.objective,
                judge_script=self.input.judge_script,
                judge_model=self.input.judge_model,
                calibration_pairs_path=data_spec.calibration_pairs_path,
                rubric_note=data_spec.rubric_note,
            ),
        )
        calibrate_handle = await self.ctx.spawn(
            calibration.run, "Start the assigned work.", upstream=(parent_handle,), capture_failure=True,
            read=(task_data(), parent_handle.files(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.judge_wall_clock_seconds, gpus=1),
        )
        calibrate_outcome = await calibrate_handle.result()
        if isinstance(calibrate_outcome, Failure):
            return keep_parent()

        # The gate. A judge that disagrees with the grader does not produce a
        # weaker training signal, it produces a signal pointing somewhere else,
        # so the branch is abandoned rather than run with a lower learning rate.
        if calibrate_outcome.agreement < self.input.min_judge_agreement:
            return keep_parent()

        budget = await self.budget.snapshot()
        if budget.wall_clock_remaining_s < self.input.min_branch_seconds:
            return keep_parent()

        ranking = RankingAgent(
            input=RankingInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                sample_script=self.input.sample_script,
                judge_script=self.input.judge_script,
                judge_model=self.input.judge_model,
                parent_checkpoint=data_spec.parent_checkpoint,
                prompt_set_path=data_spec.prompt_set_path,
                candidates_per_prompt=self.input.candidates_per_prompt,
                rubric_note=data_spec.rubric_note,
            ),
        )
        rank_handle = await self.ctx.spawn(
            ranking.run, "Start the assigned work.", upstream=(calibrate_handle,), capture_failure=True,
            # The prompt set and the parent checkpoint belong to the parent
            # that prepared them; the calibration this waits for produced
            # neither.
            read=(task_data(), parent_handle.files(), calibrate_handle.files()),
            capability=ExecutionCapability(wall_clock_seconds=self.input.judge_wall_clock_seconds, gpus=1),
        )
        rank_outcome = await rank_handle.result()
        if isinstance(rank_outcome, Failure):
            return keep_parent()
        if rank_outcome.pair_count < self.input.min_pairs:
            return keep_parent()

        # The branch. A wide measured gap means the two sides really differ, and
        # a pairwise loss can read that difference; a narrow one means the judge
        # is close to indifferent, so only the top-ranked completion is kept.
        if rank_outcome.mean_margin >= self.input.dpo_min_margin:
            method, ranked_path = "dpo", rank_outcome.pairs_path
        else:
            method, ranked_path = "raft", rank_outcome.best_only_path

        trainer = TrainerAgent(
            input=TrainerInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                eval_script=self.input.eval_script,
                soup_script=self.input.soup_script,
                dpo_module=self.input.dpo_module,
                raft_module=self.input.raft_module,
                method=method,
                parent_checkpoint=data_spec.parent_checkpoint,
                ranked_path=ranked_path,
                train_config_json=data_spec.train_config_json,
            ),
        )
        train_handle = await self.ctx.spawn(
            trainer.run, "Start the assigned work.", upstream=(rank_handle,), capture_failure=True,
            # It trains FROM the parent checkpoint and soups back into it, and
            # reads the ranked pairs the ranking wrote: two producers, two
            # grants. Waiting on the ranking exposes neither.
            read=(task_data(), parent_handle.files(), rank_handle.files()),
            capability=ExecutionCapability(wall_clock_seconds=self.input.trainer_wall_clock_seconds, gpus=1),
        )
        train_outcome = await train_handle.result()
        if isinstance(train_outcome, Failure):
            return keep_parent()
        if not train_outcome.candidates:
            return keep_parent()

        best = max(train_outcome.candidates, key=lambda item: item.score)
        if best.score <= data_spec.parent_score:
            return keep_parent()
        return SearchOutput(output_dir=best.checkpoint_dir, score=best.score)

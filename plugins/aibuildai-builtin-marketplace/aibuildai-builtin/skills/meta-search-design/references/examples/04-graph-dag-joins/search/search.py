"""Two independent sources join, one branch reuses a source, two sinks join again."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.baseline import BaselineAgent
from .agents.decision import FinalDecisionAgent
from .agents.io import (
    BaselineInput,
    DecisionInput,
    ProfileInput,
    QualityCheckInput,
    SelectorInput,
    TrainerInput,
)
from .agents.profile import ProfileAgent
from .agents.quality import QualityCheckAgent
from .agents.selector import SelectorAgent
from .agents.trainer import TrainerAgent
from .io import GraphDagJoinsSearchInput


class GraphDagJoinsSearch(Search[GraphDagJoinsSearchInput, SearchOutput]):
    """The business graph is a DAG; runtime ownership stays one tree under this Search."""

    async def explore(self) -> SearchOutput | Failure:
        profile_agent = ProfileAgent(
            input=ProfileInput(objective=self.input.objective, data_dir=self.input.data_dir),
        )
        profile_handle = await self.ctx.spawn(
            profile_agent.run, "Start the assigned work.", read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.profile_wall_clock_seconds, gpus=0),
        )

        baseline_agent = BaselineAgent(
            input=BaselineInput(objective=self.input.objective, data_dir=self.input.data_dir),
        )
        baseline_handle = await self.ctx.spawn(
            baseline_agent.run, "Start the assigned work.", read=(task_data(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.baseline_wall_clock_seconds, gpus=0),
        )

        await self.ctx.wait({profile_handle, baseline_handle})
        profile = await profile_handle.result()
        baseline = await baseline_handle.result()

        selector = SelectorAgent(
            input=SelectorInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                profile_summary=profile.summary,
                flagged_columns=profile.flagged_columns,
                baseline_score=baseline.score,
                feature_importance_summary=baseline.feature_importance_summary,
            ),
        )
        selector_handle = await self.ctx.spawn(
            selector.run,
            "Start the assigned work.",
            upstream=(profile_handle, baseline_handle),
            read=(task_data(), profile_handle.files(), baseline_handle.files(),),
            capability=ExecutionCapability(
                wall_clock_seconds=self.input.selection_wall_clock_seconds, gpus=0
            ),
        )

        quality_check = QualityCheckAgent(
            input=QualityCheckInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                column_count=profile.column_count,
                flagged_columns=profile.flagged_columns,
                profile_summary=profile.summary,
            ),
        )
        quality_handle = await self.ctx.spawn(
            quality_check.run, "Start the assigned work.", upstream=(profile_handle,),
            read=(task_data(), profile_handle.files(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.quality_wall_clock_seconds, gpus=0),
        )

        await self.ctx.wait({selector_handle})
        selected = await selector_handle.result()

        trainer = TrainerAgent(
            input=TrainerInput(
                objective=self.input.objective,
                data_dir=self.input.data_dir,
                source_dir=selected.source_dir,
                config_json=selected.selected_features_json,
                metric_name=selected.metric_name,
            ),
        )
        tuned_handle = await self.ctx.spawn(
            trainer.run, "Start the assigned work.", upstream=(selector_handle,),
            read=(task_data(), selector_handle.files(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.tuning_wall_clock_seconds, gpus=1),
        )

        await self.ctx.wait({quality_handle, tuned_handle})
        quality = await quality_handle.result()
        tuned = await tuned_handle.result()

        decision = FinalDecisionAgent(
            input=DecisionInput(
                objective=self.input.objective,
                concerns=quality.concerns,
                blocking=quality.blocking,
                metric=tuned.metric,
                metric_name=selected.metric_name,
            ),
        )
        final_handle = await self.ctx.spawn(
            decision.run, "Start the assigned work.", upstream=(quality_handle, tuned_handle),
            read=(task_data(), quality_handle.files(), tuned_handle.files(),),
            capability=ExecutionCapability(wall_clock_seconds=self.input.decision_wall_clock_seconds, gpus=0),
        )
        final = await final_handle.result()
        if not final.accepted:
            return Failure(kind=FailureKind.NO_OUTPUT, reason=final.summary)

        return SearchOutput(
            output_dir=tuned.output_dir,
            score=tuned.metric,
            components=((selected.metric_name, tuned.metric),),
        )

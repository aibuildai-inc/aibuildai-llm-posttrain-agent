"""Expand, evaluate, and prune a fixed-width frontier of pipeline states."""

from __future__ import annotations

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.evaluator import EvaluatorAgent
from .agents.expander import ExpanderAgent
from .agents.io import BeamState, EvaluatorInput, ExpanderInput
from .io import BeamSearchInput


class BeamSearch(Search[BeamSearchInput, SearchOutput]):
    """A depth-synchronous frontier of at most beam_width surviving states."""

    def _output(self, entry: tuple[float, BeamState]) -> SearchOutput:
        score, state = entry
        return SearchOutput(
            output_dir=state.dataset_dir,
            score=score,
        )

    async def explore(self) -> SearchOutput | Failure:
        root = BeamState(state_id="root", depth=0, steps=(), dataset_dir=self.input.data_dir)
        beam: tuple[BeamState, ...] = (root,)
        beam_producers: dict[str, tuple[Handle, ...]] = {root.state_id: ()}
        best_complete: tuple[float, BeamState] | None = None

        for depth in range(self.input.max_depth):
            parent_by_id = {parent.state_id: parent for parent in beam}
            expanders = {
                parent.state_id: ExpanderAgent(
                    input=ExpanderInput(
                        objective=self.input.objective,
                        data_dir=self.input.data_dir,
                        steps_so_far=parent.steps,
                        dataset_dir=parent.dataset_dir,
                        max_children=self.input.branching_factor,
                        remaining_depth=self.input.max_depth - depth,
                    ),
                )
                for parent in beam
            }
            expansion_handles = {
                state_id: await self.ctx.spawn(
                    expander.run,
                    "Expand this state.",
                    upstream=beam_producers[state_id],
                    capture_failure=True, read=(task_data(), *(h.files() for h in beam_producers[state_id])),
                    capability=ExecutionCapability(
                        wall_clock_seconds=self.input.expander_wall_clock_seconds,
                        gpus=0,
                    ),
                )
                for state_id, expander in expanders.items()
            }
            await self.ctx.wait(list(expansion_handles.values()))

            proposed: list[BeamState] = []
            state_producers: dict[str, tuple[Handle, ...]] = {}
            for parent_id, handle in expansion_handles.items():
                outcome = await handle.result()
                if isinstance(outcome, Failure):
                    continue
                parent = parent_by_id[parent_id]
                for index, child in enumerate(outcome.children):
                    state = BeamState(
                        state_id=f"{parent_id}.{depth + 1}.{index}",
                        depth=depth + 1,
                        steps=(*parent.steps, child.step_name),
                        dataset_dir=child.dataset_dir,
                    )
                    proposed.append(state)
                    state_producers[state.state_id] = (handle,)

            if not proposed:
                break

            evaluators = {
                state.state_id: EvaluatorAgent(
                    input=EvaluatorInput(
                        objective=self.input.objective,
                        steps=state.steps,
                        dataset_dir=state.dataset_dir,
                        depth=state.depth,
                        max_depth=self.input.max_depth,
                    ),
                )
                for state in proposed
            }
            evaluation_handles = {
                state_id: await self.ctx.spawn(
                    evaluator.run,
                    "Evaluate this state.",
                    upstream=state_producers[state_id],
                    capture_failure=True, read=(task_data(), *(h.files() for h in state_producers[state_id])),
                    capability=ExecutionCapability(
                        wall_clock_seconds=self.input.evaluator_wall_clock_seconds,
                        gpus=0,
                    ),
                )
                for state_id, evaluator in evaluators.items()
            }
            await self.ctx.wait(list(evaluation_handles.values()))

            scored: list[tuple[float, BeamState]] = []
            for state in proposed:
                outcome = await evaluation_handles[state.state_id].result()
                if isinstance(outcome, Failure) or not outcome.viable:
                    continue
                scored.append((outcome.score, state))
                if outcome.complete and (best_complete is None or outcome.score > best_complete[0]):
                    best_complete = (outcome.score, state)

            if best_complete is not None and best_complete[0] >= self.input.acceptance_threshold:
                return self._output(best_complete)

            ranked = sorted(scored, key=lambda item: (item[0], item[1].state_id), reverse=True)
            beam = tuple(state for _, state in ranked[: self.input.beam_width])
            beam_producers = {
                state.state_id: (evaluation_handles[state.state_id],) for state in beam
            }
            if not beam:
                break

        if best_complete is not None:
            return self._output(best_complete)
        return Failure(
            kind=FailureKind.NO_OUTPUT,
            reason="beam search exhausted its depth and frontier without a viable candidate",
        )

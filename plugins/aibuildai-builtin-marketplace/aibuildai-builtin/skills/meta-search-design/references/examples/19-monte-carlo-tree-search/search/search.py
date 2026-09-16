"""Select through the tree by UCT, expand, run one transition, sample a value, back it up."""

from __future__ import annotations

import dataclasses
import itertools
import math

from engine.failure import Failure, FailureKind
from engine.capability import ExecutionCapability
from engine.durable_execution import Handle
from engine.builtin.aibuildai import SearchOutput, task_data
from engine.search import Search

from .agents.expander import ExpanderAgent
from .agents.io import ActionSpec, ExpanderInput, RolloutInput, TrialInput, TrialOutput
from .agents.rollout import RolloutAgent
from .agents.trial import TrialAgent
from .io import MCTSSearchInput

ROOT_ID = "n0"


@dataclasses.dataclass
class TreeNode:
    """One tree-local statistics record; the durable authority stays the child Outputs."""

    parent_id: str | None
    depth: int
    decisions: tuple[str, ...]
    result: TrialOutput | None
    expandable: bool
    state_producer: tuple[Handle, ...]
    expander: tuple[Handle, ...]
    untried_actions: tuple[ActionSpec, ...]
    child_ids: tuple[str, ...]
    visits: int
    value_sum: float


def _uct_score(*, parent_visits: int, child_visits: int, child_value_sum: float, exploration_constant: float) -> float:
    if child_visits == 0:
        return math.inf
    mean = child_value_sum / child_visits
    bonus = exploration_constant * math.sqrt(math.log(parent_visits) / child_visits)
    return mean + bonus


def _best_child(tree: dict[str, TreeNode], node: TreeNode, exploration_constant: float) -> str:
    parent_visits = max(node.visits, 1)
    return max(
        node.child_ids,
        key=lambda child_id: _uct_score(
            parent_visits=parent_visits,
            child_visits=tree[child_id].visits,
            child_value_sum=tree[child_id].value_sum,
            exploration_constant=exploration_constant,
        ),
    )


def _select_path(tree: dict[str, TreeNode], exploration_constant: float) -> tuple[str, ...]:
    path = [ROOT_ID]
    current = ROOT_ID
    while True:
        node = tree[current]
        if node.result is not None or not node.expandable or node.untried_actions or not node.child_ids:
            return tuple(path)
        current = _best_child(tree, node, exploration_constant)
        path.append(current)


def _backup(tree: dict[str, TreeNode], path: tuple[str, ...], reward: float, discount: float) -> None:
    factor = 1.0
    for node_id in reversed(path):
        node = tree[node_id]
        node.visits += 1
        node.value_sum += factor * reward
        factor *= discount


def _mark_dead_end(tree: dict[str, TreeNode], path: tuple[str, ...], leaf: TreeNode, discount: float) -> None:
    leaf.expandable = False
    _backup(tree, path, 0.0, discount)


class MonteCarloTreeSearch(Search[MCTSSearchInput, SearchOutput]):
    """Every hyperparameter decision is one action; 'evaluate' ends a path and scores it."""

    async def explore(self) -> SearchOutput | Failure:
        tree: dict[str, TreeNode] = {
            ROOT_ID: TreeNode(
                parent_id=None,
                depth=0,
                decisions=(),
                result=None,
                expandable=True,
                state_producer=(),
                expander=(),
                untried_actions=(),
                child_ids=(),
                visits=0,
                value_sum=0.0,
            )
        }
        node_ids = itertools.count(1)
        best: tuple[str, TrialOutput] | None = None
        child_failures: list[str] = []
        iterations_run = 0

        for iteration in range(self.input.max_simulations):
            iterations_run = iteration + 1
            path = _select_path(tree, self.input.exploration_constant)
            leaf_id = path[-1]
            leaf = tree[leaf_id]

            if leaf.result is not None:
                _backup(tree, path, leaf.result.score, self.input.discount)
                continue
            if not leaf.expandable:
                _backup(tree, path, 0.0, self.input.discount)
                continue

            if not leaf.untried_actions:
                if leaf.depth >= self.input.max_depth:
                    _mark_dead_end(tree, path, leaf, self.input.discount)
                    continue
                expander = ExpanderAgent(
                    input=ExpanderInput(
                        objective=self.input.objective,
                        decisions=leaf.decisions,
                        branching_factor=self.input.branching_factor,
                    ),
                )
                expander_handle = await self.ctx.spawn(
                    expander.run,
                    "Propose the next actions.",
                    upstream=leaf.state_producer,
                    read=(task_data(),),
                    capability=ExecutionCapability(
                wall_clock_seconds=self.input.expander_wall_clock_seconds,
                        gpus=0,
                    ),
                )
                proposal = await expander_handle.result()
                if not proposal.actions:
                    _mark_dead_end(tree, path, leaf, self.input.discount)
                    continue
                leaf.untried_actions = proposal.actions
                leaf.expander = (expander_handle,)
            action, *remaining = leaf.untried_actions
            leaf.untried_actions = tuple(remaining)

            # A decision that keeps the path open needs no execution at all: the
            # child state is the parent's decisions plus this one. Only 'evaluate'
            # runs anything, and that is one trial role on the card.
            if action.kind == "evaluate":
                trial = TrialAgent(
                    input=TrialInput(
                        objective=self.input.objective,
                        decisions=leaf.decisions,
                        data_dir=self.input.data_dir,
                        source_dir=self.input.source_dir,
                        metric_name=self.input.metric_name,
                    ),
                )
                trial_handle = await self.ctx.spawn(
                    trial.run,
                    "Start the assigned work.",
                    upstream=(*leaf.state_producer, *leaf.expander),
                    capture_failure=True,
                    read=(task_data(),),
                    capability=ExecutionCapability(
                        wall_clock_seconds=self.input.trial_wall_clock_seconds, gpus=1
                    ),
                )
                outcome = await trial_handle.result()
                if isinstance(outcome, Failure):
                    child_failures.append(f"trial from {leaf_id}: {outcome.reason}")
                    continue
                child_decisions, child_result, producer = leaf.decisions, outcome, (trial_handle,)
            else:
                child_decisions, child_result, producer = (*leaf.decisions, action.detail), None, leaf.expander
            child_id = f"n{next(node_ids)}"
            child = TreeNode(
                parent_id=leaf_id,
                depth=leaf.depth + 1,
                decisions=child_decisions,
                result=child_result,
                expandable=True,
                state_producer=producer,
                expander=(),
                untried_actions=(),
                child_ids=(),
                visits=0,
                value_sum=0.0,
            )
            tree[child_id] = child
            leaf.child_ids = (*leaf.child_ids, child_id)
            path = (*path, child_id)
            if child.result is not None:
                reward = child.result.score
            else:
                rollout = RolloutAgent(
                    input=RolloutInput(
                        objective=self.input.objective,
                        decisions=child.decisions,
                        depth=child.depth,
                        max_depth=self.input.max_depth,
                    ),
                )
                rollout_handle = await self.ctx.spawn(
                    rollout.run,
                    "Estimate the value of this path.",
                    upstream=child.state_producer,
                    capture_failure=True,
                    read=(task_data(),),
                    capability=ExecutionCapability(
                        wall_clock_seconds=self.input.rollout_wall_clock_seconds,
                        gpus=0,
                    ),
                )
                estimate = await rollout_handle.result()
                if isinstance(estimate, Failure):
                    child_failures.append(f"rollout at {child_id}: {estimate.reason}")
                    continue
                reward = estimate.value
            _backup(tree, path, reward, self.input.discount)
            if child.result is not None and (best is None or child.result.score > best[1].score):
                best = (child_id, child.result)

        if best is None:
            return Failure(
                kind=FailureKind.NO_OUTPUT,
                reason=f"{iterations_run} simulations found no terminal candidate"
                + ("; child failures: " + "; ".join(child_failures) if child_failures else ""),
            )

        best_node_id, best_result = best
        return SearchOutput(
            output_dir=best_result.output_dir,
            score=best_result.score,
        )

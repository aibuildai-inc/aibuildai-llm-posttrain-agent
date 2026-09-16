"""The one base for facts stored on RunState."""

from __future__ import annotations

from dataclasses import dataclass, field
from typing import TYPE_CHECKING, final

from eventsourcing.domain import Aggregate

if TYPE_CHECKING:
    from engine.durable_execution import DurableExecution
    from engine.run_state import RunState
    from engine.builtin.aibuildai.search import AIBuildAISearch, Search
    from engine.work_unit.agent.base import Agent
    from engine.work_unit.base import WorkUnit


@dataclass(frozen=True)
class Event(Aggregate.Event):
    """A fact accepted by one RunState aggregate.

    The eventsourcing library calls ``apply`` on every stored fact as it folds. Each fact overrides it to name its owner and call one owner verb; the business rule lives on the owner, never here."""

    target: str | None = field(kw_only=True)
    ts: float = field(kw_only=True)
    live: bool = field(kw_only=True)
    workflow_path: str | None = field(kw_only=True)
    workflow_step: int | None = field(kw_only=True)

    @property
    def seq(self) -> int:
        return self.originator_version

    def apply(self, state: "RunState") -> None:
        """Fail loud for a fact no owner claimed.

        A new fact that forgets its own ``apply`` crashes the first time it is folded, live and on replay alike, instead of being silently skipped."""
        raise AssertionError(f"{type(self).__name__} has no owner")

    @final
    def mutate(self, aggregate: "RunState | None") -> "RunState":
        """Apply one fact and advance the durable Run Time floor."""
        state = super().mutate(aggregate)
        if state is None:
            raise AssertionError("an Event cannot create RunState")
        state.settle_clock(self.ts)
        return state

    def optional_unit(self, state: "RunState") -> "WorkUnit | None":
        """The WorkUnit this fact names, or None when it names the run itself."""
        if self.target is None:
            return None
        execution = state.execution_for(self.target)
        from engine.work_unit.base import WorkUnit

        if not isinstance(execution, WorkUnit):
            raise AssertionError(
                f"{type(self).__name__} targets {type(execution).__name__}, "
                "not a WorkUnit"
            )
        return execution

    def execution(self, state: "RunState") -> "DurableExecution":
        if self.target is None:
            raise AssertionError(f"{type(self).__name__} carries no execution target")
        return state.execution_for(self.target)

    def search(self, state: "RunState") -> "Search":
        from engine.search.base import Search

        execution = self.execution(state)
        if not isinstance(execution, Search):
            raise AssertionError(
                f"{type(self).__name__} targets {type(execution).__name__}, "
                "not a Search"
            )
        return execution

    def _product_search(self, state: "RunState") -> "AIBuildAISearch":
        from engine.builtin.aibuildai.search import AIBuildAISearch

        search = self.search(state)
        if not isinstance(search, AIBuildAISearch):
            raise AssertionError(f"{type(self).__name__} requires an AIBuildAISearch")
        return search

    def unit(self, state: "RunState") -> "WorkUnit":
        """The WorkUnit this fact names, which it must name."""
        unit = self.optional_unit(state)
        if unit is None:
            raise AssertionError(f"{type(self).__name__} carries no WorkUnit target")
        return unit

    def agent(self, state: "RunState") -> "Agent":
        """The Agent this fact names, which it must name."""
        return state.require_agent(self.unit(state))


@dataclass(frozen=True)
class UnitFact(Event):
    """A fact one WorkUnit accepts whole.

    ``apply`` is final: the unit's own ``accept`` is the entire application."""

    @final
    def apply(self, state: "RunState") -> None:
        self.unit(state).accept(self, self.ts)

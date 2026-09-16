"""The universal Search family.

Search is the recursively composable orchestration execution: an immutable
Input, one public ``run`` Action, ``ctx.spawn`` for the work it starts, the
Handles it keeps, and the typed Output it returns. Nothing here knows about
any product built on it.
"""

from __future__ import annotations

import asyncio
import logging
from abc import abstractmethod
from dataclasses import dataclass
from typing import TYPE_CHECKING, ClassVar, Generic, TypeVar, cast, final, overload

from pydantic import BaseModel, PrivateAttr

from engine.budget import BudgetReader, BudgetSnapshot, ExecutionBudgetSnapshot
from engine.capability import ExecutionCapability
from engine.durable_execution import (
    DurableExecution,
    ExecutionContext,
    Handle,
    action,
    action_output,
    latest_run_state,
    recorded_handle,
    run_clock,
    run_durable,
    run_host,
)
from engine.execution_output import ExecutionOutput, SuccessfulOutput
from engine.failure import Failure

if TYPE_CHECKING:
    pass

logger = logging.getLogger(__name__)

SearchInputT = TypeVar("SearchInputT", bound=BaseModel)
SearchSuccessT = TypeVar("SearchSuccessT", bound=SuccessfulOutput)
DelegatedT = TypeVar("DelegatedT", bound="DurableExecution")


@dataclass(frozen=True)
class RecordedAction(Generic[DelegatedT]):
    """One Action of one execution an owner started, as an observation saw it.

    A Search that delegates its scheduling -- to an Agent whose tools start
    the work, for one -- cannot hold the Handles and Outputs of children it
    did not itself spawn: the tool runs on the owner's Action workflow, and
    every workflow restores its own execution objects. This is what such a
    Search receives instead, and it is the whole of it: the child itself, what
    that one Action of it settled with, and a Handle naming that exact Action
    so a later Action can declare it as ``upstream``.

    ``output`` is None while the Action had not settled. ``handle`` is None
    for a child recorded before its first Attempt opened, which is a real
    durable state and not an impossible one: creating a child and starting its
    Action are two separate commits, so a process that dies between them
    leaves exactly that."""

    execution: DelegatedT
    output: "SuccessfulOutput | Failure | None"
    handle: "Handle[ExecutionOutput[bool]] | None"

    def require_handle(self) -> "Handle[ExecutionOutput[bool]]":
        """The Handle naming this Action, which every started Action has."""
        if self.handle is None:
            raise ValueError(
                f"{self.execution.path} was recorded before any Attempt of it "
                "opened, so it has no Action to name as upstream"
            )
        return self.handle


class Search(DurableExecution[SearchInputT], Generic[SearchInputT, SearchSuccessT]):
    """One recursively composable orchestration execution.

    What an author of a Search gets is its immutable ``input``, its ``ctx``,
    its replay-safe ``budget``, its ``capability`` and the facts of its own
    identity. That is the whole surface: this class has no member that
    returns the running root, the run's configuration, its journal state, its
    clock, its host controller, its event store or its Program environment.
    Those belong to the RUN, and the kernel reads them through the engine's
    own module functions, which no Definition inherits."""

    _intermediate: ClassVar[bool] = True

    # This root's workflow-body task (set by _run_scope / write_paper) and
    # the supervisor task joining its handle (set by run_durable); suspend()
    # wakes the joiner once the suspended root's task has fully unwound.
    _pipeline_task: "asyncio.Task[None] | None" = PrivateAttr(default=None)
    _joiner: "asyncio.Task[object] | None" = PrivateAttr(default=None)

    @classmethod
    def uid_name(cls) -> str:
        return "search"

    @classmethod
    def _check_action_capability(cls, capability: ExecutionCapability) -> None:
        """Refuse a physical resource on a Search Action, which runs no process.

        A Search decides what runs and waits for it; the cards, cores and
        memory belong to the WorkUnit Actions that hold a process open. There
        is no level here to write them on, so accepting them would record a
        limit nothing enforces and place cards nothing uses. Its own span and
        its own retry allowance stay: those bound the orchestration itself."""
        physical = tuple(
            name
            for name in ("cpu_max_cores", "memory_max_gb", "gpus")
            if getattr(capability, name) is not None
        )
        if physical:
            raise ValueError(
                f"{cls.__name__} orchestrates and runs no process of its own, "
                f"so its Action declares no {', '.join(physical)}. Declare "
                "physical resources on the WorkUnit Actions that use them."
            )

    @property
    def run_home(self) -> str:
        """The run home this Search writes under."""
        if self.parent_path is None:
            return latest_run_state().run_home
        return self.directory

    @action
    async def run(self) -> SearchSuccessT | Failure:
        """Run this Search: the one public Action every Search family exposes."""
        # Establish this Search's level in the run's resource tree before any
        # descendant can be placed below it. A generated plain Search claims
        # its level exactly as a built-in does.
        run_host().claim_level(self.path)
        return await self.explore()

    @final
    def _run_output(self) -> SearchSuccessT | Failure | None:
        """The settled result of this Search's ``run`` Action, if it settled."""
        return cast(
            "SearchSuccessT | Failure | None",
            None if not self.record.actions else action_output(self.record, 1),
        )

    def _after_event_saved(
        self,
        event: object,
        execution: "DurableExecution | None",
        *,
        event_ts: float,
        now: float,
    ) -> None:
        pass

    @abstractmethod
    async def explore(self) -> SearchSuccessT | Failure:
        """Build and run this Search's business graph."""

    @final
    def _own_target(
        self,
        target: "DurableExecution | Handle[ExecutionOutput[bool]]",
        *,
        what: str = "budget target",
    ) -> "tuple[DurableExecution, int | None]":
        """The recorded work this Search owns, and the exact Action meant.

        A Handle names one Action occurrence, so asking through it asks about
        that call; an execution object names the identity, so asking through
        it asks about its whole life. A Search may ask about work it started,
        itself included, and nothing else: another Search's execution answers
        a question the caller has no business asking, so it is refused before
        any observation is made. ``what`` names the refused thing the way the
        caller named it, so the reason says which question was asked."""
        run_state = latest_run_state()
        if isinstance(target, Handle):
            path, ordinal = target._path, target._ordinal
        else:
            path, ordinal = target._recorded_path(run_state, what=what), None
        if path != self.path and not path.startswith(f"{self.path}/"):
            raise ValueError(
                f"{what} {path} is outside {self.path}; a Search reads "
                "only the executions it owns"
            )
        return run_state.execution_for(path), ordinal

    @final
    async def delegated(
        self,
        owner: "DurableExecution",
        kind: "type[DelegatedT]",
        *,
        on: "ExecutionContext | None" = None,
    ) -> "tuple[RecordedAction[DelegatedT], ...]":
        """What one owner this Search delegated to has started, of one kind.

        ``owner`` is an execution this Search owns, named by the object the
        caller holds, and anything outside this Search's own subtree is
        refused before the observation is made. ``kind`` selects the children
        this algorithm asked that owner to start; the rest of the owner's own
        work is not this caller's business and is not returned.

        Use it where a Search cannot hold its own Handles because it did not
        spawn the children: the classic case is an Agent whose tools start the
        work, whose Action workflow is not this Search's. ``on`` names the
        Action the observation is journaled under, exactly as a budget
        observation does, and defaults to this Search's own.

        What comes back is one entry per recorded Action, in the order the
        journal kept them, so nothing here assumes how many times a child ran
        or which of its Actions is the interesting one."""
        owned, _ = self._own_target(owner, what="delegated owner")
        ctx = self.ctx if on is None else on
        seen = await ctx.step(_read_delegated, self, owned)
        state = latest_run_state()
        recorded: list[RecordedAction[DelegatedT]] = []
        for entry in seen:
            path = cast(str, entry["path"])
            execution = state.execution_for(path)
            if not isinstance(execution, kind):
                continue
            ordinal = cast("int | None", entry["ordinal"])
            recorded.append(
                RecordedAction(
                    execution,
                    action_output(state.records[path], ordinal)
                    if entry["settled"] and ordinal is not None
                    else None,
                    await recorded_handle(path, ordinal)
                    if entry["started"] and ordinal is not None
                    else None,
                )
            )
        return tuple(recorded)

    @property
    def budget(self) -> "SearchBudget":
        """The run's exploration budget, read through durable snapshots.

        Use ``await self.budget.snapshot()`` inside ``explore()`` to drive
        budget-bounded control flow, such as running another improvement round
        only while the remainder pays for it. Pass ``on=`` the running Action
        when the observation is taken from one, such as inside a tool of an
        Agent this Search declares."""
        return SearchBudget(self)


def _read_delegated(search: Search, owner: "DurableExecution") -> list[dict[str, object]]:
    """Read once every Action recorded under one owner, as plain addresses.

    What this returns is the whole checkpoint payload, and it is deliberately
    flat: a path, an ordinal, and whether that Action had opened and settled.
    No live execution, no workflow handle and no Output travels through the
    journal, so a replay returns the child set this position saw rather than
    today's larger one, and the runtime rebuilds the objects from the recorded
    addresses afterwards. An execution recorded with no Action at all is
    reported as itself, because a child created before its Action started is a
    durable state its owner may still have to count."""
    del search
    state = latest_run_state()
    seen: list[dict[str, object]] = []
    for child in state.children_of(owner.path):
        record = state.records[child.path]
        if not record.actions:
            seen.append(
                {"path": child.path, "ordinal": None, "started": False, "settled": False}
            )
            continue
        for ordinal in range(1, len(record.actions) + 1):
            action_record = record.action(ordinal)
            seen.append(
                {
                    "path": child.path,
                    "ordinal": ordinal,
                    "started": action_record.workflow_id is not None,
                    "settled": action_record.output is not None,
                }
            )
    return seen


def _read_budget_facts(search: Search) -> dict[str, object]:
    """Read the exploration budget once, from the accounting that enforces it."""
    del search
    return BudgetReader(run_clock(), latest_run_state()).exploration().model_dump()


def _read_execution_budget_facts(
    search: Search, target: "DurableExecution", ordinal: int | None
) -> dict[str, object]:
    """Read one owned Action or identity budget once, from that same accounting."""
    del search
    return (
        BudgetReader(run_clock(), latest_run_state())
        .execution(target, ordinal)
        .model_dump()
    )


class SearchBudget:
    """Replay-safe reader of budget facts for one Search.

    Each observation is journaled as one durable step, so a replay returns
    the recorded values at the same workflow position and authored control
    flow takes the same branch again."""

    def __init__(self, search: Search) -> None:
        self._search = search

    @overload
    async def snapshot(
        self, *, on: "ExecutionContext | None" = None
    ) -> BudgetSnapshot: ...

    @overload
    async def snapshot(
        self,
        target: "DurableExecution | Handle[ExecutionOutput[bool]]",
        *,
        on: "ExecutionContext | None" = None,
    ) -> ExecutionBudgetSnapshot: ...

    async def snapshot(
        self,
        target: "DurableExecution | Handle[ExecutionOutput[bool]] | None" = None,
        *,
        on: "ExecutionContext | None" = None,
    ) -> BudgetSnapshot | ExecutionBudgetSnapshot:
        """The exploration budget, one owned Action's, or one identity's.

        With no argument this returns the whole exploration's limit, used,
        and remaining facts. Given a Handle it returns what that one Action
        used and may still use; given a recorded identity it returns the same
        facts rolled up over every Action that identity has run.

        ``on`` names the Action this observation is journaled under, and
        defaults to this Search's own, which is the running Action while
        ``explore`` itself runs. Author code reached from another execution's
        running Action -- an Agent tool this Search declares, for one -- names
        that Action instead, because a step belongs to the workflow that is
        actually running, and asking a parked Search to record it raises
        rather than answering."""
        ctx = self._search.ctx if on is None else on
        if target is None:
            facts = await ctx.step(_read_budget_facts, self._search)
            return BudgetSnapshot.model_validate(facts)
        owned, ordinal = self._search._own_target(target)
        facts = await ctx.step(
            _read_execution_budget_facts, self._search, owned, ordinal
        )
        return ExecutionBudgetSnapshot.model_validate(facts)


async def run_search(
    search: Search[SearchInputT, SearchSuccessT],
) -> SearchSuccessT | Failure:
    """Run one already-constructed Search invocation and return what it produced."""
    return await run_durable(search)


"""Own Run Time and Local Time for one product run.

Run Time is business time: the sum of the run's active process epochs. Inside one epoch it advances with process monotonic time and does not pause for provider limits or waits; between epochs -- an explicit Pause, a recoverable Failure, a crash, machine downtime -- no budget is consumed. Each epoch starts from the durable elapsed floor the journal last settled (every recorded event and the periodic heartbeat settle it), so a crash loses at most the quiet tail after the last record. Local Time is one Action's own active span: a retry Attempt continues it, and a pause recorded for one Action is removed only from that Action. Summing an identity's Action spans gives its lifetime elapsed, which the configured role budget bounds; that total is a roll-up of the same spans and not a second clock.

Run Budget and Local Budget are limits, not clocks. ``Clock`` reads the Run Budget settled in the run record, each optional Local Budget in ``ExecutionCapability``, and each identity's configured lifetime total. The same run record settles the Work Unit time rules that make later Local Budgets. An edited config cannot add time to the current run. The Run Budget bounds only the exploration window: the span between the recorded exploration start and finish. Outside that window the Run Budget constrains nothing, and Setup, the Finalizer, and the later product tail are bounded by their own Local Budgets alone. The Clock starts before the fresh host GPU query, so startup work is Run Time. ``Clock`` gives relative seconds to MCP startup and calls, subprocess, and coroutine adapters at each actual launch after preparation. A value can only shrink and is no larger than the live effective remainder. A process monotonic timeout is short-lived. It is rebuilt from these facts and is never a time authority. At Clock expiry the driver starts no more cleanup work.
"""

from __future__ import annotations

import asyncio
import time
from contextlib import asynccontextmanager
from typing import TYPE_CHECKING, AsyncIterator, Callable

if TYPE_CHECKING:
    from engine.durable_execution import ActionRecord, DurableExecution
    from engine.run_state import RunState


class _LimitExpired(TimeoutError):
    """A private process timeout reached its current business limit."""


class Clock:
    """The sole source of business elapsed time and remaining time."""

    def __init__(
        self,
        state: "RunState",
        run_budget_s: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> None:
        self._state: "RunState | None" = state
        self._run_started_at_unix_s = state.run_started_at_unix_s
        self._run_budget_s = run_budget_s
        self._monotonic = monotonic
        self._process_started_monotonic_s = monotonic()
        # This epoch opens at the durable floor the journal last settled.
        # Offline time between epochs is not business time, so nothing here
        # reads the wall clock against the original start.
        self._process_started_run_s = state.durable_elapsed_floor_s
        self._limits: dict[tuple[str, int], asyncio.Timeout] = {}
        self._state_provider: "Callable[[], RunState | None] | None" = None

    @classmethod
    def before_run_open(
        cls,
        run_started_at_unix_s: float,
        run_budget_s: float,
        *,
        monotonic: Callable[[], float] = time.monotonic,
    ) -> "Clock":
        """Start the one Clock before the first journal commit."""
        clock = cls.__new__(cls)
        clock._state = None
        clock._run_budget_s = run_budget_s
        clock._monotonic = monotonic
        clock._process_started_monotonic_s = monotonic()
        clock._process_started_run_s = 0.0
        clock._run_started_at_unix_s = run_started_at_unix_s
        clock._limits = {}
        clock._state_provider = None
        return clock

    @classmethod
    def at_elapsed(
        cls,
        state: "RunState",
        run_budget_s: float,
        elapsed_s: float,
    ) -> "Clock":
        """A frozen Clock view pinned at one Run Time instant.

        For a rendered frame: no monotonic time passes, so every query
        answers as of ``elapsed_s``."""
        clock = cls(state, run_budget_s, monotonic=lambda: 0.0)
        clock._process_started_run_s = max(
            state.durable_elapsed_floor_s, elapsed_s
        )
        return clock

    def bind_run_state(self, state: "RunState") -> None:
        """Bind the first atomic journal state to this starting Clock."""
        if self._state is not None:
            raise AssertionError("Clock already has RunState")
        if state.run_started_at_unix_s != self._run_started_at_unix_s:
            raise AssertionError("RunState start does not match Clock start")
        self._state = state

    def bind_state_provider(
        self, provider: "Callable[[], RunState | None]"
    ) -> None:
        """Bind the task-local state selector owned by the caller."""
        if self._state_provider is not None:
            raise AssertionError("Clock already has a state provider")
        self._state_provider = provider

    def run_elapsed_s(self) -> float:
        """Return Run Time: settled prior epochs plus this epoch's monotonic time."""
        process_elapsed_s = max(
            0.0, self._monotonic() - self._process_started_monotonic_s
        )
        return self._process_started_run_s + process_elapsed_s

    def exploration_elapsed_s(self) -> float:
        """Return the Run Time the exploration window has consumed so far.

        Zero before exploration starts; frozen at the recorded finish once
        exploration is over, so later product-tail time never counts."""
        state = self._current_state()
        if state is None:
            return 0.0
        started_s, finished_s = state.exploration_window()
        if started_s is None:
            return 0.0
        end_s = finished_s if finished_s is not None else self.run_elapsed_s()
        return max(0.0, end_s - started_s)

    def exploration_remaining_s(self) -> float:
        """Return the settled Run Budget that remains inside the exploration window.

        Outside the window -- before exploration starts and after it finishes --
        the Run Budget constrains nothing, so this is infinite there and only
        Local Budgets bound the work."""
        state = self._current_state()
        if state is None:
            return float("inf")
        started_s, finished_s = state.exploration_window()
        if started_s is None or finished_s is not None:
            return float("inf")
        return max(0.0, self._run_budget_s - self.exploration_elapsed_s())

    def local_elapsed_s(
        self, execution: "DurableExecution", ordinal: int | None = None
    ) -> float:
        """Return the time one Action's Attempts really ran.

        Every Attempt of that Action counts, so a retry continues the span
        rather than starting a new one. Actions of the same identity are
        separate spans: their sum is this identity's lifetime elapsed, which
        is a roll-up and not a second clock. With no ``ordinal`` the running
        Action is meant, and an identity that is not running has no span."""
        return self._elapsed(self._actions(execution, ordinal))

    def _elapsed(self, actions: "tuple[ActionRecord, ...]") -> float:
        """The active time of a set of Actions, the running Attempt included."""
        now_s = self.run_elapsed_s()
        return sum(
            action.active_s
            + (
                0.0
                if action.attempt_started_at_s is None
                else max(0.0, now_s - action.attempt_started_at_s)
            )
            for action in actions
        )

    @staticmethod
    def _actions(
        execution: "DurableExecution", ordinal: int | None
    ) -> "tuple[ActionRecord, ...]":
        """The one Action a local reading is about, or none."""
        if ordinal is None:
            ordinal = None if execution._ctx is None else execution._ctx.ordinal
        if ordinal is None or ordinal > len(execution.record.actions):
            return ()
        return (execution.record.action(ordinal),)

    def local_remaining_s(
        self, execution: "DurableExecution", ordinal: int | None = None
    ) -> float:
        """Return the Local Budget that remains for one Action.

        Infinite where that Action declared no clock of its own, which does
        not make it unbounded: its identity's lifetime total and the Run still
        bound it, and ``effective_remaining_s`` is where they meet."""
        actions = self._actions(execution, ordinal)
        if not actions:
            return float("inf")
        budget_s = actions[0].capability.wall_clock_seconds
        if budget_s is None:
            return float("inf")
        return max(0.0, budget_s - self.local_elapsed_s(execution, ordinal))

    def identity_elapsed_s(self, execution: "DurableExecution") -> float:
        """Return the time every Action of one identity has really run.

        The roll-up of the same Action spans ``local_elapsed_s`` reads one at
        a time, through the same sum: one Action's span is counted once,
        whichever way it is asked for."""
        return self._elapsed(execution.record.actions)

    def identity_remaining_s(self, execution: "DurableExecution") -> float:
        """Return the lifetime budget one identity has left over all its Actions.

        A second call of the same identity spends what the first one left. A
        family that configures no lifetime returns infinite, and then only the
        Action's own declaration and the Run bound it."""
        total_s = execution.identity_wall_clock_seconds()
        if total_s is None:
            return float("inf")
        return max(0.0, total_s - self.identity_elapsed_s(execution))

    def effective_remaining_s(
        self, execution: "DurableExecution", ordinal: int | None = None
    ) -> float:
        """Return the smallest live Run or unpaused Local remainder for one Action of ``execution``: its running one, or the exact ``ordinal`` named."""
        remaining_s = self.exploration_remaining_s()
        state = self._current_state()
        if state is None:
            raise AssertionError("Clock has no RunState")
        current: DurableExecution | None = execution
        if ordinal is None:
            ordinal = None if execution._ctx is None else execution._ctx.ordinal
        while current is not None:
            # A paused Action stops its own Local Time and everything it runs
            # below it; a sibling Action's pause on the same identity is that
            # sibling's alone. The walk climbs the exact caller chain; an
            # identity that is not running climbs from its creating Action.
            action = None if ordinal is None else current.record.action(ordinal)
            if action is None or action.rate_limited_at_s is None:
                remaining_s = min(
                    remaining_s,
                    self.local_remaining_s(current, ordinal),
                    self.identity_remaining_s(current),
                )
            ordinal = (
                current.record.parent_action_ordinal
                if action is None
                else action.caller_ordinal
            )
            parent_path = current.record.parent_path
            current = None if parent_path is None else state.execution_for(parent_path)
        return remaining_s

    def _current_state(self) -> "RunState | None":
        """Use the current workflow position when code is replaying."""
        return (
            None if self._state_provider is None else self._state_provider()
        ) or self._state

    @asynccontextmanager
    async def limit(self, execution: "DurableExecution") -> AsyncIterator[None]:
        """Enforce the current relative limit on one running Action."""
        key = (execution.path, execution.ctx.ordinal)
        timeout = asyncio.timeout(None)
        try:
            async with timeout:
                if key in self._limits:
                    raise AssertionError(f"duplicate clock limit for {key}")
                self._limits[key] = timeout
                self.refresh(execution)
                try:
                    yield
                finally:
                    del self._limits[key]
        except TimeoutError:
            if timeout.expired():
                raise _LimitExpired from None
            raise

    def refresh(self, execution: "DurableExecution") -> None:
        """Rebuild the live timeouts of one identity's running Actions after a durable pause or resume fact, each from its own exact Action.

        An infinite remainder installs NO deadline. Two different states reach
        it, and they are not the same kind of thing.

        Ruled acceptable, with a reason: a paused Action. Pause time is not
        Local Time, so a paused Action contributes no Local remainder; outside
        the exploration window, where the Run Budget also contributes nothing,
        its timeout is None until it resumes. This is acceptable because a
        paused run still holds its GPU and is recoverable at any time while a
        terminated one is not. The
        pause is a journal fact, ``rate_limited_at_s`` on the Action; no
        output surface renders it, so an operator learns of it from the
        journal, not from a status. An operator who wants that patience
        bounded sets ``llm.retry_ceiling_s``, unset by default. Inside the
        exploration window the Run Budget still expires a paused Action,
        because Run Time ignores the pause; outside it, nothing does.

        Known and left alone, with no reason behind it: the root Search. The
        run-open in ``bootstrap.py`` builds its capability from the run's CPU,
        memory limits and never passes ``wall_clock_seconds``, so
        it has no Local Budget and contributes ``inf`` at every moment;
        outside the exploration window the Run Budget contributes ``inf``
        too, so the root's own timeout is None there, with no pause involved.
        Work that runs before exploration starts or after it finishes has no
        deadline of its own unless one is declared lower down. Nobody chose
        this; it falls out of the root having no Local Budget.

        Do not close either state by clamping the infinite remainder here:
        legitimate out-of-window work is bounded by its own Local Budget
        alone, and a clamp kills it silently."""
        for (path, ordinal), timeout in self._limits.items():
            if path != execution.path or timeout.expired():
                # An expired limit is already unwinding its Action; asyncio
                # refuses to reschedule it, and nothing here should either.
                continue
            remaining_s = self.effective_remaining_s(execution, ordinal)
            timeout.reschedule(
                None
                if remaining_s == float("inf")
                else asyncio.get_running_loop().time() + remaining_s
            )

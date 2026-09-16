"""The built-in ``resources`` MCP: three read-only tools.

Every Agent gets them, and each answers one question from the run's own live
state: who is running in this run right now and which host GPUs each of them
holds (``list_executions``), what the whole exploration has left
(``get_search_budget``), and what the calling Agent itself has used and may
still use (``get_my_budget``). The budget answers come from the same
``BudgetReader`` a Search reads through, so a tool result and a Search
snapshot of the same state agree. None of them writes, waits, or decides;
what a caller does with the answer is the caller's own call.

Each tool call and result travels the Agent's ordinary conversation stream.
"""

from __future__ import annotations

import json
from typing import TYPE_CHECKING

from mcp.server import Server
from mcp.types import CallToolResult, TextContent, Tool

from engine.durable_execution import latest_run_state, run_clock, run_host
from engine.budget import BudgetReader
from engine.mcp import tool_server
from engine.work_unit.base import WorkUnit

if TYPE_CHECKING:
    from engine.builtin.aibuildai.search import AIBuildAISearch

_COLUMNS = (
    "path",
    "kind",
    "name",
    "status",
    "elapsed",
    "budget",
    "gpus",
    "owner",
    "upstream",
    "directory",
    "session_id",
    "me",
)


def _hms(seconds: float | None) -> str:
    if seconds is None or seconds == float("inf"):
        return "-"
    whole = int(seconds)
    return f"{whole // 3600:d}:{whole % 3600 // 60:02d}:{whole % 60:02d}"


def executions_table(search: "AIBuildAISearch", caller_path: str) -> str:
    """One row per Action occurrence of every identity the run has recorded, live rows first."""
    rows: list[tuple[str, ...]] = []
    for record in latest_run_state().records.values():
        execution = record.execution
        # One row per Action occurrence: an identity's status is each exact
        # Action's own, never a summary stored on the identity.
        for action in record.actions:
            status = "running" if action.ended_at_s is None else "ended"
            gpus = (
                action.capability.gpu_indices
                if status == "running" and isinstance(execution, WorkUnit)
                else ()
            )
            rows.append(
                (
                    f"{record.path}#{action.ordinal}",
                    getattr(execution, "kind", None) or "-",
                    getattr(execution, "name", None) or getattr(execution, "kind", None) or "-",
                    status,
                    _hms(run_clock().local_elapsed_s(execution, action.ordinal)),
                    _hms(action.capability.wall_clock_seconds),
                    ",".join(str(index) for index in gpus) if gpus else "-",
                    execution.parent_path or "-",
                    ",".join(f"{path}#{ordinal}" for path, ordinal in action.upstream)
                    or "-",
                    latest_run_state().execution_directory(record.path),
                    getattr(execution, "session_id", None) or "-",
                    "*" if record.path == caller_path else "",
                )
            )
    rows.sort(key=lambda row: (row[3] != "running", row[0]))
    widths = [max(len(cell) for cell in column) for column in zip(_COLUMNS, *rows)]
    lines = [
        f"exploration remaining {_hms(run_clock().exploration_remaining_s())}  "
        f"host GPUs visible to this run: {run_host().host_visible_gpu_count()}",
        "  ".join(name.ljust(width) for name, width in zip(_COLUMNS, widths)),
        *(
            "  ".join(cell.ljust(width) for cell, width in zip(row, widths)).rstrip()
            for row in rows
        ),
    ]
    return "\n".join(lines)


def _pct(used: float, limit: float | None) -> str:
    """A percentage for the reader, never a second source of truth."""
    if not limit:
        return ""
    return f" ({used / limit * 100:.0f}% of {limit})"


def search_budget_text(search: "AIBuildAISearch") -> str:
    """The whole exploration's budget, in one shape for every phase.

    The first line is always a JSON object with the phase and the budget:
    ``budget`` is null before the exploration opens, because there is no
    budget running to report, and it holds the frozen figures once the
    exploration is over."""
    started_s, finished_s = latest_run_state().exploration_window()
    phase = (
        "before_exploration"
        if started_s is None
        else "exploration_finished"
        if finished_s is not None
        else "exploration_running"
    )
    facts = (
        None
        if started_s is None
        else BudgetReader(run_clock(), latest_run_state()).exploration()
    )
    envelope = json.dumps(
        {"phase": phase, "budget": None if facts is None else facts.model_dump()}
    )
    if facts is None:
        return (
            f"{envelope}\n"
            "The exploration has not started, so no exploration budget is "
            "running yet. Your work is outside it and spends none of it."
        )
    summary = (
        f"wall clock: {_hms(facts.wall_clock_remaining_s)} left of "
        f"{_hms(facts.wall_clock_limit_s)}"
        f"{_pct(facts.wall_clock_used_s, facts.wall_clock_limit_s)}; "
        + (
            f"cost: ${facts.cost_used_usd:.2f} used"
            f"{_pct(facts.cost_used_usd, facts.cost_limit_usd)}"
            if facts.cost_limit_usd is not None
            else f"cost: ${facts.cost_used_usd:.2f} used, no global limit"
        )
    )
    if finished_s is not None:
        return (
            f"{envelope}\n"
            "The exploration is over, so these figures are frozen and your "
            f"work is outside the budget. {summary}"
        )
    return f"{envelope}\n{summary}"


def my_budget_text(search: "AIBuildAISearch", caller_path: str) -> str:
    """Return the budget of the exact Action the calling Agent is running."""
    execution = latest_run_state().execution_for(caller_path)
    # The caller is inside one of its Actions, and that occurrence is what its
    # deadline and its cap belong to, so it is named here rather than left to
    # be guessed from what is running.
    facts = BudgetReader(run_clock(), latest_run_state()).execution(
        execution, execution.ctx.ordinal
    )
    scope = (
        "this work spends the exploration budget"
        if facts.cost_charges_exploration_budget
        else "this work is outside the exploration budget and spends none of it"
    )
    return (
        f"{facts.model_dump_json()}\n"
        f"your wall clock: {_hms(facts.wall_clock_local_remaining_s)} left of "
        f"{_hms(facts.wall_clock_local_limit_s)}, "
        f"{_hms(facts.wall_clock_effective_remaining_s)} once every limit above "
        f"you is counted; the cost figures are your OWN bill, and durable work "
        f"you start bills itself; {scope}."
    )


def executions_mcp_server(
    search: "AIBuildAISearch", caller_path: str
) -> Server:
    """Build the in-process resources server for one Agent identity."""

    def _text(body: str) -> CallToolResult:
        return CallToolResult(content=[TextContent(type="text", text=body)])

    async def list_executions(arguments: dict) -> CallToolResult:
        return _text(executions_table(search, caller_path))

    async def get_search_budget(arguments: dict) -> CallToolResult:
        return _text(search_budget_text(search))

    async def get_my_budget(arguments: dict) -> CallToolResult:
        return _text(my_budget_text(search, caller_path))

    no_arguments = {"type": "object", "properties": {}, "additionalProperties": False}
    return tool_server(
        "resources",
        [
            (
                Tool(
                    name="list_executions",
                    description=(
                        "List every execution of this run (Search, Agents, Programs) with "
                        "its status, elapsed time, time budget, the host GPU indices it "
                        "holds right now, the execution that owns it, the executions it "
                        "was built from (`upstream`), its directory, and its session id; "
                        "the row marked `me` is you. Follow `owner` and `upstream` and "
                        "read those directories to see what came before you. "
                        "Read-only and live: call it again whenever you need the current "
                        "picture, for example before sizing a job on a GPU that another "
                        "row also holds."
                    ),
                    input_schema=no_arguments,
                ),
                list_executions,
            ),
            (
                Tool(
                    name="get_search_budget",
                    description=(
                        "Report the whole exploration's budget: the wall-clock and "
                        "tracked LLM cost limits, what has been used, and what "
                        "remains. Read-only and live. It covers the exploration "
                        "alone -- Setup before it and delivery after it are outside "
                        "it -- so call it to judge how much room the search still "
                        "has, not how much you personally have left."
                    ),
                    input_schema=no_arguments,
                ),
                get_search_budget,
            ),
            (
                Tool(
                    name="get_my_budget",
                    description=(
                        "Report YOUR own budget: the wall clock you were granted, "
                        "how much of it you have used, what remains once every "
                        "limit above you is counted, your cost cap and spend, and "
                        "whether your work spends the exploration budget. It "
                        "always answers about you and takes no arguments."
                    ),
                    input_schema=no_arguments,
                ),
                get_my_budget,
            ),
        ],
    )

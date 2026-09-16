"""Single-sdk_record -> markdown rendering, the PURE shared per-record core.

``render_record`` is the per-record dispatcher the incremental writer (``output.transcript.transcript.Transcript``) calls for every sdk_record. It is engine-free and durable-Agent-free: it takes EXACTLY the flat ``records`` list plus the scalars its call tree reads, performs ZERO repo I/O, and RETURNS ``(parent_chunk, sub_appends)`` where each ``sub_appends`` item is ``(sub_name, text)`` to append to ``<step_dir>/sub-agents/<sub_name>.md``.

The caller supplies a turn number when this record starts a new turn.
"""

from __future__ import annotations

from typing import Callable

from engine.cost import Usage
from output.transcript._compute_helpers import (
    compute_user_blocks,
    get_msg_id,
)
from output.transcript._context_line import compute_context_line
from output.transcript._sub_files import (
    append_to_sub_file,
    format_elapsed_for_record,
)
from output.transcript._system_helpers import render_system_record
from output.transcript._writer_helpers import (
    partition_user_blocks,
    render_assistant_inner_blocks,
    render_sub_agent_return,
)
from output.transcript._cost_breakdown import compute_cost_breakdown
from output.transcript._cost_format import format_breakdown_table_data
from output.transcript.compute import _require_optional_number, _require_result_field
from output.transcript.format import _format_step_result, format_block
from output.transcript.view import BlockData, StepResultData


def render_record(
    rec: dict,
    records: list[dict],
    unit_name: str,
    current_mid: "str | None",
    last_assistant: "dict | None",
    seen_ids: set[str],
    turn_number: int | None,
    sub_name_by_tu: dict[str, str],
    sub_name_seq: dict[str, int],
    cost_fn: Callable[[str, Usage], dict] | None,
) -> "tuple[str, list[tuple[str, str]]]":
    sub_appends: list[tuple[str, str]] = []
    # Sub-agent intermediate routing: assistant w/ parent_tool_use_id and
    # user tool_result blocks that are sub-owned go to sub-agents/<sub>.md.
    ptid = rec.get("parent_tool_use_id")
    if rec.get("type") == "assistant" and ptid is not None:
        sub_name = sub_name_by_tu.get(ptid)
        if sub_name is not None:
            return "", append_to_sub_file(sub_name, rec)
    if rec.get("type") == "user":
        parent_blocks, user_subs = partition_user_blocks(
            records, sub_name_by_tu, unit_name, rec
        )
        sub_appends.extend(user_subs)
        if parent_blocks is None:
            return "", sub_appends
        rec = dict(rec)
        rec["content"] = parent_blocks
    kind = rec.get("type")
    parts: list[str] = []
    if kind == "assistant":
        mid = get_msg_id(rec)
        prev_mid = current_mid
        if prev_mid is not None and prev_mid != mid:
            if last_assistant is not None:
                src = (
                    "parent" if not last_assistant.get("parent_tool_use_id") else "sub"
                )
                parts.append(f"\n{compute_context_line(last_assistant, src)}\n")
        if prev_mid is None or prev_mid != mid:
            if turn_number is None:
                raise AssertionError("new assistant turn has no turn number")
            parts.append(f"\n## Turn {turn_number}\n\n")
        inner, inner_subs = render_assistant_inner_blocks(
            unit_name, rec, sub_name_by_tu, sub_name_seq
        )
        parts.append(inner)
        sub_appends.extend(inner_subs)
    elif kind == "user":
        # Read sub-agent dispatch ids from the routing map, not from any
        # write-only mirror — the map is eagerly populated by
        # prepopulate_sub_name_by_tu so the lookup never falls back to an
        # opaque toolu_* id on resume / out-of-order rendering.
        sub_ids = set(sub_name_by_tu.keys())
        user_blocks, _ = compute_user_blocks(
            rec, format_elapsed_for_record(rec), seen_ids, sub_ids
        )
        for ub in user_blocks:
            if ub.kind == "sub_agent_return":
                ret_str, ret_subs = render_sub_agent_return(
                    records, unit_name, sub_name_by_tu, ub, rec
                )
                parts.append(ret_str)
                sub_appends.extend(ret_subs)
            else:
                parts.append(format_block(ub))
    elif kind == "system":
        parts.append(render_system_record(rec))
    elif kind == "result":
        total = _require_optional_number(rec, "total_cost_usd")
        if last_assistant is not None:
            src = "parent" if not last_assistant.get("parent_tool_use_id") else "sub"
            parts.append(f"\n{compute_context_line(last_assistant, src)}\n")
        f = _require_result_field
        sr = StepResultData(
            f(rec, "subtype"),
            f(rec, "stop_reason"),
            f(rec, "num_turns"),
            _require_optional_number(rec, "duration_ms"),
            _require_optional_number(rec, "duration_api_ms"),
            total,
            format_elapsed_for_record(rec),
            rec.get("is_error", False),
        )
        parts.append(_format_step_result(sr))
        breakdown = None if cost_fn is None else compute_cost_breakdown(
            records, unit_name, sub_name_by_tu, cost_fn=cost_fn)
        # Pre-refactor parity: skip emit when no parent/sub rows exist.
        if breakdown is not None:
            parts.append(format_breakdown_table_data(breakdown))
    elif kind in ("task_progress", "logline", "output_rejected"):
        parts.append(format_block(BlockData(kind=kind, extra=rec)))
    return "".join(parts), sub_appends

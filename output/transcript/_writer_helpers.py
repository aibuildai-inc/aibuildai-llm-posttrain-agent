"""Writer helpers for the transcript renderer.

The leaf elapsed-time helpers and sub-agent file content builders live in _sub_files.py; the SystemMessage renderer lives in _system_helpers.py.

These helpers are engine-free and durable-Agent-free: they read the routing map ``sub_name_by_tu`` and the sequence counter ``sub_name_seq`` directly, walk the flat ``records`` list, and RETURN their ``(parent_str, sub_appends)`` for ``render_record`` to aggregate. Each ``sub_appends`` item is ``(sub_name, text)`` to append to ``<step_dir>/sub-agents/<sub_name>.md``."""

from __future__ import annotations

from output.transcript._compute_helpers import (
    _require_field,
    get_block_kind,
    get_content,
    resolve_sub_name,
)
from output.transcript._sub_files import (
    append_to_sub_file,
    append_to_sub_file_block,
    ensure_sub_header,
    format_elapsed,
    format_elapsed_for_record,
    sub_cost_tail,
)
from output.transcript.format import _fence, format_block
from output.transcript.view import BlockData, TAG_SUB


def prepopulate_sub_name_by_tu(
    records: list[dict], sub_name_by_tu: dict[str, str], sub_name_seq: dict[str, int]
) -> None:
    """Idempotently ensure ``sub_name_by_tu`` has an entry for every Task/Agent dispatch tu_id in ``records`` so the sub-agent file naming stays stable across resume / out-of-order rendering."""
    for rec in records:
        if rec.get("type") != "assistant":
            continue
        if rec.get("parent_tool_use_id") is not None:
            continue
        for blk in rec.get("content", []):
            if blk.get("kind") != "tool_use":
                continue
            if blk.get("name") not in {"Agent", "Task"}:
                continue
            tu_id = blk.get("id")
            if tu_id is None or tu_id in sub_name_by_tu:
                continue
            sub_type = blk.get("input", {}).get("subagent_type", "general-purpose")
            sub_name_by_tu[tu_id] = resolve_sub_name(sub_name_seq, sub_type)


def find_owning_sub_name(
    records: list[dict], sub_name_by_tu: dict[str, str], unit_name: str, tuid: str
) -> "str | None":
    """Return the sub_name owning a sub-emitted inner tool_use, or None when tuid is not sub-owned. Callers must run prepopulate_sub_name_by_tu first so the sub_name_by_tu lookup never falls back.
    """
    for rec in records:
        if rec.get("type") == "assistant" and rec.get("parent_tool_use_id") is not None:
            for blk in rec.get("content", []):
                if blk.get("kind") == "tool_use" and blk.get("id") == tuid:
                    ptid = rec.get("parent_tool_use_id")
                    if not isinstance(ptid, str):
                        raise AssertionError(
                            f"find_owning_sub_name: assistant rec "
                            f"parent_tool_use_id must be str, got "
                            f"{type(ptid).__name__}"
                        )
                    sub_name = sub_name_by_tu.get(ptid)
                    if sub_name is None:
                        raise AssertionError(
                            f"find_owning_sub_name: agent "
                            f"{unit_name!r} has sub-emitted "
                            f"rec (ptid={ptid!r}) inner tuid={tuid!r}, "
                            f"but sub_name_by_tu missing {ptid!r}. "
                            f"prepopulate_sub_name_by_tu must run first."
                        )
                    return sub_name
    return None


def partition_user_blocks(
    records: list[dict], sub_name_by_tu: dict[str, str], unit_name: str, rec: dict
) -> "tuple[list[dict] | None, list[tuple[str, str]]]":
    """Split a user record's content blocks between parent rendering and sub-file routing. Returns ``(parent_blocks, sub_appends)`` where ``parent_blocks`` is the parent-bound block list (possibly empty) or None when the whole record is sub-routed, and ``sub_appends`` is the list of ``(sub_name, text)`` appends for the sub-routed tool_results.

    sub_agent_return input blocks are normalized to tool_result so compute_user_blocks routes them via the sub_ids -> sub_agent_return path.
    """
    sub_routed_blocks: list[tuple[str, dict]] = []
    parent_blocks: list[dict] = []
    for blk in rec.get("content", []):
        bk = blk.get("kind")
        tuid = blk.get("tool_use_id")
        if bk == "sub_agent_return":
            blk = dict(blk)
            blk["kind"] = "tool_result"
            blk["is_error"] = False
            parent_blocks.append(blk)
        elif bk == "tool_result" and tuid is not None and tuid in sub_name_by_tu:
            parent_blocks.append(blk)
        elif bk == "tool_result" and tuid is not None:
            owning_sub_name = find_owning_sub_name(
                records, sub_name_by_tu, unit_name, tuid
            )
            if owning_sub_name is not None:
                sub_routed_blocks.append((owning_sub_name, blk))
            else:
                parent_blocks.append(blk)
        else:
            parent_blocks.append(blk)
    sub_appends = [
        append_to_sub_file_block(sub_name, blk, rec.get("ts", 0.0))
        for sub_name, blk in sub_routed_blocks
    ]
    if not parent_blocks:
        return None, sub_appends
    return parent_blocks, sub_appends


def render_assistant_inner_blocks(
    unit_name: str,
    rec: dict,
    sub_name_by_tu: dict[str, str],
    sub_name_seq: dict[str, int],
    start_idx: int = 0,
) -> "tuple[str, list[tuple[str, str]]]":
    """Render every inner block of an assistant rec with a [+H:MM:SS] prefix derived from rec['ts']. ``start_idx`` skips blocks already rendered on a previous tick. Returns ``(parent_str, sub_appends)``."""
    ts_str = format_elapsed_for_record(rec)
    parts: list[str] = []
    sub_appends: list[tuple[str, str]] = []
    for b in get_content(rec)[start_idx:]:
        bk = get_block_kind(b)
        if bk == "thinking":
            parts.append(
                format_block(
                    BlockData(
                        kind="thinking",
                        thinking=b.get("thinking", ""),
                        collapsible=True,
                        ts=ts_str,
                    )
                )
            )
        elif bk == "text":
            parts.append(
                format_block(
                    BlockData(
                        kind="text",
                        content=b.get("text", ""),
                        source_tag="[parent]",
                        ts=ts_str,
                    )
                )
            )
        elif bk == "tool_use":
            tu_str, tu_subs = render_tool_use_block(
                unit_name, b, sub_name_by_tu, sub_name_seq, rec.get("ts", 0.0)
            )
            parts.append(tu_str)
            sub_appends.extend(tu_subs)
    return "".join(parts), sub_appends


def render_tool_use_block(
    unit_name: str,
    b: dict,
    sub_name_by_tu: dict[str, str],
    sub_name_seq: dict[str, int],
    dispatch_ts: float,
) -> "tuple[str, list[tuple[str, str]]]":
    """Render one tool_use block. Returns ``(parent_str, sub_appends)`` — the sub_appends carry the sub-agent header on a Task/Agent dispatch."""
    ts_str = format_elapsed(dispatch_ts)
    name = _require_field(
        b, "name", "render_tool_use_block", "claude_agent_sdk ToolUseBlock schema"
    )
    if name in {"Agent", "Task"}:
        sub_input = b.get("input", {})
        sub_type = sub_input.get("subagent_type", "general-purpose")
        tu_id = _require_field(
            b,
            "id",
            "render_tool_use_block",
            "claude_agent_sdk ToolUseBlock schema (id is toolu_*)",
        )
        # Re-use any pre-existing assignment (prepopulate_sub_name_by_tu may
        # have already named this dispatch) — resolve_sub_name is NOT
        # idempotent and a second call would bump sub_name_seq.
        sub_name = sub_name_by_tu.get(tu_id)
        if sub_name is None:
            sub_name = resolve_sub_name(sub_name_seq, sub_type)
            sub_name_by_tu[tu_id] = sub_name
        header_append = ensure_sub_header(
            unit_name, sub_name, tu_id, sub_type, sub_input, dispatch_ts
        )
        # sub_agent_dispatch keeps ts default ("0:00:00") in format.py for
        # byte-identical rendering; do not pass ts here.
        return (
            format_block(
                BlockData(
                    kind="sub_agent_dispatch", sub_name=sub_name, tool_use_id=tu_id
                )
            ),
            [header_append],
        )
    return (
        format_block(
            BlockData(
                kind="tool_use",
                tool_name=name,
                tool_input=b.get("input", {}),
                ts=ts_str,
            )
        ),
        [],
    )


def render_sub_agent_return(
    records: list[dict],
    unit_name: str,
    sub_name_by_tu: dict[str, str],
    ub: BlockData,
    rec: dict,
) -> "tuple[str, list[tuple[str, str]]]":
    """Build the consolidated reply section for the sub file and the parent-side dispatch-marker text. Returns ``(parent_str, sub_appends)``."""
    tid = ub.tool_use_id
    if not tid:
        raise AssertionError(
            f"render_sub_agent_return: missing tool_use_id; ub={ub!r}. "
            f"Upstream: _compute_helpers.compute_user_blocks."
        )
    sub_name = sub_name_by_tu.get(tid)
    if sub_name is None:
        raise AssertionError(
            f"render_sub_agent_return: agent {unit_name!r} has a "
            f"sub_agent_return for tool_use_id={tid!r} but sub_name_by_tu "
            f"has no entry for it. prepopulate_sub_name_by_tu must run before "
            f"the user record is rendered."
        )
    ub.sub_return_name = sub_name
    final_txt = ub.content or ""
    elapsed_str = format_elapsed_for_record(rec)
    sub_text = (
        f"\n\n---\n\n## Final consolidated reply ([+{elapsed_str}] parent time)\n\n"
        f"{_fence(final_txt)}\n{final_txt}\n{_fence(final_txt)}\n" + sub_cost_tail(records, tid)
    )
    parent_str = (
        f"{TAG_SUB} [+{elapsed_str}] sub-agent `{sub_name}` returned "
        f"-> see [sub-agents/{sub_name}.md#final-consolidated-reply]"
        f"(./sub-agents/{sub_name}.md#final-consolidated-reply)\n"
    )
    return parent_str, [(sub_name, sub_text)]


def render_appended_assistant_blocks(
    unit_name: str,
    assistant: dict,
    prior_blocks: int,
    sub_name_by_tu: dict[str, str],
    sub_name_seq: dict[str, int],
    seen_ids: set,
) -> "tuple[str, list[tuple[str, str]]]":
    """Render blocks of ``assistant`` appended since its prior saved value. Transcript in-place-extends a flushed rec when per-block messages keep the same message_id, so the writer must catch up after the cursor has moved on. The caller performs the repo writes from the returned chunks.

    Route the appended blocks by ``assistant``'s own ``parent_tool_use_id``, the same ownership decision ``render_record`` makes: a sub-agent open assistant's appended blocks belong to its sub-agent file, never the parent execution.md tagged ``[parent]``."""
    appended = get_content(assistant)[prior_blocks:]
    if not appended:
        return "", []
    for b in appended:
        if get_block_kind(b) == "tool_use":
            seen_ids.add(b.get("id", ""))
    ptid = assistant.get("parent_tool_use_id")
    sub_name = sub_name_by_tu.get(ptid) if ptid is not None else None
    if sub_name is not None:
        return (
            "",
            append_to_sub_file(sub_name, assistant, prior_blocks),
        )
    return render_assistant_inner_blocks(
        unit_name,
        assistant,
        sub_name_by_tu,
        sub_name_seq,
        start_idx=prior_blocks,
    )

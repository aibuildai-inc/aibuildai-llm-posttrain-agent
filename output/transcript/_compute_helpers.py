"""Low-level block-level compute helpers used by compute.py.

Pure functions; no I/O, no time.time().
"""

from __future__ import annotations

from output.transcript.view import BlockData, SubAgentDocument, TurnData


def _require_field(d: dict, key: str, where: str, upstream: str):
    """Return d[key] or raise — loud-fail replacement for "?" sentinels."""
    v = d.get(key)
    if v is None:
        raise AssertionError(
            f"{where}: missing {key!r}; keys={sorted(d.keys())}, d={d!r}. "
            f"Upstream: {upstream}."
        )
    return v


# ---------------------------------------------------------------------------
# record field accessors over the adapter-produced record dicts
# ---------------------------------------------------------------------------


def get_block_kind(block: dict) -> str:
    """Normalise block kind: adapter records use 'kind'; SDK-shaped dicts use 'type'."""
    return block.get("kind") or block.get("type") or ""


def get_msg_id(rec: dict) -> str:
    """Return the Anthropic message id (msg_XXX) for an assistant record.

    No silent fallback: if none of the three accepted shapes carry a non-empty id, raise loudly. The previous `or "_no_id"` sentinel collapsed every id-less record to one global constant, which made the renderer's turn-bump condition `prev_mid != mid` (output/transcript/_record_render.py) always False after the first record, freezing the persisted turn counter while the transcript kept growing (~1004 stuck turns on a single 12-turn live run, 2026-05-19 investigation). Per project policy (never use defensive defaults), the absence of an id is a producer-side bug and must surface immediately.
    """
    mid = (
        rec.get("message_id")
        or (rec.get("message") or {}).get("id")
        or (rec.get("usage") or {}).get("message_id")
    )
    if not mid:
        raise AssertionError(
            f"get_msg_id: record carries no message id; keys={sorted(rec.keys())!r}. The producer (infra/backends/claude/event_adapter.py) must populate `message_id` on every assistant record."
        )
    return mid


def get_content(rec: dict) -> list[dict]:
    direct = rec.get("content")
    if direct is not None:
        return direct if isinstance(direct, list) else []
    msg = rec.get("message")
    if msg is not None:
        c = msg.get("content")
        if isinstance(c, list):
            return c
    return []


def get_usage(rec: dict) -> dict:
    direct = rec.get("usage")
    if direct:
        return direct
    return (rec.get("message") or {}).get("usage") or {}


def get_model(rec: dict) -> str:
    return rec.get("model") or (rec.get("message") or {}).get("model") or ""


# ---------------------------------------------------------------------------
# formatting helpers
# ---------------------------------------------------------------------------


def format_k(n: int) -> str:
    return f"{n / 1000:.1f}k" if n >= 1000 else str(n)


def detect_lang(text: str) -> str:
    t = text.lstrip()
    if t.startswith("{") or t.startswith("["):
        return "json"
    if t.startswith("def ") or t.startswith("import ") or t.startswith("from "):
        return "python"
    if t.startswith("$") or t.startswith("#"):
        return "bash"
    return "text"


# ---------------------------------------------------------------------------
# block-level compute
# ---------------------------------------------------------------------------


def compute_tool_result_block(block: dict, ts: str, seen_ids: set[str]) -> BlockData:
    tid = _require_field(
        block,
        "tool_use_id",
        "compute_tool_result_block",
        "claude_agent_sdk tool_result schema",
    )
    content = block.get("content")
    is_error = block.get("is_error", False)
    if content is None or content == "" or content == []:
        return BlockData(
            kind="warn", ts=ts, warn_reason="tool_result empty", tool_use_id=tid
        )
    if tid not in seen_ids:
        return BlockData(
            kind="warn", ts=ts, warn_reason="tool_result orphan", tool_use_id=tid
        )
    if isinstance(content, list):
        content_str = "\n".join(
            c.get("text", str(c)) if isinstance(c, dict) else str(c) for c in content
        )
    else:
        content_str = str(content)
    lang = detect_lang(content_str)
    lines = content_str.count("\n") + 1
    return BlockData(
        kind="tool_result",
        tool_use_id=tid,
        is_error=is_error,
        result_content=content_str,
        result_lang=lang,
        result_lines=lines,
        ts=ts,
    )


def compute_user_text_block(text: str, ts: str) -> BlockData:
    """Classify a user-text content block and tag it with ts."""
    t_lstrip = text.lstrip()
    if t_lstrip.startswith("<system-reminder>"):
        return BlockData(kind="hook_injection", text=text, ts=ts)
    if "<local-command-stdout>" in text or t_lstrip.startswith("<command-"):
        return BlockData(kind="slash_stdout", text=text, ts=ts)
    return BlockData(kind="user_text", text=text, ts=ts)


def compute_user_blocks(
    rec: dict, ts: str, seen_ids: set[str], sub_ids: set[str]
) -> tuple[list[BlockData], set[str]]:
    """Returns (blocks, handled_tool_use_ids)."""
    blocks: list[BlockData] = []
    handled: set[str] = set()
    content = rec.get("content")
    if isinstance(content, str):
        blocks.append(compute_user_text_block(content, ts))
        return blocks, handled
    for block in get_content(rec):
        bk = get_block_kind(block)
        if bk == "tool_result":
            tid = block.get("tool_use_id")
            if tid in sub_ids:
                final = block.get("content")
                if isinstance(final, list):
                    final_txt = "\n".join(
                        c.get("text", "") if isinstance(c, dict) else str(c)
                        for c in final
                    )
                else:
                    final_txt = str(final) if final else ""
                # sub_name is resolved by the caller via sub_name_map
                blocks.append(
                    BlockData(
                        kind="sub_agent_return",
                        tool_use_id=tid,
                        content=final_txt,
                        ts=ts,
                        sub_return_name=tid,
                    )
                )  # placeholder; caller replaces
                handled.add(tid or "")
            else:
                blocks.append(compute_tool_result_block(block, ts, seen_ids))
                handled.add(tid or "")
        elif bk == "text":
            blocks.append(compute_user_text_block(block.get("text", ""), ts))
    return blocks, handled


def resolve_sub_name(seq: dict[str, int], base_name: str) -> str:
    """The Nth dispatch of ``base_name`` within ``seq`` -> ``base_name`` (1st) then ``base_name_2``, ``base_name_3``, ... Mutates ``seq`` (the caller owns it). Every caller passes a counter scoped to its own lifetime: the incremental writer's is writer-local (rebuilt from the full record list every tick); each document build passes a fresh local counter so a re-render is idempotent."""
    n = seq.get(base_name, 0) + 1
    seq[base_name] = n
    return base_name if n == 1 else f"{base_name}_{n}"


# ---------------------------------------------------------------------------
# append_assistant_blocks — used by compute.py (the one-shot ExecutionDocument build path)
# ---------------------------------------------------------------------------


def append_assistant_blocks(
    rec: dict,
    source: str,
    current_turn: TurnData,
    sub_docs: list[SubAgentDocument],
    sub_by_tu: dict[str, SubAgentDocument],
    sub_name_by_tu: dict[str, str],
    name_seq: dict[str, int],
) -> None:
    for b in get_content(rec):
        bk = get_block_kind(b)
        if bk == "thinking":
            current_turn.blocks.append(
                BlockData(
                    kind="thinking", thinking=b.get("thinking", ""), collapsible=True
                )
            )
        elif bk == "text":
            current_turn.blocks.append(
                BlockData(
                    kind="text", content=b.get("text", ""), source_tag=f"[{source}]"
                )
            )
        elif bk == "tool_use":
            name = _require_field(
                b,
                "name",
                "append_assistant_blocks",
                "claude_agent_sdk ToolUseBlock schema",
            )
            if name in {"Agent", "Task"}:
                sub_input = b.get("input", {})
                sub_type = sub_input.get("subagent_type", "general-purpose")
                sub_name = resolve_sub_name(name_seq, sub_type)
                tu_id = _require_field(
                    b,
                    "id",
                    "append_assistant_blocks",
                    "claude_agent_sdk ToolUseBlock schema (id is toolu_*)",
                )
                sub_name_by_tu[tu_id] = sub_name
                sub_doc = SubAgentDocument(
                    sub_name=sub_name,
                    subagent_type=sub_type,
                    dispatch_description=sub_input.get("description", "(none)"),
                )
                sub_docs.append(sub_doc)
                sub_by_tu[tu_id] = sub_doc
                current_turn.blocks.append(
                    BlockData(
                        kind="sub_agent_dispatch", sub_name=sub_name, tool_use_id=tu_id
                    )
                )
            else:
                # Carry the SDK tool_use id so a downstream renderer can pair this
                # tool_use with its later tool_result block (the Web trajectory
                # detail's one-line tool card reads the result's ok/error
                # by id). The persisted-transcript path ignores it.
                current_turn.blocks.append(
                    BlockData(
                        kind="tool_use",
                        tool_name=name,
                        tool_input=b.get("input", {}),
                        tool_use_id=b.get("id"),
                    )
                )

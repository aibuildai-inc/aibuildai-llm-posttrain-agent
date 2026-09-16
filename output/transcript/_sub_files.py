"""Elapsed-time helpers and sub-agent file content builders.

These are the leaf helpers: the ``[+H:MM:SS]`` elapsed formatting and the functions that PRODUCE the text appended to the per-step ``sub-agents/<name>.md`` files. They perform no I/O; each returns its ``(sub_name, text)`` append (or an empty list when nothing is produced) and the caller threads it up to ``render_record`` for the writer / one-shot driver to persist.
"""

from __future__ import annotations

import json

from output.transcript._compute_helpers import _require_field
from engine.cost import Usage


def format_elapsed(seconds: float) -> str:
    h, rem = divmod(int(seconds), 3600)
    m, s = divmod(rem, 60)
    return f"{h}:{m:02d}:{s:02d}"


def format_elapsed_for_record(rec: dict) -> str:
    ts = _require_field(
        rec,
        "ts",
        "format_elapsed_for_record",
        "the stored event (every sdk_record carries run-relative time)",
    )
    return format_elapsed(ts)


def append_to_sub_file(
    sub_name: str, rec: dict, start_idx: int = 0
) -> list[tuple[str, str]]:
    """Return the ``(sub_name, text)`` append for a sub-routed assistant rec.

    ``start_idx`` skips content blocks already rendered on a previous tick, so the catch-up path (in-place growth of a sub-agent record) renders only the appended blocks. Empty list when the rec produces no content."""
    elapsed_str = format_elapsed_for_record(rec)
    chunks: list[str] = []
    if rec.get("type") == "assistant":
        for blk in rec.get("content", [])[start_idx:]:
            kind = blk.get("kind")
            if kind == "thinking":
                chunks.append(
                    f"\n[+{elapsed_str}] [thinking]\n{blk.get('thinking', '')}\n"
                )
            elif kind == "text":
                chunks.append(f"\n[+{elapsed_str}] [text]\n{blk.get('text', '')}\n")
            elif kind == "tool_use":
                tname = _require_field(
                    blk,
                    "name",
                    "append_to_sub_file",
                    "claude_agent_sdk ToolUseBlock schema",
                )
                tinput = blk.get("input", {})
                if "command" in tinput:
                    body = tinput["command"]
                else:
                    body = json.dumps(tinput, indent=2)
                chunks.append(
                    f"\n[+{elapsed_str}] [tool_use {tname}]\n```\n{body}\n```\n"
                )
    if chunks:
        return [(sub_name, "".join(chunks))]
    return []


def append_to_sub_file_block(sub_name: str, blk: dict, ts: float) -> tuple[str, str]:
    """Return the ``(sub_name, text)`` append for a sub-routed user tool_result."""
    elapsed_str = format_elapsed(ts)
    tuid = _require_field(
        blk,
        "tool_use_id",
        "append_to_sub_file_block",
        "claude_agent_sdk tool_result schema",
    )
    content = blk.get("content", "")
    chunk = f"\n[+{elapsed_str}] [tool_result {tuid}]\n```\n{content}\n```\n"
    return (sub_name, chunk)


def sub_cost_tail(records: list[dict], ptid: str) -> str:
    total_in = total_out = 0
    model: str | None = None
    for rec in records:
        if rec.get("type") == "assistant" and rec.get("parent_tool_use_id") == ptid:
            un = Usage.from_raw(rec.get("usage"))
            total_in += un.input_tokens
            total_out += un.output_tokens
            model = rec.get("model") or model
    if model is None:
        # Sub-agent that only emitted a tool_result (no AssistantMessage) has
        # no row to price; render a placeholder and let the agent's cost
        # object handle authoritative accounting (observed 2026-05-24 in
        # tiny_moons e2e aggregator).
        return "\n**Model**: (no sub assistant message recorded)\n"
    return f"\n**Model**: {model}\n**Usage**: input={total_in}, output={total_out}\n"


def ensure_sub_header(
    unit_name: str,
    sub_name: str,
    tu_id: str,
    sub_type: str,
    sub_input: dict,
    dispatch_ts: float,
) -> tuple[str, str]:
    """Return the ``(sub_name, text)`` header append for a sub-agent dispatch."""
    desc = sub_input.get("description", "(none)")
    prompt = sub_input.get("prompt", "")
    h, rem = divmod(int(dispatch_ts), 3600)
    m, s = divmod(rem, 60)
    ts_str = f"{h}:{m:02d}:{s:02d}"
    header = (
        f"# Sub-agent transcript: `{sub_name}`\n\n"
        f"- **Subagent type**: `{sub_type}`\n"
        f"- **Dispatched at**: [+{ts_str}] (parent time)\n"
        f"- **Parent agent**: {unit_name}\n"
        f"- **Parent tool_use_id**: {tu_id}\n"
        f"- **Dispatch description**: {desc}\n"
        f"\n## Dispatch input prompt\n\n```\n{prompt}\n```\n"
        f"\n---\n\n## Sub-agent session\n\n"
    )
    return (sub_name, header)

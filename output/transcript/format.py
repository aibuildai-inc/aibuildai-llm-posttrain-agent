"""Pure string-builder layer — Document dataclasses to markdown strings.

No file I/O, no side effects.
"""
from __future__ import annotations

import re

from infra.util.debug import debug_log_enabled
from output.transcript.format_tools import TOOL_FORMATTERS as TOOL_FORMATTERS  # re-export
from output.transcript.format_tools import format_tool_input
from output.transcript._sub_files import format_elapsed
from output.transcript.view import (
    TAG_BUDGET, TAG_ERROR, TAG_HOOK, TAG_INFO,
    TAG_STOP, TAG_SUB, TAG_USER, TAG_WARN,
    BlockData, StepResultData,
)


def _debug_id_suffix(tool_use_id: str | None) -> str:
    """' (id=...)' only under AIBUILDAI_DEBUG_LOG=1; hidden from users by default."""
    return f" (id={tool_use_id})" if debug_log_enabled() else ""


# ---------------------------------------------------------------------------
# shape grammar (Q9.1+Q9.2: L1 deleted; L2/L3/L4 renamed)
# ---------------------------------------------------------------------------

# The inverse of the shapes below. The Web display reads the written
# transcript back into typed blocks with these, so they live beside the
# shape builders and change with them. A line that matches none of them
# is prose and stays Markdown.
TURN_HEAD_RE = re.compile(r"^## Turn (\d+)\s*$", re.MULTILINE)
FOLD_HEAD_RE = re.compile(
    r"^<details><summary>\[\+([\d:]+)\] \[([^\]]+)\] (.+?) \(([^)]*)\)</summary>$"
)
TOOL_CALL_RE = re.compile(r"^\[\+([\d:]+)\] \*\*Tool call\*\*: `([^`]+)`$")
TEXT_RE = re.compile(r"^\[\+([\d:]+)\] (\[[^\]]+\]) (.*)$", re.DOTALL)


def shape_blockquote(tag: str, ts_str: str, text: str) -> str:
    return f"> {tag} [+{ts_str}] {text}\n"


def _fence(code: str) -> str:
    n = 3
    while ("`" * n) in code:
        n += 1
    return "`" * n


def shape_collapsible(tag: str, summary: str, lang: str, code: str,
                      code_summary: str = "") -> str:
    extra = f", {code_summary}" if code_summary else ""
    fence = _fence(code)
    return (
        f"\n<details><summary>{tag} {summary} ({lang}{extra})</summary>\n\n"
        f"{fence}{lang}\n{code}\n{fence}\n</details>\n"
    )


def shape_section(tag: str, title: str, ts_str: str, body: str) -> str:
    return f"\n---\n## {tag} {title} at {ts_str}\n\n{body}\n\n---\n"


# ---------------------------------------------------------------------------
# block formatting
# ---------------------------------------------------------------------------

def _require_block_attr(block: BlockData, attr: str):
    v = getattr(block, attr)
    if v is None:
        raise AssertionError(
            f"format.format_block: kind={block.kind!r} {attr}=None; "
            f"producer must set BlockData.{attr}. block={block!r}"
        )
    return v


def _require_extra_field(extra: dict, key: str, kind: str):
    v = (extra or {}).get(key)
    if v is None:
        raise AssertionError(
            f"format.format_block: kind={kind!r} extra missing {key!r}; "
            f"extra={extra!r}."
        )
    return v


def format_block(block: BlockData) -> str:
    # Parent-side inner blocks carry a [+H:MM:SS] prefix from
    # rec['ts'] so execution.md aligns visually with sub-agent files.
    kind = block.kind
    ts_prefix = f"[+{block.ts}] " if block.ts else ""
    if kind == "thinking":
        return shape_collapsible(f"{ts_prefix}[thinking]", "thinking", "text",
                                 block.thinking or "",
                                 f"{len(block.thinking or '')} chars")
    if kind == "text":
        return f"{ts_prefix}{block.source_tag or '[parent]'} {block.content}\n\n"
    if kind == "tool_use":
        name = _require_block_attr(block, "tool_name")
        return (f"\n{ts_prefix}**Tool call**: `{name}`\n\n"
                f"{format_tool_input(name, block.tool_input or {})}\n")
    if kind == "tool_result":
        tag = TAG_ERROR if block.is_error else TAG_INFO
        return shape_collapsible(f"{ts_prefix}{tag}",
                                 f"tool_result{_debug_id_suffix(block.tool_use_id)}",
                                 block.result_lang or "text",
                                 block.result_content or "",
                                 f"{block.result_lines} lines")
    if kind == "warn":
        return shape_blockquote(TAG_WARN, _require_block_attr(block, "ts"),
                                f"{block.warn_reason}{_debug_id_suffix(block.tool_use_id)}")
    if kind == "sub_agent_dispatch":
        sub = _require_block_attr(block, "sub_name")
        ts = block.ts or "0:00:00"
        return (f"\n{TAG_SUB} [+{ts}] **dispatched sub-agent** "
                f"`{sub}` -> see [sub-agents/{sub}.md](./sub-agents/{sub}.md)\n")
    if kind == "sub_agent_return":
        sub = _require_block_attr(block, "sub_return_name")
        ts = block.ts or "0:00:00"
        return (f"{TAG_SUB} [+{ts}] sub-agent `{sub}` returned "
                f"-> see [sub-agents/{sub}.md#final-consolidated-reply]"
                f"(./sub-agents/{sub}.md#final-consolidated-reply)\n")
    if kind == "hook_injection":
        return shape_collapsible(TAG_HOOK, "hook injection", "text", block.text or "", "")
    if kind == "slash_stdout":
        return shape_collapsible(TAG_HOOK, "slash command output", "text", block.text or "", "")
    if kind == "user_text":
        return shape_collapsible(TAG_USER, "user text", "text", block.text or "", "")
    if kind == "logline":
        return shape_collapsible(TAG_BUDGET, "budget reminder", "text",
                                 (block.extra or {}).get("text", ""), "")
    if kind == "output_rejected":
        extra = block.extra or {}
        ts_str = format_elapsed(_require_extra_field(extra, "ts", "output_rejected"))
        diagnostic = _require_extra_field(extra, "diagnostic", "output_rejected")
        return shape_blockquote(TAG_WARN, ts_str, f"output rejected: {diagnostic}")
    if kind == "task_progress":
        extra = block.extra or {}
        task_id = _require_extra_field(extra, "task_id", "task_progress")
        return f"\n_task progress {task_id}: {extra.get('description', '')}_\n"
    return f"\n_{kind}_\n"


# ---------------------------------------------------------------------------
# turn + cost formatting
# ---------------------------------------------------------------------------

def _format_step_result(sr: StepResultData) -> str:
    tag = TAG_ERROR if sr.is_error else TAG_STOP
    duration = "n/a" if sr.duration_ms is None else str(sr.duration_ms)
    duration_api = "n/a" if sr.duration_api_ms is None else str(sr.duration_api_ms)
    total_cost = "n/a" if sr.total_cost_usd is None else f"${sr.total_cost_usd:.4f}"
    body = (f"- subtype: {sr.subtype}\n- stop_reason: {sr.stop_reason}\n"
            f"- turns: {sr.turns}\n- duration_ms: {duration}\n"
            f"- duration_api_ms: {duration_api}\n"
            f"- total_cost_usd: {total_cost}\n")
    return shape_section(tag, f"Step result ({sr.subtype})", sr.ts, body)


# ---------------------------------------------------------------------------
# document-level format functions
# ---------------------------------------------------------------------------

def format_execution_header(
    unit_name: str,
    started_iso: str,
    sidecar_links: list[str],
) -> str:
    """Format the execution.md header."""
    header = (
        f"# {unit_name.upper()} Execution Log\n\n"
        f"- **Agent**: {unit_name}\n"
        f"- **Started**: {started_iso}\n"
    )
    if sidecar_links:
        links = " | ".join(f"[{name}](./{name})" for name in sidecar_links)
        header += f"- **Sidecars**: {links}\n"
    return header + "\n---\n"


def output_json_footer(ordinal: int) -> str:
    """Link the structured Output sidecar owned by one Action occurrence."""
    name = f"output_{ordinal}.json"
    return f"\n## Structured output\n\n[{name}](./{name})\n"

"""Per-tool input formatters and TOOL_FORMATTERS registry.

Each formatter takes a tool-input dict and returns a markdown string. Depends on shape_collapsible from format.py (imported inline to avoid circular).
"""
from __future__ import annotations

import json
from typing import Any


def _require(d: dict, key: str, tool: str) -> Any:
    """Return d[key] or raise AssertionError naming the tool and upstream.

    Loud-fail: every tool-input field flagged as required by the Claude Agent SDK tool schema MUST be present. The producer is the SDK itself (claude_agent_sdk) — if a required field is missing, the upstream tool schema or caller-provided record is malformed; surface immediately rather than render a "?" sentinel into the transcript.
    """
    v = d.get(key)
    if v is None:
        raise AssertionError(
            f"format_tools._fmt_{tool.lower()}: tool input missing required "
            f"field {key!r}; input keys: {sorted(d.keys())}, input: {d!r}. "
            f"Upstream: claude_agent_sdk {tool} tool schema."
        )
    return v


def _fence(code: str) -> str:
    n = 3
    while ("`" * n) in code:
        n += 1
    return "`" * n


def _collapsible(summary: str, lang: str, code: str) -> str:
    """Inline version of shape_collapsible without the tag parameter."""
    fence = _fence(code)
    return (
        f"\n<details><summary> {summary} ({lang})</summary>\n\n"
        f"{fence}{lang}\n{code}\n{fence}\n</details>\n"
    )


def _fmt_bash(d: dict) -> str:
    cmd = _require(d, "command", "Bash")
    desc = d.get("description", "")
    fence = _fence(cmd)
    out = f"{fence}bash\n{cmd}\n{fence}\n"
    if desc:
        out += f"_{desc}_\n"
    return out


def _fmt_read(d: dict) -> str:
    fp = _require(d, "file_path", "Read")
    parts = [f"`{fp}`"]
    if d.get("offset"):
        parts.append(f"offset={d['offset']}")
    if d.get("limit"):
        parts.append(f"limit={d['limit']}")
    return " ".join(parts) + "\n"


def _fmt_write(d: dict) -> str:
    fp = _require(d, "file_path", "Write")
    content = _require(d, "content", "Write")
    return f"`{fp}`\n" + _collapsible("content", "text", content)


def _fmt_edit(d: dict) -> str:
    fp = _require(d, "file_path", "Edit")
    old = _require(d, "old_string", "Edit")
    new = _require(d, "new_string", "Edit")
    return (
        f"`{fp}`\n"
        + _collapsible("old_string", "text", old)
        + _collapsible("new_string", "text", new)
    )


def _fmt_grep(d: dict) -> str:
    pattern = _require(d, "pattern", "Grep")
    return f"`{pattern}` in `{d.get('path', '.')}`\n"


def _fmt_glob(d: dict) -> str:
    pattern = _require(d, "pattern", "Glob")
    return f"`{pattern}`\n"


def _fmt_websearch(d: dict) -> str:
    query = _require(d, "query", "WebSearch")
    return f"`{query}`\n"


def _fmt_todowrite(d: dict) -> str:
    todos = d.get("todos", [])
    # A model sometimes passes the list as JSON text; iterating that string
    # would print one character per row. Invalid text raises and the caller
    # falls back to the raw JSON collapsible.
    if isinstance(todos, str):
        todos = json.loads(todos)
    # SDK schema variability: each todo may be a dict {content, status} or a
    # bare string (newer SDK variants pass plain strings). Normalize both.
    lines = []
    for t in todos:
        if isinstance(t, dict):
            lines.append(f"- {t.get('content', '')} [{t.get('status', '')}]")
        else:
            lines.append(f"- {t}")
    return "\n".join(lines) + "\n" if lines else "(empty)\n"


def _fmt_monitor(d: dict) -> str:
    command = _require(d, "command", "Monitor")
    return f"`{command}`\n"


def _fmt_skill(d: dict) -> str:
    name = _require(d, "skill", "Skill")
    args = d.get("args", "")
    return f"`{name}`" + (f" args={args!r}" if args else "") + "\n"


def _fmt_structured_output(d: dict) -> str:
    return _collapsible("output", "json", json.dumps(d, indent=2, ensure_ascii=False))


def _fmt_toolsearch(d: dict) -> str:
    query = _require(d, "query", "ToolSearch")
    return f"`{query}`\n"


def _fmt_taskoutput(d: dict) -> str:
    timeout = _require(d, "timeout", "TaskOutput")
    return f"block={d.get('block', False)} timeout={timeout}\n"


TOOL_FORMATTERS: dict[str, Any] = {
    "Bash":             _fmt_bash,
    "Read":             _fmt_read,
    "Write":            _fmt_write,
    "Edit":             _fmt_edit,
    "Grep":             _fmt_grep,
    "Glob":             _fmt_glob,
    "WebSearch":        _fmt_websearch,
    "TodoWrite":        _fmt_todowrite,
    "Monitor":          _fmt_monitor,
    "Skill":            _fmt_skill,
    "StructuredOutput": _fmt_structured_output,
    "ToolSearch":       _fmt_toolsearch,
    "TaskOutput":       _fmt_taskoutput,
}


def format_tool_input(name: str, input_dict: dict) -> str:
    """Render a tool_use's input field as markdown. Falls back to a JSON collapsible when there is no per-tool formatter, OR when the registered formatter cannot render the given input (e.g. a malformed / variant tool call from the model that is missing a field the formatter expects, like a ``Read`` carrying ``file_text`` instead of ``file_path``).

    This is a DISPLAY function on the heartbeat/transcript render path — it must never crash the render loop (and thereby the whole pipeline) over a cosmetic formatting concern. Real tool-input contract violations are caught loud in the execution path (the PreToolUse hooks), not here; best-effort rendering of the raw input is the correct behavior for the transcript."""
    fn = TOOL_FORMATTERS.get(name)
    if fn:
        try:
            return fn(input_dict)
        except (AssertionError, KeyError, TypeError, ValueError):
            pass  # malformed input for this tool — fall through to raw JSON
    return _collapsible("input", "json",
                        json.dumps(input_dict, indent=2, ensure_ascii=False, default=str))

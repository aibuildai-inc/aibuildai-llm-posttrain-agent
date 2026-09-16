"""Renderer for the session-init SystemMessage record."""
from __future__ import annotations

from pathlib import Path

from infra.util.debug import debug_log_enabled
from output.transcript._compute_helpers import _require_field
from output.transcript._sub_files import format_elapsed_for_record
from output.transcript.format import shape_section
from output.transcript.view import TAG_INFO


def render_system_record(rec: dict) -> str:
    sub = rec.get("subtype", "")
    data = rec.get("data", {})
    ts_str = format_elapsed_for_record(rec)
    if sub == "init":
        upstream = "SessionStarted event payload"
        req = lambda k: _require_field(data, k, "render_system_record/init", upstream)
        if "launcher_path" not in data:
            raise AssertionError(
                "render_system_record/init: missing required 'launcher_path'; "
                f"keys={sorted(data.keys())}. Upstream: {upstream}."
            )
        launcher_path = data["launcher_path"]
        launcher_line = (
            f"- launcher: [{Path(launcher_path).name}]"
            f"(./launcher/{Path(launcher_path).name})\n"
            if launcher_path is not None
            else ""
        )
        if not debug_log_enabled():
            body = f"- model: {req('model')}\n" + launcher_line
            return shape_section(TAG_INFO, "Session init", ts_str, body)
        # Hard-required (4): provider-neutral domain fields every session has.
        # NB: rendered label for ``cwd`` is ``cwd:``, NOT ``run_home:``. The
        # Web header reserves the ``run_home:`` label for the per-run
        # output directory ``run_state.run_home``; reusing one label for two
        # distinct paths on the same screen was a bug.
        lines = [
            f"- session_id: `{req('session_id')}`",
            f"- model: {req('model')}",
            f"- cwd: {req('cwd')}",
            f"- tools: {', '.join(req('tools'))}",
        ]
        if launcher_path is not None:
            name = Path(launcher_path).name
            lines.append(f"- launcher: [{name}](./launcher/{name})")
        # Optional: render only when non-empty.
        mcp_server_names = data.get("mcp_server_names") or []
        if mcp_server_names:
            lines.append(f"- mcp: {', '.join(mcp_server_names)}")
        body = "\n".join(lines) + "\n"
        return shape_section(TAG_INFO, "Session init", ts_str, body)
    if sub == "action":
        ordinal = _require_field(data, "ordinal", "render_system_record/action", "ActionStarted event payload")
        message = _require_field(data, "message", "render_system_record/action", "ActionStarted event payload")
        return shape_section(TAG_INFO, f"Action #{ordinal}", ts_str, message + "\n")
    raise AssertionError(
        f"render_system_record: unknown system subtype {sub!r}"
    )

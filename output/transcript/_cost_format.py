"""Cost-breakdown markdown renderer (the format half of the breakdown).

Pure (no I/O): turns a ``CostBreakdownTableData`` payload into the markdown breakdown table.
"""
from __future__ import annotations

from infra.util.formatting import fmt_tokens
from output.transcript.view import CostBreakdownTableData


def format_breakdown_table_data(data: CostBreakdownTableData) -> str:
    """Render a CostBreakdownTableData payload as the markdown breakdown table."""
    sdk_str = f"${data.sdk_total:.6f}" if data.sdk_total is not None else "n/a"
    lines = [
        "",
        "## Cost breakdown",
        "",
        f"`[{data.label}]` model={data.parent_model} · SDK billed {sdk_str}",
        "",
        "| source | calls | cache_r | cw | input | output | ws | $ |",
        "|---|---|---|---|---|---|---|---|",
    ]
    for source, calls, usage, dollars in data.rows:
        lines.append(
            f"| {source} | {calls} | "
            f"{fmt_tokens(usage.cache_read_input_tokens)} | "
            f"{fmt_tokens(usage.cache_creation_input_tokens)} | "
            f"{fmt_tokens(usage.input_tokens)} | "
            f"{fmt_tokens(usage.output_tokens)} | "
            f"{usage.web_search_requests} | ${dollars:.6f} |"
        )
    lines.append("")
    if not data.attribution_available:
        # Honest degrade: this model / backend cannot be attributed —
        # the model is unpriced (the flat subscription-billed route, or a
        # genuinely-unknown id), or no token traffic was recorded. Show the
        # truth rather than a fabricated "$0 attributable | +100% | investigate".
        lines.append(
            "**Cost unavailable for this backend/model**: this run's per-message "
            "usage / pricing is not attributable (unpriced model, or the flat "
            "subscription route reports no per-token usage). The SDK-billed total "
            "above, when present, is the only available figure."
        )
        lines.append("")
        return "\n".join(lines)
    if data.sdk_total is not None:
        lines.append(
            f"**SDK billed** (`total_cost_usd`, ground truth): ${data.sdk_total:.6f}"
        )
    lines.append(f"**Attributable** (sum of rows above): ${data.derived_total:.6f}")
    if data.sdk_total is not None:
        gap = data.sdk_total - data.derived_total
        rel = (gap / data.sdk_total * 100) if data.sdk_total else 0.0
        lines.append(
            f"**Unattributable** (SDK billed − attributable): ${gap:+.6f} ({rel:+.4f}%)"
        )
        has_sub = any(src.startswith("sub/") for src, _, _, _ in data.rows)
        if has_sub:
            lines.append(
                "  - Expected positive: sub-agent hidden side queries "
                "(e.g. `tool_use_summary_generation` from sub's Bash) bill into "
                "`total_cost_usd` but are invisible to the SDK Python stream "
                "(they don't emit AssistantMessage events). Token counts cannot "
                "be recovered."
            )
        else:
            lines.append(
                "  - Expected ~0 for single-model runs. Non-zero here means our "
                "price table has drifted from the CLI's — investigate."
            )
    lines.append("")
    return "\n".join(lines)

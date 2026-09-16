"""Cost-breakdown compute shared by Transcript rendering paths.

The markdown renderer half (`format_breakdown_table_data`) lives in `_cost_format.py`.

  - `compute_cost_breakdown(records, unit_name, sub_name_by_tu, *, cost_fn) -> CostBreakdownTableData` Groups sdk_records by parent vs sub/<name>, sums usage, prices each row, returns the structured payload.

No token-derived residual row (removed): ``ResultMessage.usage`` vs. the per-message assistant-record sum does NOT reconcile by subtraction — the SDK's own per-turn figures routinely sum to MORE than its cumulative result (commit 8bb6142890b3: a 5-turn selector, output 1+3+1+1506+2=1513 vs. result 1065, residual -448, no duplicate records). A gap computed from that subtraction is therefore not attributable to anything specific — positive or negative, it may just as well be SDK accounting noise as a real hidden call — so it is never priced or shown as a row. The whole gap is instead carried honestly by ``sdk_total - derived_total`` (the renderer's "Unattributable" line), the same pattern already used by Cost's residual handling.

Sub rows are priced at ``parent_model`` because ``sub_usages`` here stores only usage dicts (no per-record model). For deterministic_cost_fn (model-agnostic) this is invisible; for production cost_for_call this is a known limitation to address in a follow-up commit.
"""

from __future__ import annotations

import logging
from typing import Callable

from infra.cost_pricing import UnknownModelError
from engine.cost import Usage
from output.transcript._compute_helpers import get_model, get_usage
from output.transcript.view import CostBreakdownTableData

logger = logging.getLogger(__name__)


# ---------------------------------------------------------------------------
# compute
# ---------------------------------------------------------------------------


def _sum_usage(usages: list[dict]) -> Usage:
    """Sum raw usage dicts into one canonical Usage."""
    total = Usage()
    for u in usages:
        total = total + Usage.from_raw(u)
    return total


def _is_priceable(cost_fn: Callable[[str, Usage], dict], model: str) -> bool:
    """Whether ``model`` resolves to any pricing tier under ``cost_fn``.

    False for a genuinely-unknown id and the flat subscription-billed route (both raise ``UnknownModelError`` now that the ``gpt-`` $0 blanket is gone). Drives the honest-degrade footer. Parallels ``cost_pricing.can_price`` but is parameterized by the INJECTED ``cost_fn`` (so the deterministic test cost_fn resolves here too)."""
    try:
        cost_fn(model, Usage())
        return True
    except UnknownModelError:
        return False


def _has_tokens(usage: Usage) -> bool:
    return any(
        getattr(usage, k) > 0
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )


def _priced_total(
    cost_fn: Callable[[str, Usage], dict], model: str, usage: Usage, row_label: str
) -> float:
    """Price one breakdown row; a pricing-table miss must not kill the transcript render — the row shows $0 and we warn loudly."""
    try:
        return cost_fn(model, usage)["total"]
    except UnknownModelError:
        logger.warning(
            "cost breakdown: no pricing tier for model %r (row %r shows $0) — "
            "add a tier in infra/cost_pricing.py",
            model,
            row_label,
        )
        return 0.0


def compute_cost_breakdown(
    records: list[dict],
    unit_name: str,
    sub_name_by_tu: dict[str, str],
    *,
    cost_fn: Callable[[str, Usage], dict],
) -> "CostBreakdownTableData | None":
    """Group sdk_records by parent vs sub/<name> and price each.

    Returns ``None`` when no parent or sub assistant records exist.
    """
    parent_usages: list[dict] = []
    sub_usages: dict[str, list[dict]] = {}
    result_aggregate: Usage = Usage()
    sdk_total: float | None = 0.0
    # Initialized to None (not 'unknown'): a missing parent_model that
    # reaches the display is itself a bug — an assistant record without
    # a 'model' field violates the SDK contract. The post-loop check
    # raises if parent_usages exist but no record carried a model.
    parent_model: str | None = None
    for rec in records:
        t = rec.get("type")
        if t == "result":
            reported_cost = rec.get("total_cost_usd")
            if reported_cost is None:
                sdk_total = None
            elif sdk_total is not None:
                sdk_total += reported_cost
            result_aggregate = result_aggregate + Usage.from_raw(get_usage(rec))
        elif t == "assistant":
            u = get_usage(rec)
            ptid = rec.get("parent_tool_use_id")
            if ptid is None:
                parent_usages.append(u)
                m = get_model(rec)
                if m:
                    parent_model = m
            else:
                if not isinstance(ptid, str):
                    raise AssertionError(
                        f"assistant rec parent_tool_use_id must be str, "
                        f"got {type(ptid).__name__}"
                    )
                name = sub_name_by_tu.get(ptid, ptid)
                sub_usages.setdefault(name, []).append(u)

    if not parent_usages and not sub_usages:
        return None

    if parent_usages and parent_model is None:
        raise AssertionError(
            f"compute_cost_breakdown: agent {unit_name} has "
            f"{len(parent_usages)} parent assistant usage(s) but no record "
            f"carried a 'model' field. SDK contract: every assistant record "
            f"carries the model name. Upstream regression in "
            f"the backend event adapter (infra/backends/claude/event_adapter.py) or the Claude SDK."
        )
    if parent_model is None:
        # sub-only path: derive from sub records' model field. SDK contract
        # guarantees at least one sub-assistant record carries the model.
        for rec in records:
            if rec.get("type") == "assistant":
                m = get_model(rec)
                if m:
                    parent_model = m
                    break
        if parent_model is None:
            raise AssertionError(
                f"compute_cost_breakdown: agent {unit_name} has "
                f"sub-agent usages but no assistant record (parent or sub) "
                f"carried a 'model' field. SDK contract violated."
            )

    # Detect whether the result aggregate carries any real (positive) tokens.
    # In production, an early-abort turn's result record carries usage=None
    # from the SDK, which flows through _translate_result into an all-zero
    # 4-key dict. An all-zero dict is truthy, so per-record truthiness checks
    # misidentify it as "usage available". Gating on a positive aggregate
    # avoids that false positive; this feeds the attribution_available
    # gate below (usage_present).
    result_usage_available = any(
        getattr(result_aggregate, k) > 0
        for k in (
            "input_tokens",
            "output_tokens",
            "cache_read_input_tokens",
            "cache_creation_input_tokens",
        )
    )

    rows: list[tuple[str, int, Usage, float]] = []
    parent_summed = _sum_usage(parent_usages)
    rows.append(
        (
            "parent",
            len(parent_usages),
            parent_summed,
            _priced_total(cost_fn, parent_model, parent_summed, "parent"),
        )
    )
    for name, usages in sub_usages.items():
        s = _sum_usage(usages)
        rows.append(
            (
                f"sub/{name}",
                len(usages),
                s,
                _priced_total(cost_fn, parent_model, s, f"sub/{name}"),
            )
        )
    derived_total = sum(d for _, _, _, d in rows)
    # Honest-degrade signal: the breakdown is only meaningful when the
    # parent model is priceable AND some token traffic was recorded. A bare
    # subscription-billed run is neither (unpriceable id + all-zero per-message
    # usage), so the renderer must not show the misleading "$0 attributable |
    # +100% unattributable | investigate" banner.
    sub_summed = _sum_usage([u for usages in sub_usages.values() for u in usages])
    usage_present = (
        result_usage_available or _has_tokens(parent_summed) or _has_tokens(sub_summed)
    )
    attribution_available = _is_priceable(cost_fn, parent_model) and usage_present
    return CostBreakdownTableData(
        label=unit_name,
        parent_model=parent_model,
        rows=rows,
        derived_total=derived_total,
        sdk_total=sdk_total,
        result_usage_available=result_usage_available,
        attribution_available=attribution_available,
    )

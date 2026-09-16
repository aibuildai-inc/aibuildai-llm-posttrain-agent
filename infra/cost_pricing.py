"""Pricing for LLM API calls.

Every model — Claude included — is priced from the local ``infra.model_catalog`` disk cache or baked snapshot, matched under any normalized form: exact, ``[1m]``-stripped, date-stripped, or bare->slug (so the DeepSeek-official bare ``deepseek-v4-flash``, the bare ``gpt-5.5``, and ``claude-opus-4-8[1m]`` all resolve). The snapshot is built from OpenRouter plus a LiteLLM fallback. A run never refreshes it or touches the network for pricing. Listed per-token prices are taken as the real consumed price (a product decision). There are NO hardcoded prices: LiteLLM carries the ``anthropic`` provider, so Claude is in the catalog like everything else.

Whether the SDK's self-reported costUSD is authoritative for a SETTLED call is a SEPARATE, run-level decision keyed on the endpoint the run targets (Anthropic native vs a third-party gateway), NOT a property of the price table. That logic lives with the Cost behavior, not here. This module is pure ``tokens x rate`` pricing.

The bundled CLI injects synthetic AssistantMessages carrying ``model='<synthetic>'`` (3 occurrences in the bundled binary). They are not /v1/messages calls and never bill — an explicit zero special-case so ``cost_for_call`` returns zeros instead of raising ``UnknownModelError``.

A model with NO catalog entry under any normalized form raises ``UnknownModelError`` — the genuinely-unknown case. Callers degrade honestly (cost unavailable) rather than billing a fake $0. This is the only degrade path; a model the catalog knows under any form (bare / namespaced / dated / [1m]) is priced.
"""
from __future__ import annotations

from infra import model_catalog
from engine.cost import Usage

# Web search bills a flat per-request fee across all models; the catalog carries
# no per-request web-search rate, so it is a module constant.
WEB_SEARCH_USD = 0.01

# The bundled CLI's synthetic-message marker. Never a /v1/messages call, never
# bills — priced as explicit zeros and deliberately kept OUT of the catalog.
_SYNTHETIC = "<synthetic>"

_ZERO_COST: dict[str, float] = {
    "input": 0.0, "output": 0.0, "cache_read": 0.0, "cache_write": 0.0,
    "web_search": 0.0, "total": 0.0,
}


class UnknownModelError(ValueError):
    """Raised when cost_for_call is asked to price a model not in the catalog.

    Carries the unrecognized ``model`` identifier as a structured field so callers can log it / branch on it without parsing message strings.
    """
    def __init__(self, model: str, message: str = "") -> None:
        super().__init__(message or f"Unknown model: {model!r}")
        self.model = model


def cost_for_call(model: str, usage: Usage) -> dict[str, float]:
    """Compute per-category USD cost for one /v1/messages call.

    Returns a dict with keys: input, output, cache_read, cache_write, web_search, total. All values in USD. Raises ``UnknownModelError`` when the catalog has no entry for ``model`` under any normalized form.

    ``usage.speed`` (e.g. opus "fast") carries no distinct price under the runtime id form in the catalog, so it does not change the rate here: an Anthropic run settles via the SDK's costUSD (which bills the fast tier correctly), and the pre-settlement live estimate uses the standard rate.
    """
    if _SYNTHETIC in model:
        return dict(_ZERO_COST)
    info = model_catalog.catalog_rates_for(model)
    if info is None:
        raise UnknownModelError(
            model,
            f"No catalog entry for model={model!r} under any normalized form "
            f"(exact / [1m]-stripped / date-stripped / bare->slug). Its cost "
            f"degrades honestly.",
        )
    # cache_write falls back to the plain input rate when the catalog lists none
    # (e.g. OpenRouter deepseek has no prompt-caching surcharge — cache writes
    # bill as plain input).
    cw = info.cache_write if info.cache_write > 0 else info.input
    cost = {
        "input": usage.input_tokens / 1_000_000 * info.input,
        "output": usage.output_tokens / 1_000_000 * info.output,
        "cache_read": usage.cache_read_input_tokens / 1_000_000 * info.cache_read,
        "cache_write": usage.cache_creation_input_tokens / 1_000_000 * cw,
        "web_search": usage.web_search_requests * WEB_SEARCH_USD,
    }
    cost["total"] = sum(cost.values())
    return cost


def can_price(model: str) -> bool:
    """Whether ``model`` resolves to a price (synthetic zero or a catalog entry).

    False for a genuinely-unknown id AND the flat subscription-billed route — the cost display degrades honestly for both instead of fabricating a $0 or a 100%-unattributable banner.
    """
    if _SYNTHETIC in model:
        return True
    return model_catalog.catalog_rates_for(model) is not None

"""Resolve a model's context window from the local model catalog.

This module is the single source of truth for "how big is model X's context window". It reads the disk cache and then the baked snapshot maintained by ``infra.model_catalog``. The upstream snapshot sources are OpenRouter plus a LiteLLM fallback for direct-API routes. A run never refreshes them or touches the network for this lookup.

The ``[1m]`` suffix is the Claude Code CLI's convention for requesting a 1M-token context window on models that support it. Some models now expose 1M context as a standard capability; the catalog records each model's real window.

An unknown model returns ``None`` (window unknown) with a one-time warning — never a fabricated cap. The Web Workspace then shows the raw used-token count instead of a usage bar (a bar needs a real denominator). The ``[1m]`` suffix on a model that does NOT support 1M (a known smaller window) is honoured as that smaller window, and on a genuinely-unknown id stays ``None`` — never inflated to a blanket 1M.
"""
from __future__ import annotations

import functools
import logging

from infra import model_catalog

logger = logging.getLogger(__name__)

_1M_SUFFIX = "[1m]"


@functools.lru_cache(maxsize=None)
def context_window_for_model(model_id: str) -> int | None:
    """Return the context window (in tokens) for a model string, or ``None``.

    Handles the Claude Code ``[1m]`` suffix convention:
      - ``"claude-opus-4-8[1m]"``            -> 1_000_000  (catalog says 1M)
      - ``"claude-opus-4-8"``                -> 1_000_000  (catalog says 1M)
      - ``"claude-haiku-4-5-20251001[1m]"``  -> 200_000    (catalog says <1M)
      - ``"claude-nonexistent-model[1m]"``   -> None        (unknown -> no fabricated cap, NOT a blanket 1M)

    Cached via ``lru_cache`` so each warning fires exactly once per unique string per process.
    """
    has_1m_suffix = model_id.endswith(_1M_SUFFIX)
    base_id = model_id[: -len(_1M_SUFFIX)] if has_1m_suffix else model_id

    base_context = model_catalog.context_length_for(base_id)
    if base_context is None:
        # Genuinely unknown — never fabricate a cap (a [1m] suffix on an unknown
        # id must NOT become a blanket 1M). The Web Workspace shows raw used tokens.
        logger.warning(
            "Unknown model %r — context window unknown; the Web Workspace shows raw "
            "token usage instead of a usage bar. Missing from the catalog "
            "(infra.model_catalog).",
            base_id,
        )
        return None

    # ``[1m]`` requested on a model the catalog says is smaller: honour the real
    # (smaller) window, never inflate to 1M.
    if has_1m_suffix and base_context < 1_000_000:
        logger.warning(
            "Model %r does not support a 1M context window (catalog reports "
            "%d); treating the [1m] request as its %d-token window.",
            base_id, base_context, base_context,
        )
    return base_context

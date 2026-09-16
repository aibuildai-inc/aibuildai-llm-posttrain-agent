"""Offline-safe model-metadata catalog.

There are no hand-maintained price or context tables: every value comes from a snapshot of authoritative upstream sources:

  - OpenRouter ``/api/v1/models`` (no auth) is the authoritative price + context source for every OpenRouter-routed model. Its listed per-token prices are taken as the real consumed price (a product decision).
  - The LiteLLM ``model_prices_and_context_window.json`` is consulted ONLY as a fallback for direct-API routes OpenRouter does not serve under the configured id (Claude-direct ``claude-opus-4-8``, OpenAI ``gpt-5.5``, DeepSeek-direct ``deepseek-chat``); it supplies context windows for those ids.

``load_catalog`` is in-process memoized and never touches the network. It reads the disk cache first, then the baked snapshot shipped in the package.
"""

from __future__ import annotations

import dataclasses
import functools
import json
import re
from pathlib import Path
from typing import Literal
from urllib.parse import urlparse



@dataclasses.dataclass(frozen=True)
class ModelInfo:
    """One model's normalized metadata. Prices are USD per 1M tokens."""

    context_length: int
    input: float
    output: float
    cache_read: float
    cache_write: float
    source: str


# --- paths ----------------------------------------------------------------


def _cache_path() -> Path:
    return Path.home() / ".aibuildai" / "model_catalog.json"


def build_catalog_json(catalog_data_dir: Path) -> str:
    """The baked catalog-snapshot JSON text under ``catalog_data_dir`` (the ``_catalog_data`` package dir shipped next to this module).

    The import freeze below reads it into ``_BAKED_CATALOG_JSON``.
    """
    return (catalog_data_dir / "model_catalog.json").read_text(encoding="utf-8")


# The shipped offline-floor snapshot, read once at THIS module's import (which
# cli.py forces at launch before command dispatch), so a mid-run `git` update to
# infra/_catalog_data/ cannot swap the snapshot under the running process.
_BAKED_CATALOG_JSON = build_catalog_json(
    Path(__file__).resolve().parent / "_catalog_data"
)


# --- snapshot I/O ---------------------------------------------------------


def _read_snapshot(path: Path) -> dict[str, ModelInfo]:
    payload = json.loads(path.read_text())
    return {k: ModelInfo(**v) for k, v in payload["models"].items()}


def _read_baked_snapshot() -> dict[str, ModelInfo]:
    """The shipped offline-floor snapshot, parsed from the JSON text frozen at import (``_BAKED_CATALOG_JSON``). It touches no disk here: the file text is read once at import, so a mid-run tree update cannot change what this returns."""
    payload = json.loads(_BAKED_CATALOG_JSON)
    return {k: ModelInfo(**v) for k, v in payload["models"].items()}


# --- lookups (NEVER touch the network) ------------------------------------


@functools.lru_cache(maxsize=1)
def load_catalog() -> dict[str, ModelInfo]:
    """The in-process catalog: disk cache first, then the baked snapshot.

    Memoized for the process.
    """
    cache = _cache_path()
    if cache.exists():
        return _read_snapshot(cache)
    return _read_baked_snapshot()

# Trailing date stamp: compact ``-YYYYMMDD`` and dashed ``-YYYY-MM-DD`` forms.
_DATE_SUFFIX = re.compile(r"-\d{8}$|-\d{4}-\d{2}-\d{2}$")

# Claude Code's trailing ``[1m]`` 1M-context-request suffix. It never changes
# the per-token price (a 1m-context variant bills at the same rate), so it is
# stripped before matching. ``infra.model_info`` strips it separately for the
# context-window meaning; here it is pure pricing normalization.
_ONE_M_SUFFIX = "[1m]"

# bare-suffix -> canonical slug KEY, derived from the catalog and memoized by the
# catalog object's identity (so it auto-rebuilds when load_catalog re-reads after
# a refresh — no separate cache to clear).
_BARE_INDEX_CACHE: dict[int, dict[str, str]] = {}


def _bare_slug_index() -> dict[str, str]:
    """Map a provider-slug entry's bare model id to its canonical slug KEY.

    A direct-API route (DeepSeek-official, etc.) reports a BARE id (``deepseek-v4-flash``) where the catalog only has the OpenRouter SLUG (``deepseek/deepseek-v4-flash``). This index resolves the bare id to that slug key. Only UNAMBIGUOUS suffixes are included — a bare id whose suffix maps to more than one slug stays unresolved (we cannot guess the provider).
    """
    catalog = load_catalog()
    key = id(catalog)
    cached = _BARE_INDEX_CACHE.get(key)
    if cached is not None:
        return cached
    counts: dict[str, int] = {}
    for slug in catalog:
        if "/" in slug:
            counts[slug.rsplit("/", 1)[-1]] = counts.get(slug.rsplit("/", 1)[-1], 0) + 1
    index = {
        slug.rsplit("/", 1)[-1]: slug
        for slug in catalog
        if "/" in slug and counts[slug.rsplit("/", 1)[-1]] == 1
    }
    _BARE_INDEX_CACHE.clear()
    _BARE_INDEX_CACHE[key] = index
    return index


def canonical_key_for(model_id: str) -> str | None:
    """The catalog KEY a runtime id resolves to, or None when it has no unambiguous match (the honest-degrade case).

    The single shared matching function — pricing, context, AND the header "matched model" display all go through this so they agree on which entry a runtime id resolves to. The deterministic chain:

      0. strip a trailing ``[1m]`` context-request suffix (it never changes the price; no catalog key carries it),
      1. exact catalog key,
      2. strip a trailing date stamp (``-YYYYMMDD`` / ``-YYYY-MM-DD``), retry exact,
      3. by basename: a BARE id maps to the UNIQUE provider-slug whose part after the last ``/`` matches (``deepseek-v4-flash`` -> ``deepseek/deepseek-v4-flash``).

    An exact catalog key always wins first, so a bare id that IS a key (LiteLLM's bare ``gpt-5.5`` / ``deepseek-chat``) returns itself, not a slug. Returns the canonical key string (with provider prefix when matched to a slug).
    """
    catalog = load_catalog()
    base = (
        model_id[: -len(_ONE_M_SUFFIX)]
        if model_id.endswith(_ONE_M_SUFFIX)
        else model_id
    )
    if base in catalog:
        return base
    date_stripped = _DATE_SUFFIX.sub("", base)
    if date_stripped != base and date_stripped in catalog:
        return date_stripped
    if "/" not in date_stripped:
        return _bare_slug_index().get(date_stripped)
    return None


# Endpoint-routable model-id SHAPE filter. A well-formed id for one of the
# product's endpoints:
#   - Anthropic:   claude-{opus|sonnet|haiku|fable}-N (+ optional date/[1m] tail)
#   - DeepSeek:    bare deepseek-* official ids
#   - OpenAI:      bare gpt-* official ids
#   - OpenRouter:  provider/model slugs (any provider, so a new OpenRouter model
#                  needs no edit here)
# This is a SHAPE gate, DISTINCT from ``canonical_key_for`` (catalog membership):
# a bare foreign id like an embedding model is catalog-known but NOT routable on
# our endpoints, so it fails this. The router-output verifier (the in-session
# gate), the llm.auto.candidates validator, AND the caller-side
# ``validate_router_models`` converter all use this ONE predicate, so what the
# verifier accepts the converter keeps and the config gate rejects an unroutable
# model id up front (no silent downstream fall-back to the default model).
_ROUTABLE_MODEL_RE = re.compile(
    r"^(claude-(opus|sonnet|haiku|fable)-\d"
    r"|deepseek-[a-z0-9._-]+"  # DeepSeek official bare ids
    r"|gpt-[a-z0-9._-]+"  # OpenAI official bare ids
    r"|[a-z0-9][a-z0-9._-]*/[a-z0-9][a-z0-9._-]*)"  # OpenRouter provider/model slugs
)


def is_routable_model_id(model_id: str) -> bool:
    """True iff ``model_id`` has the SHAPE of an id routable on one of the product's endpoints (claude-* / bare deepseek-* / bare gpt-* / provider-slug). Pure shape check; combine with ``canonical_key_for(...) is not None`` for the full "valid known routable model" predicate."""
    return _ROUTABLE_MODEL_RE.match(model_id) is not None


def _lookup(model_id: str) -> ModelInfo | None:
    """The ModelInfo a runtime id resolves to, via ``canonical_key_for``."""
    key = canonical_key_for(model_id)
    return load_catalog().get(key) if key is not None else None


def context_length_for(model_id: str) -> int | None:
    """Context window for ``model_id`` from the full merged catalog, or None."""
    info = _lookup(model_id)
    return info.context_length if info is not None else None


def catalog_rates_for(model_id: str) -> ModelInfo | None:
    """Best-effort rates for ``model_id`` from the catalog, under ANY normalized form (exact, date-stripped, bare->slug) and ANY source (OpenRouter or the LiteLLM fallback).

    Pricing degrades ONLY for an id with no catalog entry under any form (the truly-unknown path). A model the catalog knows under any form is priced from it — there is no per-family "this one degrades" rule (a flat-monthly route's catalog price is a list-price estimate, surfaced rather than dropped). Claude resolves here too (LiteLLM carries the ``anthropic`` provider); there are no hardcoded prices. Whether to trust the SDK's costUSD over this catalog price for a SETTLED call is a separate, run-level decision keyed on the endpoint, made by the settlement code — not this table.
    """
    return _lookup(model_id)


def endpoint_is_anthropic(base_url: str) -> bool:
    """True when ``base_url`` (the resolved ``ANTHROPIC_BASE_URL``) targets Anthropic, or is unset, which is the SDK default.

    The one host check behind the run's endpoint identity (``RunConfigRecord.is_anthropic``), the web-tool capability rule (``endpoint_disallowed_tools``), and the credential variable and nonstreaming-fallback choices startup makes for the bundled binary."""
    base = (base_url or "").strip()
    if not base:
        return True
    host = (urlparse(base).hostname or "").lower()
    return host == "anthropic.com" or host.endswith(".anthropic.com")


def endpoint_provider_for_model(
    model_id: str,
) -> Literal["anthropic", "openrouter", "deepseek", "openai"]:
    """Derive which provider/endpoint a model id belongs to.

    The product targets three direct model families -- claude, deepseek, and openai -- plus the OpenRouter gateway, all served through the claude backend with ``ANTHROPIC_BASE_URL`` selecting the endpoint:
      - ``deepseek-*`` (bare, no provider prefix) -> "deepseek": DeepSeek's official Anthropic-compat endpoint (api.deepseek.com/anthropic).
      - ``gpt-*`` (bare, no provider prefix) -> "openai": OpenAI models.
      - ``{provider}/{model}`` slug -> "openrouter".
      - anything else (claude-*) -> "anthropic". This is a SHAPE rule only, never catalog membership (that is ``canonical_key_for``). startup/model_endpoint.py reads it through ``LlmConfig.endpoint_provider`` to auto-export endpoint vars at startup, so a yaml can set ``llm.default.model: deepseek-chat`` with no manual endpoint wiring.
    """
    if "/" in model_id:
        return "openrouter"
    if model_id.startswith("deepseek"):
        return "deepseek"
    if model_id.startswith("gpt-"):
        return "openai"
    return "anthropic"

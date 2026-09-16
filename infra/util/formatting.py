"""Pure formatting helpers — no Rich, no SDK, no I/O.

Token / elapsed / hh:mm:ss formatters, the greedy `reflow` token-wrap, and the model-id helpers (canonical_model_name / short_model_name). Leaf module in the infra.util layer with no upward dependency on engine/. (Agent identity colour was removed in the colour-load audit's first item: a unit's Agent column is now neutral bold and role is carried by the name text + column position.)
"""
from __future__ import annotations

import re
from collections.abc import Sequence

def fmt_tokens(n: int) -> str:
    """Format token count as human-readable string (e.g. 1.2M, 45.2k)."""
    if n >= 1_000_000:
        return f"{n / 1_000_000:.1f}M"
    if n >= 1000:
        return f"{n / 1000:.1f}k"
    return str(n)


def fmt_elapsed(seconds: float) -> str:
    """Compact elapsed time, largest one-or-two units: ``45s`` / ``2m20s`` / ``1h30m``.

    Non-positive input renders ``--``. This is the DENSE style; deliberately distinct from fmt_hhmmss below (zero-padded ``H:MM:SS``) — two formats, one home, so consumers cannot drift. Consumers: the cold-start progress line and the Web Workspace rows.
    """
    if seconds <= 0:
        return "--"
    m, s = divmod(int(seconds), 60)
    h, m = divmod(m, 60)
    if h:
        return f"{h}h" if m == 0 else f"{h}h{m:02d}m"
    if m:
        return f"{m}m" if s == 0 else f"{m}m{s:02d}s"
    return f"{s}s"


def fmt_device(device_indices: "Sequence[int]") -> str:
    """User-facing compute-device label from a unit's allocated GPU indices.

    Empty -> ``cpu`` (no GPU allocated); one index -> ``gpu 1``; several -> ``gpu 0,1``. Plain language for a non-expert user, deliberately NOT the torch ``cuda:N`` form. Single source shared by the Web Workspace execution view and the run exit summary so the two surfaces render the device identically. This is the ALLOCATION the framework stamped on the unit; a GPU allocation is a faithful record of the training device because the coder contract mandates CUDA (no silent CPU fallback) and the preflight probe aborts a GPU training attempt whose interpreter cannot see CUDA.
    """
    if not device_indices:
        return "cpu"
    return "gpu " + ",".join(str(i) for i in device_indices)


def fmt_hhmmss(seconds: float) -> str:
    """Zero-padded wall clock ``H:MM:SS`` (e.g. ``0:00:38`` / ``1:01:01``).

    Negative input clamps to 0. Single source for the Web Workspace H:MM:SS style. Consumers: the shared header/wall-elapsed view and the WorkUnit or nested-execution rows. The compact ``2m20s`` style is fmt_elapsed above — kept separate by design.
    """
    total = max(0, int(seconds))
    minutes, secs = divmod(total, 60)
    hours, minutes = divmod(minutes, 60)
    return f"{hours}:{minutes:02d}:{secs:02d}"


def reflow(chunks: Sequence[str], width: int, sep: str = " · ") -> list[str]:
    """Greedy token-reflow: pack ``chunks`` into lines no wider than ``width``, joined by ``sep``, breaking BEFORE a chunk that would overflow so a line never ends with a dangling separator (the separator is glue BETWEEN chunks, never a line terminator).

    A single chunk wider than ``width`` takes its own line (the terminal then soft-wraps it as a last resort — a continuation line, never lost data). This is the zero-information-loss alternative to letting the terminal soft-wrap a pre-joined ``a · b · c`` string, which strands the separator at the fold. Used by the tree HEADER / FOOTER rows so they read clean at narrow widths instead of orphaning a token after a hanging ``·``.
    """
    if not chunks:
        return []
    lines: list[str] = []
    current = chunks[0]
    for chunk in chunks[1:]:
        if len(current) + len(sep) + len(chunk) <= width:
            current = f"{current}{sep}{chunk}"
        else:
            lines.append(current)
            current = chunk
    lines.append(current)
    return lines


_DATE_SUFFIX_RE = re.compile(r"-\d{8}(?=(\[[^\]]+\])?$)")


def canonical_model_name(model: str) -> str:
    """One canonical model id: strip the trailing -YYYYMMDD date stamp (keeping any [variant] suffix). Pricing needles in infra/cost_pricing.py are substrings, so canonical names still resolve to the same tier.

    'claude-haiku-4-5-20251001' -> 'claude-haiku-4-5'; 'claude-opus-4-8-20260514[1m]' -> 'claude-opus-4-8[1m]'. Keeps distinct versions distinct (opus-4-7 vs opus-4-8). Use this when keying per-model aggregates so an aliased request id and the dated snapshot the SDK reports back don't split into two rows.
    """
    return _DATE_SUFFIX_RE.sub("", model)


def short_model_name(model_id: str, include_version: bool = False) -> str:
    """Shorten an SDK model id for display.

    Family only (default): 'claude-sonnet-5' -> 'sonnet'. With version (include_version=True): 'claude-sonnet-5' -> 'sonnet-5', 'claude-haiku-4-5-20251001' -> 'haiku-4-5' (trailing date stamp dropped). OpenRouter 'provider/model' ids: family = provider ('deepseek/deepseek-v4-flash' -> 'deepseek'); with version -> 'deepseek-v4-flash' (date stamp dropped). Other ids returned unchanged (tolerant, never raises).
    """
    # OpenRouter / third-party ids are 'provider/model-slug'. Family is the provider.
    if "/" in model_id:
        provider, _, slug = model_id.partition("/")
        if not include_version:
            return provider
        parts = [p for p in slug.split("-") if not (p.isdigit() and len(p) >= 6)]
        return "-".join(parts) if parts else provider
    if not model_id.startswith("claude-"):
        return model_id
    parts = model_id.split("-")
    family = parts[1]
    if not include_version:
        return family
    version = [p for p in parts[2:] if not (p.isdigit() and len(p) >= 6)]
    return "-".join([family, *version]) if version else family

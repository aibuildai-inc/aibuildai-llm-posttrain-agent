"""Per-turn context footnote.

Default renders a one-line human token summary; AIBUILDAI_DEBUG_LOG=1 restores the full cache_read/cw/uncached + model + message-id breakdown for developers.
"""
from __future__ import annotations

import re

from infra.util.debug import debug_log_enabled
from engine.cost import Usage
from output.transcript._compute_helpers import format_k, get_model, get_msg_id, get_usage


def compute_turn_usage(rec: dict) -> tuple[int, int, str]:
    """Structured (total_input_tokens, output_tokens, model) for a turn's last assistant record -- the structured sibling of compute_context_line's text.

    total_input folds the cache_read + cache_creation tokens in (the same sum the context line shows as "tokens in"). The Web trajectory master-detail reads these for the per-turn header (tokens in->out + ctx% via context_window_for_model). Fails loud on a model-less record, exactly like compute_context_line, so a broken record surfaces rather than degrading."""
    u = Usage.from_raw(get_usage(rec))
    total_input = u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens
    model = get_model(rec)
    if not model:
        raise AssertionError(
            f"compute_turn_usage: assistant record missing 'model'; "
            f"keys={sorted(rec.keys())}, rec={rec!r}. Upstream: "
            f"the backend event adapter (infra/backends/claude/event_adapter.py; AssistantMessage exposes event.model)."
        )
    return total_input, u.output_tokens, model


# The inverse of the default context line below (the Web display reads it
# back as two token counts). The debug form is left as prose.
CONTEXT_RE = re.compile(r"^\*\*Context\*\*: \[([^\]]+)\] ~(\S+) tokens in / (\S+) out$")


def compute_context_line(rec: dict, source: str) -> str:
    u = Usage.from_raw(get_usage(rec))
    total_input = u.input_tokens + u.cache_read_input_tokens + u.cache_creation_input_tokens
    model = get_model(rec)
    if not model:
        raise AssertionError(
            f"compute_context_line: assistant record missing 'model'; "
            f"keys={sorted(rec.keys())}, rec={rec!r}. Upstream: "
            f"the backend event adapter (infra/backends/claude/event_adapter.py; AssistantMessage exposes event.model)."
        )
    if not debug_log_enabled():
        return (f"**Context**: [{source}] ~{format_k(total_input)} tokens in "
                f"/ {format_k(u.output_tokens)} out")
    mid_short = get_msg_id(rec)[:14]
    return (
        f"**Context**: [{source}] {format_k(total_input)} input "
        f"({format_k(u.cache_read_input_tokens)} cache_read, "
        f"{format_k(u.cache_creation_input_tokens)} cw, "
        f"{format_k(u.input_tokens)} uncached) "
        f"· {format_k(u.output_tokens)} output "
        f"· {u.web_search_requests} web_search "
        f"· model={model} · msg={mid_short}..."
    )

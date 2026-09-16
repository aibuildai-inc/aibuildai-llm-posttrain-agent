"""Public Transcript type."""

from __future__ import annotations

from typing import TYPE_CHECKING, Any

if TYPE_CHECKING:
    from output.transcript.transcript import Transcript, TranscriptProjection

__all__ = ["Transcript", "TranscriptProjection"]


def __getattr__(
    name: str,
) -> Any:  # Any: PEP 562 module hook resolves attrs of varying type
    if name in __all__:
        from output.transcript import transcript

        return getattr(transcript, name)
    raise AttributeError(f"module {__name__!r} has no attribute {name!r}")

"""Stderr logging configuration for the aibuildai pipeline.

The product owns no durable human log file. The typed PostgreSQL journal, the
Agent transcripts, and the producer artifacts are the run's durable record;
ordinary ``logging`` output is ephemeral process diagnostics and goes to
stderr only.

Idempotent: a second call removes our owned handlers and reinstalls a fresh
one. Foreign handlers (jupyter, an embedding host) keep their order, level, and
formatter.
"""
from __future__ import annotations

import logging
import sys

from infra.log_markers import OWNED_HANDLER_ATTR


def configure_logging(*, stream_level: int = logging.WARNING) -> None:
    """Attach the one owned stderr handler at ``stream_level``."""
    root = logging.getLogger()
    # The root level is not ours to raise. It decides which records exist at
    # all, so pinning it to our own handler's level would also silence an INFO
    # handler a host -- jupyter, an embedding application -- attached to the
    # root. Our handler filters at ``stream_level``; the root stays at INFO so
    # a foreign handler still sees everything it asked for.
    root.setLevel(logging.INFO)

    for handler in list(root.handlers):
        if getattr(handler, OWNED_HANDLER_ATTR, False):
            root.removeHandler(handler)
            handler.close()

    stream = logging.StreamHandler(sys.stderr)
    stream.setLevel(stream_level)
    stream.setFormatter(
        logging.Formatter(
            "%(asctime)s %(levelname)-7s %(name)s: %(message)s", "%Y-%m-%d %H:%M:%S"
        )
    )
    setattr(stream, OWNED_HANDLER_ATTR, True)
    root.addHandler(stream)

    # Noisy transport-level loggers whose DEBUG/INFO output is unreadable.
    for noisy in ("httpx", "httpcore", "anyio", "asyncio"):
        logging.getLogger(noisy).setLevel(logging.WARNING)

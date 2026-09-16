"""Single source of truth for the AIBUILDAI_DEBUG_LOG developer toggle.

Default OFF: user-facing renderers (transcript, Web Workspace) hide transport- and billing-layer machine detail. AIBUILDAI_DEBUG_LOG=1 restores it for developers.
"""
from __future__ import annotations

import os


def debug_log_enabled() -> bool:
    """True when AIBUILDAI_DEBUG_LOG selects verbose developer output."""
    return bool(int(os.environ.get("AIBUILDAI_DEBUG_LOG", "0")))

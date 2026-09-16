"""Map shared AIBuildAI visual roles to Web CSS properties."""

from __future__ import annotations

from output.status_presentation import (
    ERROR_INK,
    PENDING_INK,
    RUNNING_INK,
    SUCCESS_INK,
    WARNING_INK,
)
from output.visual_theme import (
    BACKGROUND,
    BORDER,
    BRAND,
    BRAND_INK,
    DATA_ACCENT,
    GUIDE,
    SELECTION,
    SURFACE,
    SURFACE_SUBTLE,
    TEXT,
    TEXT_MUTED,
)


def _atlas_variables() -> dict[str, str]:
    """Map the owned warm editorial palette to the Atlas roles."""
    return {
        "atlas-canvas": BACKGROUND,
        "atlas-surface": SURFACE,
        "atlas-card": SURFACE_SUBTLE,
        "atlas-border": BORDER,
        "atlas-grid": GUIDE,
        "atlas-ink": TEXT,
        "atlas-ink-secondary": TEXT_MUTED,
        "atlas-status-success": SUCCESS_INK,
        "atlas-status-warning": WARNING_INK,
        "atlas-status-error": ERROR_INK,
        "atlas-status-running": RUNNING_INK,
        "atlas-status-pending": PENDING_INK,
    }


def theme_css() -> str:
    """The palette as one ``:root`` CSS block, served at ``/api/v1/theme.css``."""
    variables = {
        "highlight": BRAND,
        "highlight-ink": BRAND_INK,
        "accent": DATA_ACCENT,
        "foreground": TEXT,
        "background": BACKGROUND,
        "surface": SURFACE,
        "panel": SURFACE_SUBTLE,
        "border": BORDER,
        "guide": GUIDE,
        "selection": SELECTION,
        "magnitude": DATA_ACCENT,
        "content": TEXT,
        "secondary": TEXT_MUTED,
        "status-success": SUCCESS_INK,
        "status-warning": WARNING_INK,
        "status-error": ERROR_INK,
        "status-running": RUNNING_INK,
        "status-pending": PENDING_INK,
        **_atlas_variables(),
    }
    lines = "\n".join(
        f"  --aibuildai-{name}: {value};" for name, value in variables.items()
    )
    return f":root {{\n{lines}\n}}\n"

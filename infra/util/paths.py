"""Canonical path helper — every path string entering the system passes through here."""
from __future__ import annotations

from pathlib import Path


def canonical_path(p: str | Path) -> Path:
    """Physical absolute Path, follows symlinks. Idempotent. Path.resolve(strict=False) for non-existent paths."""
    return Path(p).resolve()


def is_within(child: str | Path, parent: str | Path) -> bool:
    """True iff `child` equals `parent` or is a descendant of it, compared by PHYSICAL path (both sides resolved through symlinks via canonical_path).

    This is the ONE way to ask "is this path inside that directory" anywhere in the codebase. Do NOT hand-roll `.startswith(...)`, `.relative_to(...)`, `is_relative_to(...)`, or a local `_under` helper on paths — they get the symlink canonicalization inconsistent (a known bug class). Resolving both sides also collapses `..`, so traversal escapes are rejected.
    """
    c = canonical_path(child)
    p = canonical_path(parent)
    return c == p or p in c.parents

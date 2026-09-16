"""Resolve one Action's logical file grants into the physical view its launch binds.

``FileRef`` is a durable Action fact and does no filesystem I/O, as no
persisted record may; deciding what a reference resolves to on this host means
reading the host, so that reading lives here, at the boundary between the
recorded grant and the sandbox that enforces it. Agent launch and Program
launch are its only callers, and both hand the answer straight to
``Confinement``.
"""

from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path

from engine.durable_execution import FileRef, FileRoot


def _bound_paths(ref: FileRef) -> tuple[str, ...]:
    """The host paths one reference binds, resolved against the host now.

    A plain resource binds itself. An index binds itself and the real target
    of every symlink inside it, because a link is only as readable as what it
    names -- the rule the referring owner asked for when it minted the
    reference, applied here rather than by a list of special resource names.
    """
    root = ref.path()
    base = Path(root)
    if not ref.links or not base.is_dir():
        return (root,)
    targets = [
        str(entry.resolve()) for entry in sorted(base.rglob("*")) if entry.is_symlink()
    ]
    return (root, *dict.fromkeys(targets))


def granted_paths(refs: Iterable[FileRef], *, running: str) -> tuple[str, ...]:
    """Every host path one Action's grants bind, each named once.

    ``running`` is the journal path of the execution the Action runs on. It is
    what a self-reference resolves to, so a caller can grant a child its own
    live tree without knowing the path the same spawn is about to give it."""
    out: list[str] = []
    for ref in refs:
        if ref.root is FileRoot.EXECUTION and not ref.owner:
            ref = ref.model_copy(update={"owner": running})
        for path in _bound_paths(ref):
            if path not in out:
                out.append(path)
    return tuple(out)

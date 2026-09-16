"""Producer workspace preparation — source, attempt, and artifact dirs.

Each producer run owns its own artifact root (the run's ``artifacts/``): the
Coder's source sits at ``<artifact root>/source`` with ``attempt_smoke`` /
``attempt_full`` symlinks pointing at ``<artifact root>/smoke`` / ``full``.
The Coder prompt instructs ancestor-as-read-only; the manager does NOT
enforce with ``chmod -R a-w``. This is a trust-the-prompt design.

``prepare_dir`` also creates the ``scratch`` and ``artifacts`` directories
every execution owns for each of its runs.
"""

from __future__ import annotations

import logging
import os
import shutil
from fnmatch import fnmatch
from pathlib import Path

_logger = logging.getLogger(__name__)


def publish_link(source_dir: str, link_path: str) -> bool:
    """Point one index name at the producer directory that owns it.

    The producer keeps the only copy of its bytes; the index holds the name.
    Idempotent, so a retry needs no staging tree: a link already naming this
    directory succeeds, a missing producer is reported unpublished, and
    anything else there is a conflict this will not resolve. The value is
    RELATIVE because ``write-paper`` copies a run directory to write in place,
    and an absolute target would point the copy back at the original."""
    resolved = Path(source_dir).resolve()
    link = Path(link_path)
    link.parent.mkdir(parents=True, exist_ok=True)
    relative = Path(os.path.relpath(resolved, link.parent.resolve()))
    if link.is_symlink():
        if link.readlink() == relative:
            return True
        raise FileExistsError(f"{link} points at {link.readlink()}, not {relative}")
    if link.exists():
        raise FileExistsError(f"{link} exists and is not a link")
    if not resolved.is_dir():
        return False
    link.symlink_to(relative, target_is_directory=True)
    return True


def next_free_dir(parent: str, prefix: str) -> str:
    """Create the next ``<prefix>_N`` directory under ``parent`` and return its name.

    Graph-free work has no journal path to name it, so its name comes from what is already on disk. The directory is created here, and creation fails if it exists, so two allocations can never be handed the same name and a restarted process can never be handed a name whose directory an earlier process already filled."""
    root = Path(parent)
    root.mkdir(parents=True, exist_ok=True)
    used = [
        int(entry.name[len(prefix) + 1 :])
        for entry in root.iterdir()
        if entry.name.startswith(f"{prefix}_")
        and entry.name[len(prefix) + 1 :].isdigit()
    ]
    name = f"{prefix}_{max(used, default=0) + 1}"
    (root / name).mkdir()
    return name


def prepare_dir(directory: str) -> None:
    """Create one directory a run owns, with its parents; an existing one is kept."""
    Path(directory).mkdir(parents=True, exist_ok=True)


def install_attempt_symlinks(work_dir: str, artifact_root: Path) -> None:
    """Link ``<work_dir>/attempt_smoke`` and ``<work_dir>/attempt_full`` to the artifact root.

    Idempotent: replaces existing symlinks. Raises FileExistsError if a regular directory of the same name exists (guards against `cp -r` of an old workspace).
    """
    root = artifact_root
    nd = Path(work_dir)
    for sub in ("smoke", "full"):
        link = nd / f"attempt_{sub}"
        if link.is_symlink():
            link.unlink()
        elif link.exists():
            kind = "directory" if link.is_dir() else "file"
            raise FileExistsError(
                f"{link} exists as a regular {kind}; refusing to overwrite "
                f"with a symlink. Remove or rename it first."
            )
        link.symlink_to(root / sub)


# Filenames / directory names we never inherit from the parent workspace.
# Heavy artifacts live in artifact_dir (reached via symlink at runtime);
# Child-private outputs are produced fresh.
# Entries skipped by copy_source_from_parent. Matched as fnmatch GLOBS against
# each top-level name and passed to shutil.ignore_patterns for nested dirs.
#
# The workspace layout means a child inherits the parent's SOURCE
# by plain copy — train.py / inference.py / run.sh / config*.yaml / utils are
# NOT listed, so they ARE inherited (the warm-start: a child starts from the
# parent's working code and edits it). Everything below is per-workspace RESULT,
# DELIVERABLE, symlink, or heavy non-source that must NOT carry over:
_INHERIT_SKIP = frozenset(
    {
        "attempt_smoke",  # symlink -> <artifact root>/smoke
        "attempt_full",  # symlink -> <artifact root>/full
        # Per-workspace results — each workspace produces its own; inheriting shadows it.
        "README.md",  # workspace deliverable (Aggregator entry point)
        "feedback.txt",  # workspace findings for the next Reviser
        "*_submission_under_review.json",  # workspace submission materialized for the LLM verifier
        "raw_test*",  # workspace prediction deliverable (file or dir)
        ".cache",  # library cache (HF/torch/pip) — recreated, never copied
        "__pycache__",
        ".pytest_cache",
    }
)

# Old runs can still hold retired copies at the workspace root. A nested source file
# with the same name is ordinary source and must still be copied.
_RETIRED_WORKSPACE_FILES = frozenset(
    {
        "artifact_paths.json",
        "design_plan.json",
        "judge_score.json",
        "revision_proposal.json",
    }
)


def initialize_fresh_coder_workspace(work_dir: str, artifact_root: Path) -> None:
    """Clear creation-time paths immediately before attaching a fresh Coder.

    This function is never called when a Coder child already exists. A partial Coder therefore keeps every workspace edit and checkpoint and resumes itself. A fresh Coder starts from source inherited from its parent plus newly created artifact directories; clearing the destination here makes that one-time copy deterministic.
    """
    nd = Path(work_dir)
    if nd.is_dir():
        shutil.rmtree(nd)
    if artifact_root.is_dir():
        shutil.rmtree(artifact_root)


def copy_source_from_parent(parent_work_dir: str, child_work_dir: str) -> None:
    """Copy small source files from the parent workspace into the child.

    Replaces git's checkout-from-parent behaviour. With heavy artifacts living in artifact_dir (and the parent workspace containing only source files and two symlinks), this is a fast, predictable operation — typically a handful of .py / .yaml / .sh files, milliseconds even on a busy host.

    parent_work_dir is empty when Search is the parent. The child then starts from an empty workspace.

    Symlinks are NEVER followed or recreated — child gets fresh symlinks via install_attempt_symlinks(). Items in _INHERIT_SKIP are passed to shutil.ignore_patterns so recursive copies of nested directories (e.g. utils/__pycache__) also drop them.
    """
    child = Path(child_work_dir)
    child.mkdir(parents=True, exist_ok=True)
    if not parent_work_dir:
        return
    src = Path(parent_work_dir)
    if not src.is_absolute():
        raise AssertionError(
            f"parent_work_dir must be absolute, got {parent_work_dir!r}"
        )
    if not src.is_dir():
        return
    for entry in src.iterdir():
        if entry.name in _RETIRED_WORKSPACE_FILES or any(
            fnmatch(entry.name, pat) for pat in _INHERIT_SKIP
        ):
            continue
        dst = child / entry.name
        if entry.is_symlink():
            continue
        if entry.is_dir():
            shutil.copytree(
                entry,
                dst,
                symlinks=False,
                ignore_dangling_symlinks=True,
                ignore=shutil.ignore_patterns(*_INHERIT_SKIP),
            )
        else:
            shutil.copy2(entry, dst)


def prepare_coder_workspace(
    work_dir: str,
    artifact_root: "str | Path",
    parent_work_dir: str,
) -> None:
    """Build one execution's fresh workspace before its first unit starts.

    The caller names both directories, so ordinary and trial workspaces use the
    same preparation code. Every step here deletes or writes real
    directories, so the durable caller runs it as one DBOS step: a workflow that
    replays past that point joins the Coder it already started instead of wiping
    the workspace that Coder is using."""
    root = Path(artifact_root)
    initialize_fresh_coder_workspace(work_dir, root)
    copy_source_from_parent(parent_work_dir, work_dir)
    (root / "smoke").mkdir(parents=True, exist_ok=True)
    (root / "full").mkdir(parents=True, exist_ok=True)
    install_attempt_symlinks(work_dir, root)

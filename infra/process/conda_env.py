"""Resolve the host Conda root and the named task environment interpreter."""

from __future__ import annotations

import json
import os
import sys
from collections.abc import Callable
from pathlib import Path

from infra.process.command import run_captured


def conda_root() -> str:
    """Return the environment-tool ROOT to bind into the sandbox.

    Derived from ``sys.prefix`` (the manager's own interpreter lives in a conda env). Binding the host root keeps a ``run.conda_env`` named environment resolvable inside the sandbox, and ``_validated_root`` rejects a non-conda prefix — binding one would launch fine (the dir exists) but the sandboxed per-task ``python`` + CUDA libs would NOT resolve -> a confusing in-sandbox ModuleNotFoundError; crash here with a clear reason instead.
    """
    return _validated_root(Path(sys.prefix).resolve(), origin="sys.prefix")


def _validated_root(prefix: Path, origin: str) -> str:
    """Walk ``prefix`` up to its ``envs`` ancestor, else require a conda install."""
    cur = prefix
    while cur != cur.parent:
        if cur.name == "envs":
            return str(cur.parent)
        cur = cur.parent
    if not ((prefix / "envs").is_dir() or (prefix / "conda-meta").is_dir()):
        raise AssertionError(
            f"conda_root() derived {str(prefix)!r} from {origin} but it is not a "
            f"conda installation (no envs/ or conda-meta/); the sandbox would bind a "
            f"non-conda prefix and the task python would not resolve inside it"
        )
    return str(prefix)


def _conda_base() -> Path | None:
    """Return the real conda root, or None when it cannot be derived.

    Derived from the live environment, never hardcoded: ``CONDA_EXE`` points at ``<conda_base>/bin/conda`` (so ``parent.parent`` is the root), and ``CONDA_PREFIX`` is the active env prefix (the root itself when ``base`` is active). ``~/anaconda3`` is NOT assumed — on hosts whose conda lives elsewhere (e.g. a custom prefix under a data mount) that guess is simply wrong.
    """
    conda_exe = os.environ.get("CONDA_EXE")
    if conda_exe:
        return Path(conda_exe).parent.parent
    conda_prefix = os.environ.get("CONDA_PREFIX")
    if conda_prefix:
        return Path(conda_prefix)
    return None


def conda_executable() -> str:
    """Return the host Conda executable, needed only to clone a ``run.conda_env`` named host environment.

    Cloning stays on the host conda that owns the source environment: micromamba's ``--clone`` re-solves only the conda packages and silently drops everything pip installed, so a micromamba clone of a real task environment would be an incomplete copy that fails deep inside paid work.
    """
    configured = os.environ.get("CONDA_EXE")
    if configured and Path(configured).is_file():
        return configured
    candidate = Path(conda_root()) / "bin" / "conda"
    if not candidate.is_file():
        raise AssertionError(
            f"run.conda_env names a host conda environment, but no host conda "
            f"executable exists (checked CONDA_EXE and {candidate}); install "
            "conda, or drop run.conda_env so AIBuildAI creates the Program "
            "environment with its own tooling."
        )
    return str(candidate)


_ENVS_DIRS_BY_CONDA: dict[str, tuple[Path, ...] | None] = {}


async def _envs_dirs_from_conda(
    conda_exe: str, remaining_s: Callable[[], float]
) -> "tuple[Path, ...] | None":
    """Ask conda itself for its ``envs_dirs``, in precedence order, or None.

    ``conda config --show --json`` is the single authoritative answer to "where does a named env live": it already merges, in conda's own precedence order, the ``CONDA_ENVS_DIRS`` / ``CONDA_ENVS_PATH`` overrides, every ``.condarc`` ``envs_dirs`` entry, ``<conda_base>/envs`` and ``~/.conda/envs``. Querying conda is strictly more faithful than reconstructing that precedence by hand — a hand-rolled guess silently diverges the moment a host configures custom ``envs_dirs``. Cached per ``conda_exe`` (the config is stable per process). Returns None — never raises — when conda can't be invoked or parsed, so callers fall back to the derived default list.
    """
    if conda_exe in _ENVS_DIRS_BY_CONDA:
        return _ENVS_DIRS_BY_CONDA[conda_exe]
    try:
        proc = await run_captured(
            [conda_exe, "config", "--show", "--json"],
            remaining_s(),
        )
        if proc.returncode != 0:
            return None
        dirs = json.loads(proc.stdout).get("envs_dirs") or []
    except TimeoutError:
        raise
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    result = tuple(Path(d) for d in dirs if d)
    _ENVS_DIRS_BY_CONDA[conda_exe] = result
    return result


def _derived_envs_dirs() -> list[Path]:
    """Best-effort ``envs_dirs`` when the conda CLI is unavailable.

    Mirrors conda's default precedence without spawning it: explicit ``CONDA_ENVS_PATH`` / ``CONDA_ENVS_DIRS`` overrides first, then ``<conda_base>/envs``, then the per-user ``~/.conda/envs``. Used only when ``_envs_dirs_from_conda`` returns None (no ``CONDA_EXE``, conda not on PATH, call failed) — e.g. CPU-only / non-conda hosts, where nothing resolves anyway and the resolver correctly returns None.
    """
    dirs: list[Path] = []
    for var in ("CONDA_ENVS_PATH", "CONDA_ENVS_DIRS"):
        raw = os.environ.get(var)
        if raw:
            dirs.extend(Path(p) for p in raw.split(os.pathsep) if p)
    base = _conda_base()
    if base is not None:
        dirs.append(base / "envs")
    dirs.append(Path.home() / ".conda" / "envs")
    return dirs


async def _conda_envs_dirs(remaining_s: Callable[[], float]) -> list[Path]:
    """Every directory conda may place a named env in, in precedence order.

    Authoritative source is conda itself (``_envs_dirs_from_conda``); the derived list (``_derived_envs_dirs``) is the fallback. Order-preserving dedupe so a directory configured twice isn't probed twice.
    """
    conda_exe = os.environ.get("CONDA_EXE")
    from_cli = (
        await _envs_dirs_from_conda(conda_exe, remaining_s) if conda_exe else None
    )
    dirs = list(from_cli) if from_cli else _derived_envs_dirs()
    seen: set[Path] = set()
    out: list[Path] = []
    for d in dirs:
        if d not in seen:
            seen.add(d)
            out.append(d)
    return out


async def resolve_task_interpreter_or_none(
    *, conda_env_name: "str | None", remaining_s: Callable[[], float]
) -> "str | None":
    """Resolve the shared per-task BASE env python, or None.

    One interpreter path per configured conda ``envs_dir`` (see ``_conda_envs_dirs``), in conda's precedence order; None when ``conda_env_name`` is empty or nothing resolves. Safe before any execution exists -- the Setup boundary verifier uses it before the run has started any work.
    """
    if not conda_env_name:
        return None
    for d in await _conda_envs_dirs(remaining_s):
        interpreter_path = d / conda_env_name / "bin" / "python"
        if interpreter_path.is_file():
            return str(interpreter_path)
    return None

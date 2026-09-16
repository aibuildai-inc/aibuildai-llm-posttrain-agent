"""Write process-owned plugin files to one temporary resource tree.

Called once from the CLI composition root after process-scope entry and before config construction. The run reads the built-in plugin files once at launch and exposes them as one ``plugins/`` tree that stays fixed for the whole run, so an edit to the checkout while a run is in flight cannot change what its agents read. Standalone user skills are then copied into that tree. User plugin dirs remain at their own paths.

The copied files are plain text. They live in RAM-backed tmpfs when it is present. Normal exit removes the directory. A host restart clears tmpfs. Every materialized directory and file is owner-only (0700 dirs, 0600 files), so other local users cannot read a run's tree.
"""

from __future__ import annotations

import atexit
import json
import os
import shutil
import tempfile
from pathlib import Path

import yaml


_materialized_root: Path | None = None
USER_SKILL_PLUGIN_NAME = "aibuildai-user"
_user_skill_cache: "dict[frozenset[tuple[str, str]], Path | None]" = {}

# Names/suffixes excluded from the plugin bundle (build artifacts, never content).
SKIP_NAMES = {"__pycache__", ".DS_Store"}
SKIP_SUFFIXES = {".pyc", ".pyo"}


def build_plugin_bundle(plugins_root: Path) -> "dict[str, dict[str, str]]":
    """Map every shipped plugin file under ``plugins_root`` to its utf-8 text, keyed ``{marketplace}/{plugin}`` -> ``{rel_path: content}``.

    The launch materialize below reads it. Hidden (dot-prefixed) marketplaces/plugins are skipped, matching the built-in discovery in ``engine.work_unit.agent.plugins``."""
    bundle: dict[str, dict[str, str]] = {}
    for marketplace in sorted(plugins_root.iterdir()):
        if not marketplace.is_dir() or marketplace.name.startswith("."):
            continue
        for plugin in sorted(marketplace.iterdir()):
            if not plugin.is_dir() or plugin.name.startswith("."):
                continue
            key = f"{marketplace.name}/{plugin.name}"
            files: dict[str, str] = {}
            for f in sorted(plugin.rglob("*")):
                if not f.is_file():
                    continue
                if f.name in SKIP_NAMES or f.suffix in SKIP_SUFFIXES:
                    continue
                rel = str(f.relative_to(plugin))
                files[rel] = f.read_text(encoding="utf-8")
            bundle[key] = files
    return bundle


def _populate(root: Path) -> None:
    source_root = Path(__file__).resolve().parent.parent
    plugin_bundle = build_plugin_bundle(source_root / "plugins")

    # Make every new file private to this user.
    prev_umask = os.umask(0o077)
    try:
        for plugin_key, files in plugin_bundle.items():
            for rel_path, content in files.items():
                target = root / "plugins" / plugin_key / rel_path
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content, encoding="utf-8")
    finally:
        os.umask(prev_umask)


def materialize_to_tmpfs() -> str:
    """Decode the shipped plugin files into a process-owned RAM-backed directory.

    The directory is removed on normal process exit. A host restart clears tmpfs.
    """
    global _materialized_root
    if _materialized_root is not None:
        raise AssertionError(
            "shipped resources were already materialized for this process"
        )
    shm = Path("/dev/shm")
    base_dir = str(shm) if shm.is_dir() else tempfile.gettempdir()
    root = Path(tempfile.mkdtemp(prefix="aibuildai-resources-", dir=base_dir))
    try:
        _populate(root)
    except BaseException:
        shutil.rmtree(root)
        raise
    atexit.register(shutil.rmtree, root)
    _materialized_root = root
    return str(root)


def materialized_root() -> Path | None:
    """Return the tmpfs root written by :func:`materialize_to_tmpfs`.

    The returned path contains the launch-frozen ``plugins/`` tree.

    Returns ``None`` before startup has materialized the bundles. Required resource consumers fail clearly in that state; they never fall back to the mutable checkout.
    """
    return _materialized_root


def materialize_user_skills(
    byo: list[tuple[str, Path]],
) -> Path | None:
    """Copy standalone user skills into the process resource tree.

    Each directory must contain a ``SKILL.md`` whose name matches the config key. The same set of skill paths is copied once per process.
    """
    key = frozenset((name, str(path)) for name, path in byo)
    if key in _user_skill_cache:
        return _user_skill_cache[key]
    if not byo:
        _user_skill_cache[key] = None
        return None
    if _materialized_root is None:
        raise AssertionError(
            "shipped resources must be materialized before user skills"
        )

    # The SDK loads standalone skills through one hidden plugin in the same
    # process-owned tree as the shipped plugins.
    plugin_dir = _materialized_root / "plugins" / USER_SKILL_PLUGIN_NAME
    (plugin_dir / ".claude-plugin").mkdir(parents=True, exist_ok=True)
    (plugin_dir / ".claude-plugin" / "plugin.json").write_text(
        json.dumps({"name": USER_SKILL_PLUGIN_NAME}),
        encoding="utf-8",
    )
    for name, src in byo:
        if not src.is_dir():
            raise ValueError(f"skills: BYO {name!r} path is not a directory: {src}")
        skill_md = src / "SKILL.md"
        if not skill_md.is_file():
            raise ValueError(
                f"skills: BYO {name!r} directory is missing SKILL.md: {src}"
            )

        # Read the declared skill name from the one frontmatter block that uses it.
        text = skill_md.read_text(encoding="utf-8")
        declared = None
        if text.startswith("---"):
            parts = text.split("---", 2)
            if len(parts) == 3:
                meta = yaml.safe_load(parts[1])
                if isinstance(meta, dict):
                    declared = meta.get("name")
        if declared != name:
            raise ValueError(
                f"skills: BYO key {name!r} must equal its SKILL.md name (got "
                f"{declared!r}) so the handle aibuildai-user:{name}, the on-disk "
                f"dir, and the SDK skill name align: {src}"
            )
        dest = plugin_dir / "skills" / name
        if dest.exists():
            shutil.rmtree(dest)
        shutil.copytree(src, dest)
    _user_skill_cache[key] = plugin_dir
    return plugin_dir

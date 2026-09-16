import importlib
import pkgutil
import re
import subprocess
import sys
from importlib.metadata import PackageNotFoundError
from importlib.metadata import version as _metadata_version
from pathlib import Path

APP_NAME = "aibuildai"            # package / pip name
APP_DISPLAY_NAME = "AIBuildAI"    # brand name shown in the Web header

# A release is named by its tag, and the tag has one of two shapes:
#   - a DIRECT line is named by a version number: ``v2.5.5``;
#   - a NAMED line is named by its codename and date: ``science-2026-08-28``.
# A named line has no version number of its own; its date IS its number, stored
# as the version triple ``2026.8.28`` so every consumer of APP_VERSION (PEP 440
# metadata, the journal's pinned version) keeps working unchanged. The
# codename travels beside it as APP_LINE. ``internal/*`` tags are never a source.
_DIRECT_TAG_RE = re.compile(r"^v(\d+)\.(\d+)\.(\d+)$")
_NAMED_TAG_RE = re.compile(r"^([a-z][a-z0-9]*)-(\d{4})-(\d{2})-(\d{2})$")
_DIRECT_TAG_GLOB = "v[0-9]*.[0-9]*.[0-9]*"
# Version and line of the source this tree was published from; used when no
# git tag or installed package metadata is available.
_RELEASE_VERSION: tuple[str, str | None] = ("2026.9.8", "post-train")
_NAMED_TAG_GLOB = "*-[0-9][0-9][0-9][0-9]-[0-9][0-9]-[0-9][0-9]"


def parse_release_tag(tag: str) -> tuple[str | None, str]:
    """``(line, bare_version)`` for a release tag: ``("science", "2026.8.28")`` or ``(None, "2.5.5")``. ValueError for anything else, including the ``-latest`` pointer tags."""
    named = _NAMED_TAG_RE.match(tag)
    if named:
        codename, year, month, day = named.groups()
        return codename, f"{int(year)}.{int(month)}.{int(day)}"
    direct = _DIRECT_TAG_RE.match(tag)
    if direct:
        return None, ".".join(str(int(part)) for part in direct.groups())
    raise ValueError(f"not a release tag: {tag!r}")


def _describe(match: str) -> tuple[str, int, str] | None:
    """``(tag, distance, local_tail)`` for the nearest tag matching ``match``, or None when there is none. Runs git in aibuildai_version.py's own directory (not the process cwd) so a launch from inside an unrelated git repo can never read that repo's tags."""
    try:
        completed = subprocess.run(
            ["git", "describe", "--tags", "--long", "--dirty", "--match", match,
             "--exclude", "internal/*"],
            cwd=Path(__file__).resolve().parent,
            capture_output=True,
            text=True,
            check=True,
        )
    except (OSError, subprocess.CalledProcessError):
        # git missing, not a repo, or no matching tag yet.
        return None
    described = completed.stdout.strip()
    if not described:
        return None
    dirty = described.endswith("-dirty")
    if dirty:
        described = described[: -len("-dirty")]
    # --long always yields <tag>-<distance>-g<sha>; the tag itself may contain "-".
    tag, distance, sha = described.rsplit("-", 2)
    tail = f"{distance}.{sha}" + (".dirty" if dirty else "")
    return tag, int(distance), tail


def _from_git() -> tuple[str, str | None] | None:
    # Live single source of truth in a dev checkout: the nearest release tag of
    # either shape. On a tie the named tag wins, which is what a commit carrying
    # both an old v-tag and its dated re-tag must report.
    candidates = [c for c in (_describe(_NAMED_TAG_GLOB), _describe(_DIRECT_TAG_GLOB)) if c]
    if not candidates:
        return None
    tag, distance, tail = min(candidates, key=lambda c: c[1])
    line, bare = parse_release_tag(tag)
    # The distance/sha/dirty tail is not PEP 440, so it becomes a local-version
    # segment; that keeps pyproject's dynamic version and ``pip install -e .``
    # working in an unreleased dev checkout.
    return (bare if distance == 0 and not tail.endswith(".dirty") else f"{bare}+{tail}"), line


def _from_metadata() -> tuple[str, str | None] | None:
    # Installed package without a git checkout (e.g. a pip wheel): the version
    # recorded at install time, derived from the tag by pyproject's dynamic
    # version. Metadata is already a bare PEP 440 version (no leading "v") and
    # carries no line.
    try:
        return _metadata_version(APP_NAME), None
    except PackageNotFoundError:
        return None

def _derive_version() -> tuple[str, str | None]:
    return _from_git() or _from_metadata() or _RELEASE_VERSION


# APP_VERSION: bare version triple, e.g. "2.5.0" or "2026.8.28" — never carries a
# leading "v". APP_LINE: the codename of a named line ("science"), None on a
# direct line.
APP_VERSION, APP_LINE = _derive_version()


def version_string() -> str:
    return f"{APP_DISPLAY_NAME} {release_label(APP_VERSION, APP_LINE)}"


def display_version(version: str) -> str:
    """Strip the PEP 440 local-version segment (the ``+local`` tail ``git describe`` folds in, e.g. ``+461.gabc1234`` or ``+461.gabc1234.dirty``) so a version COMPARISON sees the stable release identity ``2.5.0`` rather than the per-commit build id. APP_VERSION itself keeps the full string, because pip metadata requires the build-unique form. NOT a user-facing name: that is ``release_label``."""
    return version.split("+", 1)[0]


def _release_date(version: str) -> str:
    """``2026.8.28`` -> ``2026-08-28``: the date a named line's version triple stores."""
    year, month, day = display_version(version).split(".")
    return f"{year}-{int(month):02d}-{int(day):02d}"


def release_tag(version: str, line: str | None) -> str:
    """The release tag for the build at ``version`` on ``line`` (a codename, or None on a direct line): ``science-2026-08-28`` or ``v2.5.5``. A dev build (a PEP 440 local segment such as ``+735.gSHA``) keeps that segment after the tag, so the artifact it names cannot be mistaken for a release. The line is always passed explicitly: this build's own is ``APP_LINE``, and release.sh passes the build's because inside the Docker build git is absent and ``APP_LINE`` is unknown."""
    local = version.partition("+")[2]
    suffix = f"+{local}" if local else ""
    if line is not None:
        return f"{line}-{_release_date(version)}{suffix}"
    return f"v{version}"

def release_label(version: str, line: str | None) -> str:
    """What a HUMAN surface calls the build at ``version`` on ``line``: ``Science (2026-08-28)`` on a named line, ``v2.5.5`` on a direct line. A dev build is labelled by its tag plus its local segment, which is how ``--version`` still recovers the source commit of a dev build."""
    if "+" in version or line is None:
        return release_tag(version, line)
    return f"{line.capitalize()} ({_release_date(version)})"


# The first-party modules and packages preload_source_runtime imports before a
# source command starts.

RUNTIME_TOP_LEVEL_MODULES = [
    "cli.py",
    "cli_impl.py",
    "bootstrap.py",
    "config.py",
    "aibuildai_version.py",
]

RUNTIME_PACKAGES = [
    "engine",
    "infra",
    "memory",
    "output",
    "startup",
]


# Pinned third-party source trees the runtime imports directly, repo-relative.
# Empty today: no runtime module imports one.
RUNTIME_SUBMODULE_PATHS: list[str] = []


# Concrete Search Definition packages. They are DISCOVERED like every other
# first-party module -- the build needs their names, and so does the revision
# guard's inventory -- but they are not ACTIVATED here. Which one a run uses is
# decided by `search.kind` after the configuration is read, and importing all
# nine before that is the eager all-package loading this runtime deliberately
# does not do. `engine.builtin.aibuildai` is the shared product shell every run
# needs and is not deferred.
_DEFERRED_PACKAGE_ROOT = "engine.builtin."
_SHARED_BUILTIN = "engine.builtin.aibuildai"


def _is_deferred(module_name: str) -> bool:
    """Whether importing this module would activate a concrete Definition package."""
    if not module_name.startswith(_DEFERRED_PACKAGE_ROOT):
        return False
    return not (
        module_name == _SHARED_BUILTIN or module_name.startswith(_SHARED_BUILTIN + ".")
    )


def _discover_module_names(package_name: str, package_path: list[str]) -> set[str]:
    """Every module name under one package, WITHOUT importing any of them.

    ``pkgutil.walk_packages`` imports each package it descends into, which is
    exactly the activation this function must not perform; ``iter_modules``
    only lists, so the descent is done here from the directory it names."""
    found: set[str] = set()
    pending = [(package_name, list(package_path))]
    while pending:
        prefix, paths = pending.pop()
        for info in pkgutil.iter_modules(paths):
            name = f"{prefix}.{info.name}"
            found.add(name)
            if info.ispkg:
                pending.append(
                    (name, [str(Path(entry) / info.name) for entry in paths])
                )
    return found


def add_runtime_submodule_paths() -> None:
    """Put every pinned third-party submodule source tree on ``sys.path``.

    Fails loud when a declared submodule is missing, so an uninitialized checkout says so here rather than as a confusing ImportError at the first score call.
    """
    source_root = Path(__file__).resolve().parent
    for relative in RUNTIME_SUBMODULE_PATHS:
        path = source_root / relative
        # An uninitialized submodule is an EMPTY directory git already created, not
        # an absent one, so is_dir() is true exactly when the guard must fire.
        if not path.is_dir() or not any(path.iterdir()):
            raise ImportError(
                f"runtime submodule {relative!r} is not initialized at {path}; "
                f"run: git submodule update --init {relative}"
            )
        entry = str(path)
        if entry not in sys.path:
            sys.path.insert(0, entry)


def preload_source_runtime() -> None:
    """Import the complete first-party runtime before a source command starts."""
    source_root = Path(__file__).resolve().parent
    if not (source_root / ".git").exists():
        return
    # Before the first-party import sweep below, so a first-party module that
    # imports a pinned submodule tree resolves it.
    add_runtime_submodule_paths()

    head_before = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()

    module_names = {
        Path(path).stem
        for path in RUNTIME_TOP_LEVEL_MODULES
        if Path(path).stem not in {"aibuildai_version", "cli"}
    }
    for package_name in RUNTIME_PACKAGES:
        package = importlib.import_module(package_name)
        package_path = getattr(package, "__path__", None)
        if package_path is None:
            raise AssertionError(
                f"first-party runtime package {package_name!r} has no __path__"
            )
        module_names.add(package_name)
        module_names.update(_discover_module_names(package_name, list(package_path)))

    for module_name in sorted(module_names):
        if _is_deferred(module_name):
            continue
        importlib.import_module(module_name)

    head_after = subprocess.run(
        ["git", "rev-parse", "HEAD"],
        cwd=source_root,
        capture_output=True,
        text=True,
        check=True,
    ).stdout.strip()
    if head_after != head_before:
        raise SystemExit(
            "the source checkout changed while first-party runtime modules were "
            f"loading: {head_before} -> {head_after}; start the command again"
        )


def run_cli() -> None:
    """Load one source revision, then run the command."""
    preload_source_runtime()
    from cli_impl import cli
    cli()

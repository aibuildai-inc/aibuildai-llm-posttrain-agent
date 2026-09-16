"""Memory read interface and its filesystem and launch-snapshot backings.

A ``MemorySource`` is the read contract the reader needs over the memory corpus (per-task histories, cross-task patterns, the task->category mapping). Two backings implement it:

- ``MemoryStore`` — the FILESYSTEM store, parameterized by a root dir, resolving tasks/, patterns/ and task_categories.json beneath it. Two constructors: builtin() (the shipped corpus under memory/) and user() (the user-writable dir from memory.user_dir). It carries the write surface the source-only tools use: memorize.py publishes one task document, curate.py maintains the built-in pattern documents.
- ``BundleMemoryStore`` — read-only, backed by a ``MEMORY_BUNDLE``-shaped dict built once at import from the corpus on disk, so a run reads one snapshot of the built-in corpus.

The ``builtin_source()`` factory always returns that launch-snapshot backing. Developer curation tools use ``MemoryStore`` directly for writable filesystem content.
"""
from __future__ import annotations

import abc
import json
from pathlib import Path


# Root of the built-in corpus, resolved next to this module. The launch
# snapshot is built from it once at import; it is also the filesystem root for
# the curator, which needs write access.
BUILTIN_MEMORY_ROOT = Path(__file__).resolve().parent


def _validate_task_category_mapping(raw_mapping: object) -> dict[str, str]:
    """Return a typed task-to-category mapping or fail on corrupt corpus data."""
    assert isinstance(raw_mapping, dict), (
        "task category mapping must be a JSON object"
    )
    mapping: dict[str, str] = {}
    for task, category in raw_mapping.items():
        assert isinstance(task, str) and task.strip(), (
            f"task names must be non-empty strings; got {task!r}"
        )
        assert isinstance(category, str) and category.strip(), (
            f"category for task {task!r} must be a non-empty string"
        )
        mapping[task] = category
    return mapping


class MemorySource(abc.ABC):
    """The read contract over the memory corpus the reader consumes.

    The abstract primitives are the only filesystem-vs-bundle difference; the concrete derived methods (category_for_task / tasks_by_category) depend solely on those primitives, so both backings share them.
    """

    @abc.abstractmethod
    def read_task(self, task_name: str) -> str | None:
        """The per-task history text, or None when the task has no entry."""
        ...

    @abc.abstractmethod
    def read_pattern(self, category: str) -> str | None:
        """The cross-task pattern text for a category, or None when absent."""
        ...

    @abc.abstractmethod
    def iter_patterns(self) -> list[tuple[str, str]]:
        """Sorted list of (pattern_stem, text) over every pattern."""
        ...

    @abc.abstractmethod
    def task_category_mapping(self) -> dict[str, str]:
        """The validated task-to-pattern-category mapping."""
        ...

    @abc.abstractmethod
    def assert_present(self) -> None:
        """Hard-fail if the built-in corpus is missing or unusable."""
        ...

    def category_for_task(self, task_name: str) -> str | None:
        """The pattern category mapped to a task, or None when unmapped."""
        return self.task_category_mapping().get(task_name)

    def tasks_by_category(self) -> dict[str, list[str]]:
        """Return tasks grouped by their mapped pattern category."""
        categories: dict[str, list[str]] = {}
        for task, category in self.task_category_mapping().items():
            categories.setdefault(category, []).append(task)
        return categories


class MemoryStore(MemorySource):
    def __init__(self, root: Path | str) -> None:
        self.root = Path(root)

    @property
    def tasks_dir(self) -> Path:
        return self.root / "tasks"

    @property
    def patterns_dir(self) -> Path:
        return self.root / "patterns"

    @property
    def task_category_mapping_path(self) -> Path:
        return self.root / "task_categories.json"

    @classmethod
    def builtin(cls) -> "MemoryStore":
        return cls(BUILTIN_MEMORY_ROOT)

    @classmethod
    def user(cls, user_dir: str) -> "MemoryStore":
        return cls(Path(user_dir).expanduser())

    def read_task(self, task_name: str) -> str | None:
        f = self.tasks_dir / f"{task_name}.md"
        return f.read_text() if f.exists() else None

    def read_pattern(self, category: str) -> str | None:
        f = self.patterns_dir / f"{category}.md"
        return f.read_text() if f.exists() else None

    def iter_patterns(self) -> list[tuple[str, str]]:
        return [(p.stem, p.read_text()) for p in sorted(self.patterns_dir.glob("*.md"))]

    def task_category_mapping(self) -> dict[str, str]:
        raw_mapping = json.loads(
            self.task_category_mapping_path.read_text(encoding="utf-8")
        )
        return _validate_task_category_mapping(raw_mapping)

    def assert_present(self) -> None:
        """Hard-fail if the built-in corpus is missing. The message names the missing path and how to restore it, because this fires on a user's machine when the bundle is incomplete."""
        assert self.root.exists(), (
            f"memory/ package directory missing at {self.root}; "
            f"it ships with the source tree."
        )
        assert self.task_category_mapping_path.exists(), (
            f"memory/task_categories.json missing at {self.task_category_mapping_path} — "
            f"required to route task→pattern category. "
            f"Check that memory/task_categories.json was bundled."
        )


class BundleMemoryStore(MemorySource):
    """Read-only built-in corpus backed by the generated MEMORY_BUNDLE dict.

    The bundle structure:
        {"tasks": {stem: text}, "patterns": {stem: text}, "task_categories": dict}
    """

    def __init__(self, bundle: dict) -> None:
        self._tasks: dict[str, str] = bundle["tasks"]
        self._patterns: dict[str, str] = bundle["patterns"]
        self._task_category_mapping = _validate_task_category_mapping(
            bundle["task_categories"]
        )

    def read_task(self, task_name: str) -> str | None:
        return self._tasks.get(task_name)

    def read_pattern(self, category: str) -> str | None:
        return self._patterns.get(category)

    def iter_patterns(self) -> list[tuple[str, str]]:
        return sorted(self._patterns.items())

    def task_category_mapping(self) -> dict[str, str]:
        return self._task_category_mapping

    def assert_present(self) -> None:
        """The bundled corpus may be empty: memory ships without built-in
        tasks or patterns and is populated by the user's own runs."""
        return None


def build_memory_bundle(memory_root: Path) -> dict:
    """Embed the built-in memory corpus rooted at ``memory_root`` as a single dict.

    Returns ``{"tasks": {stem: text}, "patterns": {stem: text}, "task_categories":
    <validated mapping>}``: every ``tasks/*.md`` and ``patterns/*.md`` keyed by its
    filename stem (no ``.md``), plus the validated scalar mapping from ``task_categories.json``. This is exactly the shape ``BundleMemoryStore`` reads.

    The import freeze below builds ``_BUILTIN_MEMORY_BUNDLE`` from it.
    """
    tasks = {
        f.stem: f.read_text(encoding="utf-8")
        for f in sorted((memory_root / "tasks").glob("*.md"))
    }
    patterns = {
        f.stem: f.read_text(encoding="utf-8")
        for f in sorted((memory_root / "patterns").glob("*.md"))
    }
    task_categories = MemoryStore(memory_root).task_category_mapping()
    return {"tasks": tasks, "patterns": patterns, "task_categories": task_categories}


# The built-in corpus, read from disk once at import (which cli.py forces at
# launch before command dispatch), so a mid-run `git` update to memory/ cannot
# swap a task/pattern/mapping under the running process.
_BUILTIN_MEMORY_BUNDLE = build_memory_bundle(BUILTIN_MEMORY_ROOT)


def builtin_source() -> MemorySource:
    """The built-in corpus read interface, snapshotted at launch: the in-memory ``MEMORY_BUNDLE``-shaped dict built at import is served through ``BundleMemoryStore``, and the corpus is never re-read from disk after import."""
    return BundleMemoryStore(_BUILTIN_MEMORY_BUNDLE)

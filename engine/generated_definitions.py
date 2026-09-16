"""Activate generated Definition packages in this Python process.

A generated package is ordinary run-owned Python source. Activation is one
operation: make a published package importable under its stable module name,
with its package-local prompts available, through ordinary Python import.
Every family invariant fires where it fires for built-in and extension code --
at class formation and construction -- never here.
"""

from __future__ import annotations

import importlib
import importlib.util
import sys
import threading
from pathlib import Path
from types import ModuleType

from infra.util.paths import is_within

AUTHORING_FACADES = (
    "engine.builtin.aibuildai",
    "engine.builtin.meta.authoring",
    "engine.capability",
    "engine.composite",
    "engine.durable_execution",
    "engine.execution_output",
    "engine.failure",
    "engine.search",
    "engine.work_unit.agent",
    "engine.work_unit.program",
)


class GeneratedDefinitionError(Exception):
    """A published generated package could not be loaded as written."""


_LOCK = threading.RLock()


def _definition_package_dir(run_home: Path, package_relpath: str) -> Path:
    """Return one real generated package path after checking its saved relative path."""
    relative = Path(package_relpath)
    if relative.is_absolute() or ".." in relative.parts:
        raise ValueError(f"generated package leaves the run home: {package_relpath!r}")
    root = run_home.resolve()
    step = root
    for part in relative.parts:
        step /= part
        if step.is_symlink():
            raise TypeError(f"generated package path cannot contain symlink {step}")
    package_dir = (root / relative).resolve()
    if not is_within(package_dir, root):
        raise ValueError(f"generated package leaves the run home: {package_relpath!r}")
    return package_dir


def _owned(name: str, package: str) -> bool:
    return name == package or name.startswith(f"{package}.")


def activate_generated_definition(
    run_home: Path,
    *,
    module_name: str,
    package_relpath: str,
    requested_module: str | None = None,
) -> ModuleType:
    """Load one published generated package, or one lazy module in it, once.

    The isolated package verifier is the fallible pre-publication phase; here
    a failure raises ``GeneratedDefinitionError`` and the caller fails
    honestly. Like a failed ordinary ``import``, a package whose top-level
    execution fails leaves ``sys.modules`` without its entries so a later
    attempt starts clean; nothing else is rolled back.
    """
    root = run_home.resolve()
    package_dir = _definition_package_dir(root, package_relpath)
    if requested_module is not None and not _owned(requested_module, module_name):
        raise ValueError(
            f"generated module {requested_module!r} is outside {module_name!r}"
        )
    with _LOCK:
        loaded = sys.modules.get(module_name)
        identity = (root, package_relpath)
        if (
            loaded is not None
            and getattr(loaded, "_aibuildai_definition", None) != identity
        ):
            raise AssertionError(
                f"{module_name} is already loaded from another run path"
            )
        try:
            if loaded is None:
                init_path = package_dir / "__init__.py"
                spec = importlib.util.spec_from_file_location(
                    module_name,
                    init_path,
                    submodule_search_locations=[str(package_dir)],
                )
                if spec is None or spec.loader is None:
                    raise AssertionError(f"cannot import generated package {init_path}")
                loaded = importlib.util.module_from_spec(spec)
                sys.modules[module_name] = loaded
                spec.loader.exec_module(loaded)
                setattr(loaded, "_aibuildai_definition", identity)
                prompts_dir = package_dir / "prompts"
                if prompts_dir.is_dir():
                    from engine.work_unit.agent.prompt import (
                        register_extension_templates,
                    )

                    register_extension_templates(
                        str(prompts_dir),
                        prefix=module_name.removeprefix("aibuildai_"),
                    )
            if requested_module is not None:
                importlib.import_module(requested_module)
        except Exception as exc:
            if (
                getattr(sys.modules.get(module_name), "_aibuildai_definition", None)
                != identity
            ):
                for name in tuple(sys.modules):
                    if _owned(name, module_name):
                        del sys.modules[name]
            raise GeneratedDefinitionError(
                f"generated package {module_name} failed to load: {exc}"
            ) from exc
        return loaded


def activate_generated_type(type_name: str) -> None:
    """Activate the lazy module named by one generated execution type."""
    requested_module, separator, _ = type_name.partition(":")
    module_name = requested_module.partition(".")[0]
    package = sys.modules.get(module_name)
    if (
        not separator
        or package is None
        or not module_name.startswith("aibuildai_meta_")
    ):
        return
    run_home, package_relpath = getattr(package, "_aibuildai_definition")
    activate_generated_definition(
        run_home,
        module_name=module_name,
        package_relpath=package_relpath,
        requested_module=requested_module,
    )


def publish_generated_definition(module_name: str, package_relpath: str) -> None:
    """Record one generated package's publication for this run.

    The recording is unconditional. Inside a workflow this call is a durable
    step, and a replay that skips a step the original run recorded shifts
    every later step onto the wrong record, so no caller may decide from
    folded state whether to record. The fold owns the idempotence instead: a
    repeat of the same module at the same path keeps the one entry, and only
    a module that moved is a conflict."""
    from engine.durable_execution import record_event
    from engine.event.events import GeneratedDefinitionPublished

    record_event(
        GeneratedDefinitionPublished,
        None,
        module_name=module_name,
        package_relpath=package_relpath,
    )

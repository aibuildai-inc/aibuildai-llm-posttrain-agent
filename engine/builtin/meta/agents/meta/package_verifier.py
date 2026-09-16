"""Check a generated Search package before its required semantic review.

Called from the Meta verifier Program's own worker, which the run throws away
after the Action, so nothing it activates stays registered in the manager. The
check is intentionally small and mechanical:

- each source file parses, and its first-party imports come from the authoring
  facades and name only what each facade's own ``__all__`` exports;
- the package activates through the product's own loader under the module name
  the run will use, and every package-local module imports, so class formation
  fires every family invariant exactly where built-in and extension code face
  it;
- every concrete Agent the package ships resolves a real registered prompt
  template through the same boundary the live spec builder uses;
- the minimal Meta handoff succeeds: the package exports ``SEARCH_TYPE``, a
  concrete plain child Search returning ``SearchOutput``, and the submitted
  Input payload builds its invocation.

Generated code is trusted product-authored code. This is an architectural
boundary, not a security sandbox.
"""

from __future__ import annotations

import ast
import os
import sys
from importlib import import_module
from pathlib import Path

from pydantic import ValidationError

from engine.generated_definitions import (
    AUTHORING_FACADES,
    GeneratedDefinitionError,
    _definition_package_dir,
    _owned,
    activate_generated_definition,
)
from engine.builtin.meta.authoring import _make_search_invocation, _validate_search_type
from engine.work_unit.agent.base import Agent
from engine.work_unit.agent.prompt import agent_template_for
from aibuildai_version import RUNTIME_PACKAGES, RUNTIME_TOP_LEVEL_MODULES



def _callee(func: ast.expr) -> str | None:
    """The constructed name, whether it is written plain or on a module."""
    if isinstance(func, ast.Name):
        return func.id
    return func.attr if isinstance(func, ast.Attribute) else None


def _omits_capability(call: ast.Call) -> bool:
    """Whether one Action start says nothing about what that call may spend.

    A capability belongs to the invocation, not to the identity answering it,
    so this reads the ``spawn`` rather than the construction above it. Both
    spellings count: ``self.ctx.spawn(...)`` and a bare ``spawn(...)``."""
    if _callee(call.func) != "spawn":
        return False
    return not any(keyword.arg == "capability" for keyword in call.keywords)


# The first-party import roots, read off the one runtime manifest.
_FIRST_PARTY_ROOTS = frozenset(RUNTIME_PACKAGES) | {
    name.removesuffix(".py") for name in RUNTIME_TOP_LEVEL_MODULES
}
class PackageContractError(Exception):
    """A generated package broke its authored package contract."""


def _check_imports(source: Path) -> None:
    """Enforce the one generic SDK import boundary on one source file."""
    try:
        tree = ast.parse(source.read_text(encoding="utf-8"), filename=str(source))
    except (OSError, SyntaxError) as exc:
        raise PackageContractError(f"{source}: {exc}") from exc
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            first_party = [
                alias.name
                for alias in node.names
                if alias.name.partition(".")[0] in _FIRST_PARTY_ROOTS
            ]
            if first_party:
                raise PackageContractError(
                    f"{source}: generated code must import public names with "
                    f"from ... import ..., not import {', '.join(first_party)}"
                )
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            if node.module.partition(".")[0] not in _FIRST_PARTY_ROOTS:
                continue
            if node.module not in AUTHORING_FACADES:
                raise PackageContractError(
                    f"{source}: {node.module} is not a public SDK module; "
                    f"the authoring facades are {', '.join(AUTHORING_FACADES)}"
                )
            exported = getattr(import_module(node.module), "__all__")
            missing = sorted(
                {alias.name for alias in node.names} - set(exported)
            )
            if missing:
                raise PackageContractError(
                    f"{source}: {node.module} does not export {', '.join(missing)}"
                )
        elif isinstance(node, ast.Call) and _omits_capability(node):
            # Every Action says what that one call may spend. Generated code
            # is written by a model, so an omission found here is found before
            # the Search runs instead of whenever that line is reached.
            raise PackageContractError(
                f"{source}:{node.lineno}: spawn(...) declares no capability="
            )


def _package_modules(module_name: str, package_dir: Path) -> list[tuple[Path, str]]:
    """Map each package-local Python file to the module name that imports it."""

    def reject_unreadable(error: OSError) -> None:
        raise PackageContractError(
            f"generated package directory cannot be read: {error.filename}: {error.strerror}"
        ) from error

    modules: list[tuple[Path, str]] = []
    sources_by_module: dict[str, Path] = {}
    for root, dirs, files in os.walk(
        package_dir, followlinks=False, onerror=reject_unreadable
    ):
        directory = Path(root)
        for name in (*dirs, *files):
            if (directory / name).is_symlink():
                raise PackageContractError(
                    "generated package must be a self-contained source tree, "
                    f"not symlink {directory / name}"
                )
        for name in files:
            source = directory / name
            if source.suffix != ".py":
                continue
            relative = source.relative_to(package_dir).with_suffix("")
            parts = (
                relative.parts[:-1] if relative.name == "__init__" else relative.parts
            )
            if any(not part.isidentifier() for part in parts):
                raise PackageContractError(
                    f"{source}: its path is not importable as a module name"
                )
            submodule = ".".join((module_name, *parts))
            prior = sources_by_module.get(submodule)
            if prior is not None:
                # foo.py beside foo/__init__.py: one module identity, so the
                # second file would never execute and its Definitions would
                # silently not exist.
                raise PackageContractError(
                    f"two files map to the same module {submodule}: "
                    f"{prior} and {source}"
                )
            sources_by_module[submodule] = source
            modules.append((source, submodule))
    return sorted(modules)


def verify(
    run_home: Path,
    *,
    package_relpath: str,
    module_name: str,
    input_payload: dict[str, object],
) -> None:
    try:
        package_dir = _definition_package_dir(run_home, package_relpath)
    except (TypeError, ValueError) as exc:
        raise PackageContractError(str(exc)) from exc
    if not (package_dir / "__init__.py").is_file():
        raise PackageContractError(f"generated package needs {package_dir}/__init__.py")
    modules = _package_modules(module_name, package_dir)
    for source, _ in modules:
        _check_imports(source)
    try:
        package = activate_generated_definition(
            run_home, module_name=module_name, package_relpath=package_relpath
        )
        # Import every package-local module through the same activation the
        # run's lazy loads use, so class formation and prompt binding fire for
        # everything the package ships, not only what __init__ imports.
        for _, submodule in modules:
            activate_generated_definition(
                run_home,
                module_name=module_name,
                package_relpath=package_relpath,
                requested_module=submodule,
            )
    except GeneratedDefinitionError as exc:
        raise PackageContractError(str(exc)) from exc
    # Every concrete Agent the package ships must resolve a real registered
    # template through the one prompt boundary the live spec builder uses, so
    # a prompt_template naming a missing file fails here, not at the first
    # spec build inside a live run. The Agent family owns the answer to
    # "concrete or shared base"; this only reads it.
    checked: set[type] = set()
    for _, submodule in modules:
        for value in vars(sys.modules[submodule]).values():
            if (
                not isinstance(value, type)
                or not issubclass(value, Agent)
                or not value._is_concrete_definition()
                or value in checked
                or not _owned(value.__module__, module_name)
            ):
                continue
            checked.add(value)
            try:
                agent_template_for(value)
            except AssertionError as exc:
                raise PackageContractError(str(exc)) from exc
    try:
        search_type = _validate_search_type(package)
    except (AssertionError, TypeError) as exc:
        raise PackageContractError(str(exc)) from exc
    try:
        _make_search_invocation(search_type, input_payload)
    except ValidationError as exc:
        raise PackageContractError(
            f"{search_type.__name__} cannot be built from input_payload: {exc}"
        ) from exc

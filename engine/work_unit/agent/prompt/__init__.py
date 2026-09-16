"""Load, render, and assemble every pipeline Agent prompt.

Python computes values and passes them in ``ctx``. A template owns the text. ``StrictUndefined`` makes a missing value fail at render time. ``autoescape=False`` keeps paths and JSON unchanged. ``keep_trailing_newline=True`` keeps rendered output byte-stable.
"""

from __future__ import annotations

import sys
from pathlib import Path
from typing import TYPE_CHECKING

from jinja2 import meta
from jinja2 import (
    ChoiceLoader,
    DictLoader,
    Environment,
    FileSystemLoader,
    PrefixLoader,
    StrictUndefined,
    TemplateNotFound,
)
from jinja2.runtime import Macro

if TYPE_CHECKING:
    from engine.work_unit.agent.base import Agent


def _read_templates(root: Path, prefix: str | None) -> dict[str, str]:
    templates: dict[str, str] = {}
    for file_path in sorted(root.rglob("*.j2")):
        if not file_path.is_file():
            continue
        key = file_path.relative_to(root).as_posix()
        templates[key if prefix is None else f"{prefix}/{key}"] = file_path.read_text(
            encoding="utf-8"
        )
    return templates


def build_prompt_templates(templates_root: Path) -> dict[str, str]:
    """Map every shipped template to its key: the shared ones by their own path, each built-in package's by ``<package>/<path>``.

    This is the single file scan every run uses. A package's templates are keyed under the package name for the same reason a generated package's are: two packages may define ``agent/designer.j2`` and neither may shadow the other.
    """
    templates = _read_templates(templates_root, None)
    builtin_root = templates_root.parents[2] / "builtin"
    for package_prompts in sorted(builtin_root.glob("*/prompts")):
        if not package_prompts.is_dir():
            continue
        templates.update(_read_templates(package_prompts, package_prompts.parent.name))
    return templates


# One in-memory map, read once at import, so a source update cannot change a
# prompt while old code still runs.
_BUILTIN_LOADER = DictLoader(
    build_prompt_templates(Path(__file__).resolve().parent)
)

# Extension templates are added after the built-in loader. They can add new
# names, but they cannot replace shipped templates.
_LOADER = ChoiceLoader([_BUILTIN_LOADER])

_ENV = Environment(
    loader=_LOADER,
    undefined=StrictUndefined,
    autoescape=False,
    keep_trailing_newline=True,
    trim_blocks=True,
    lstrip_blocks=True,
)
_ENV.filters["repr"] = repr

_RENDER_ARGUMENTS = {
    "search": (),
    "agent": ("input", "schema_json"),
}


def _package_prefix(definition_type: type) -> str | None:
    """The template prefix of the package that defines one Definition, if any.

    A first-party built-in package and a run-generated package are keyed the
    same way, so the same local template name may appear in both without
    either shadowing the other."""
    module = definition_type.__module__
    if module.startswith("engine.builtin."):
        return module.split(".")[2]
    root = module.partition(".")[0]
    package = sys.modules.get(root)
    if package is not None and hasattr(package, "_aibuildai_definition"):
        return root.removeprefix("aibuildai_")
    return None


def _validate_macro(
    template_name: str,
    macro_name: str,
    macro: object,
    arguments: tuple[str, ...],
) -> None:
    actual = macro.arguments if isinstance(macro, Macro) else None
    catches = (
        (macro.caller, macro.catch_kwargs, macro.catch_varargs)
        if isinstance(macro, Macro)
        else None
    )
    if actual != arguments or catches != (False, False, False):
        raise AssertionError(
            f"{template_name!r} must define plain {macro_name}{arguments}; "
            f"found arguments={actual}, caller/kwargs/varargs={catches}"
        )


def _is_agent_template(template_name: str) -> bool:
    return template_name.startswith("agent/") or "/agent/" in template_name


def _validate_generated_template_names(
    environment: Environment, template_name: str, source: str
) -> None:
    """Reject names a generated template cannot resolve when its macro runs."""
    names = meta.find_undeclared_variables(environment.parse(source))
    unknown = sorted(names - environment.globals.keys())
    if unknown:
        raise AssertionError(
            f"{template_name!r} uses undefined Jinja name(s): {unknown}. "
            "Use only macro arguments and built-in Jinja names."
        )


def validate_prompt_template(template_name: str, arguments: tuple[str, ...]) -> None:
    """Fail if one Search or Agent template has the wrong macros."""
    module = _ENV.get_template(template_name).module
    _validate_macro(template_name, "render", getattr(module, "render", None), arguments)
    if _is_agent_template(template_name) and hasattr(module, "details"):
        _validate_macro(
            template_name, "details", getattr(module, "details"), ("input",)
        )


def register_extension_templates(
    templates_dir: str, prefix: str | None = None
) -> object | None:
    """Add templates from one extension directory, optionally under a prefix.

    When ``prefix`` is given, templates are accessible only as ``prefix/relative``, so two packages may define the same local name without collision.
    """
    root = Path(templates_dir)
    if not root.is_dir():
        return None
    fs_loader = FileSystemLoader(templates_dir)
    extension_loader = (
        PrefixLoader({prefix: fs_loader}) if prefix is not None else fs_loader
    )
    combined_loader = ChoiceLoader([*_LOADER.loaders, extension_loader])
    task_env = Environment(
        loader=combined_loader,
        undefined=StrictUndefined,
        autoescape=False,
        keep_trailing_newline=True,
        trim_blocks=True,
        lstrip_blocks=True,
    )
    task_env.filters["repr"] = repr
    if prefix is not None:
        names = lambda relative: f"{prefix}/{relative.as_posix()}"
    else:
        names = lambda relative: relative.as_posix()
    for file_path in sorted(root.rglob("*.j2")):
        if not file_path.is_file():
            continue
        relative = file_path.relative_to(root)
        template_name = names(relative)
        arguments = _RENDER_ARGUMENTS.get(relative.parts[0])
        if arguments is not None:
            if prefix is not None:
                _validate_generated_template_names(
                    task_env,
                    template_name,
                    file_path.read_text(encoding="utf-8"),
                )
            module = task_env.get_template(template_name).module
            _validate_macro(
                template_name, "render", getattr(module, "render", None), arguments
            )
            if _is_agent_template(template_name) and hasattr(module, "details"):
                _validate_macro(
                    template_name, "details", getattr(module, "details"), ("input",)
                )
    _LOADER.loaders = [*_LOADER.loaders, extension_loader]
    if _ENV.cache is not None:
        _ENV.cache.clear()
    return extension_loader


def agent_template_for(agent_type: "type[Agent]") -> str:
    """Resolve, check, and return the template name one Agent type renders from.

    A generated package's templates register under the package prefix, so two packages may reuse one local name. An Agent authored in such a package names its template by the authored form (``agent/x.j2``); this derives the prefixed name from the class's own defining package, then requires that the template is registered and defines the agent macro contract. This is the one prompt boundary for built-in, extension, and generated Agents: the isolated package verifier and the live spec builder both call it, so a prompt that cannot render fails at verification, not at the first spec build.
    """
    template = agent_type.prompt_template
    if template is None:
        raise AssertionError(f"{agent_type.__name__} declares no prompt_template")
    prefix = _package_prefix(agent_type)
    if prefix is not None:
        template = f"{prefix}/{template}"
    try:
        validate_prompt_template(template, _RENDER_ARGUMENTS["agent"])
    except TemplateNotFound as exc:
        raise AssertionError(
            f"{agent_type.__name__} declares prompt_template "
            f"{agent_type.prompt_template!r} but no template named "
            f"{template!r} is registered"
        ) from exc
    return template


def search_template_for(search_type: type) -> str:
    """Resolve and check the template name one Search type renders from.

    The same package rule the Agents follow, so a package's own ``search.j2``
    never collides with another package's."""
    template = getattr(search_type, "prompt_template", None)
    if not isinstance(template, str):
        raise AssertionError(f"{search_type.__name__} declares no prompt_template")
    prefix = _package_prefix(search_type)
    if prefix is not None:
        template = f"{prefix}/{template}"
    try:
        validate_prompt_template(template, _RENDER_ARGUMENTS["search"])
    except TemplateNotFound as exc:
        raise AssertionError(
            f"{search_type.__name__} declares prompt_template "
            f"{getattr(search_type, 'prompt_template')!r} but no template named "
            f"{template!r} is registered"
        ) from exc
    return template


def render(template_name: str, ctx: dict) -> str:
    """Render one named template. Missing values fail clearly."""
    return _ENV.get_template(template_name).render(**ctx)


def render_prompt(**values: object) -> str:
    """Render one complete Agent prompt from its values."""
    return render("prompt.j2", values)


INTEGRITY_VIOLATIONS = [
    {
        "id": "plagiarism",
        "rule": (
            "Do NOT search for or use solutions, writeups, discussion posts, or leaderboard "
            "approaches for this specific task or competition. If search results return "
            "competition-specific solutions (e.g. Kaggle writeups, GitHub solution repos, "
            "winning approach posts), do NOT open or read them. You MAY search for general "
            "domain knowledge: architecture designs, SOTA models, training techniques, "
            "loss functions, data augmentation strategies, and academic papers."
        ),
    },
    {
        "id": "data_leakage",
        "rule": (
            "Use only design data that is visible in your sandbox. You may use every visible "
            "feature and representation, including test-time training. Do NOT seek or use hidden "
            "target labels, the score program, or any other oracle that reveals the answer."
        ),
    },
    {
        "id": "fabricated_metrics",
        "rule": (
            "Reported metrics MUST accurately reflect actual training results. A "
            "reported score must come from held-out data, never from training data. "
            "Do NOT report metrics that were not actually computed."
        ),
    },
    {
        "id": "hallucinated_references",
        "rule": (
            "Do NOT cite playbook principles, papers, or external sources you have not "
            "actually read. Every reference must be real and verified."
        ),
    },
]

"""Render the starter config (`example.yaml` / `aibuildai config`).

GENERATED from the Pydantic config models in config.py -- that module stays the single source of truth for defaults and per-option docs; this one owns only how they are rendered for a person. `example_yaml_text` is what `aibuildai config` prints; `python -m startup.example_yaml` rewrites the committed reference copy at the repo root with that note."""

import dataclasses
import enum
import io
from pathlib import Path
from typing import TYPE_CHECKING
from typing import Literal, Union, get_args, get_origin

from pydantic import BaseModel, TypeAdapter
from pydantic.fields import FieldInfo
from pydantic_core import PydanticUndefined

# _ConfigBaseModel is private by convention; this module is the one renderer of
# that model tree, so it imports the base to recognize a section.
from config import AgentConfig, _ConfigBaseModel

if TYPE_CHECKING:
    from ruamel.yaml import YAML
    from ruamel.yaml.comments import CommentedMap
    from ruamel.yaml.nodes import ScalarNode
    from ruamel.yaml.representer import RoundTripRepresenter


_EXAMPLE_YAML_BANNER = (
    "# aibuildai config -- AUTO-GENERATED, do not hand-edit this file.\n"
    "#\n"
)
_EXAMPLE_YAML_MAINTAINER_NOTE = (
    "# GENERATED from the Pydantic config models in config.py (the single source of\n"
    "# truth for defaults AND per-option docs). To change a default/comment or add/\n"
    "# remove an option, edit config.py and regenerate:\n"
    "#   `python -m startup.example_yaml`.\n"
    "#\n"
)
_EXAMPLE_YAML_USER_GUIDE = (
    "# To configure a run, copy this file and edit your COPY's values, then:\n"
    "#   aibuildai run <your copy>\n"
    "# Lines marked `REQUIRED -- replace` have no default and must be supplied.\n"
    "\n"
)
_EXAMPLE_YAML_INDENT = 2


def _is_section(annotation: object) -> bool:
    """A nested config sub-model the generator recurses into (renders as a YAML block), as opposed to a leaf whose value is serialized in place. Only ``_ConfigBaseModel`` subclasses are sections; everything else (primitives, enums, optionals, and other domain value types) is a leaf."""
    return _section_type(annotation) is not None


def _section_type(annotation: object) -> type[BaseModel] | None:
    if isinstance(annotation, type) and issubclass(annotation, _ConfigBaseModel):
        return annotation
    return None


def _unwrap_optional(annotation: object) -> object:
    if get_origin(annotation) is Union:
        args = [a for a in get_args(annotation) if a is not type(None)]
        if len(args) == 1:
            return args[0]
    return annotation


def _enum_choices(annotation: object) -> list[str] | None:
    base = _unwrap_optional(annotation)
    if get_origin(base) is Literal:
        return [str(a) for a in get_args(base)]
    if isinstance(base, type) and issubclass(base, enum.Enum):
        return [str(m.value) for m in base]
    return None


def _bounds(field: FieldInfo) -> str | None:
    parts: list[str] = []
    for meta in field.metadata:
        for attr, sym in (("ge", ">="), ("le", "<="), ("gt", ">"), ("lt", "<")):
            value = getattr(meta, attr, None)
            if value is not None:
                parts.append(f"{sym} {value}")
    return ", ".join(parts) if parts else None


def _is_required(field: FieldInfo) -> bool:
    return field.default is PydanticUndefined and field.default_factory is None


def _placeholder(field: FieldInfo) -> object:
    extra = field.json_schema_extra or {}
    if not (isinstance(extra, dict) and "placeholder" in extra):
        raise AssertionError(
            "a required field (no default) must carry "
            "json_schema_extra={'placeholder': ...} for the generator"
        )
    value = extra["placeholder"]
    # A callable derives its value at render time.
    if callable(value):
        value = value()
    return value


def _default_search_input() -> dict:
    """The starter's live ``search.input`` block, derived from the default kind's own Input model: every REQUIRED field appears with its declared placeholder. The generated file validates out of the box, and no second handwritten dictionary exists to drift from the model."""
    from engine.builtin import search_type_for_kind

    from config import SearchConfig

    kind = SearchConfig.model_fields["kind"].default
    model = search_type_for_kind(kind).parameters_type
    return {
        name: _placeholder(field)
        for name, field in model.model_fields.items()
        if _is_required(field)
    }


def _leaf_value(field: FieldInfo) -> object:
    if field.default is not PydanticUndefined:
        raw = field.default
    elif field.default_factory is not None:
        raw = field.get_default(call_default_factory=True)
    else:
        return _placeholder(field)
    # Serialize the DECLARED default to a YAML-native value without running any
    # field validator (TypeAdapter.dump_python is serialization, not validation),
    # so e.g. user_dir stays the raw "~/.aibuildai/memory".
    return TypeAdapter(field.annotation).dump_python(raw, mode="json")


def _offered_choices(field: FieldInfo, path: tuple[str, ...]) -> list[str]:
    """The value hints for one field, generated from the annotation."""
    del path
    return list(_enum_choices(field.annotation) or ())


def _search_kind_lines() -> list[str]:
    """One line per offered search method, written by the packages themselves: the starter config's method list is derived from the kind-to-package map instead of a hand-written copy that goes stale."""
    # Deferred import: a built-in package imports config at module top (a real
    # runtime cycle), so the renderer reaches the map only at render time.
    from engine.builtin import offered_kind_docs

    return [f"'{kind}' = {doc}" for kind, doc in offered_kind_docs()]


def _search_input_lines() -> list[str]:
    """One block per offered search method listing its own parameter model's fields, written by those models themselves: ``search.input`` validates directly against the selected package's parameters, so the starter config's field list is derived from them instead of a hand-written copy. Rendering the starter config is the one operation that reads every offered package; a run reads only its own."""
    from engine.builtin import offered_kinds, search_type_for_kind

    lines: list[str] = []
    for kind in offered_kinds():
        model = search_type_for_kind(kind).parameters_type
        if not model.model_fields:
            lines.append(f"fields for kind '{kind}': (none)")
            continue
        lines.append(f"fields for kind '{kind}':")
        for name, field in model.model_fields.items():
            if _is_required(field):
                value = "REQUIRED"
            else:
                value = f"default {field.get_default(call_default_factory=True)!r}"
            head = field.description.strip().splitlines()[0] if field.description else ""
            lines.append(f"  {name} ({value})" + (f" -- {head}" if head else ""))
    return lines


def _comment_lines(field: FieldInfo, path: tuple[str, ...]) -> list[str]:
    lines: list[str] = []
    if _is_section(field.annotation) and field.annotation.__doc__:
        lines.extend(field.annotation.__doc__.strip().splitlines())
    if field.description:
        lines.extend(field.description.strip().splitlines())
    if path == ("search", "kind"):
        lines.extend(_search_kind_lines())
    if path == ("search", "input"):
        lines.extend(_search_input_lines())
    choices = _offered_choices(field, path)
    if choices:
        lines.append("one of: " + " | ".join(choices))
    bounds = _bounds(field)
    if bounds:
        lines.append(bounds)
    # The REQUIRED marker belongs on leaf keys the user fills, not on a required
    # section header (its required leaf children each carry their own marker).
    if _is_required(field) and not _is_section(field.annotation):
        lines.append("REQUIRED -- replace")
    # An opaque empty-container field (e.g. dict[str, object] defaulting to {}) carries
    # a raw example snippet in json_schema_extra["example"]; render it as a
    # commented, ready-to-uncomment skeleton below the description.
    extra = field.json_schema_extra
    if isinstance(extra, dict) and extra.get("example"):
        lines.append("Example (uncomment + edit):")
        lines.extend("  " + ln for ln in str(extra["example"]).splitlines())
    return [ln.rstrip() for ln in lines]


def _has_example(field: FieldInfo) -> bool:
    return isinstance(field.json_schema_extra, dict) and bool(
        field.json_schema_extra.get("example")
    )


def _is_advanced(field: FieldInfo) -> bool:
    """A field tagged json_schema_extra={'advanced': True}: kept in the model (fully settable, unchanged default) but rendered in the bottom 'Advanced' block instead of inline in its concern, so the default example body shows only the knobs users routinely set. Advanced never rejects."""
    extra = field.json_schema_extra
    return isinstance(extra, dict) and bool(extra.get("advanced"))


def _is_generated(field: FieldInfo) -> bool:
    """A manager-filled field that must not appear in user config examples."""
    extra = field.json_schema_extra
    return isinstance(extra, dict) and bool(extra.get("generated"))


def _hides(hidden: tuple[str, ...], path: tuple[str, ...], name: str) -> bool:
    """True when this build hides the field at ``path + (name,)``. The dotted path is matched whole, so a hidden block name (``verifier``) stops the walk at the block and a hidden leaf (``llm.auto``) stops at the leaf. Both example halves ask this same question, which is what keeps a hidden field out of the body AND out of the Advanced block: the two are only orthogonal if one filter feeds both. A hidden VALUE is not here: that field stays visible, its refused values leave its value hint through _comment_lines, and refusing them is _reject_hidden_features's alone."""
    return ".".join(path + (name,)) in hidden


@dataclasses.dataclass(frozen=True)
class _Entry:
    """One field of the config tree, walked exactly once. A section carries its (already filtered) children; a leaf carries None."""

    name: str
    path: tuple[str, ...]
    field: FieldInfo
    advanced: bool
    children: "tuple[_Entry, ...] | None"


def _walk(
    model_cls: type[BaseModel], path: tuple[str, ...], hidden: tuple[str, ...]
) -> "tuple[_Entry, ...]":
    """THE one pass over the config tree. Every per-field property (generated, hidden, advanced) is decided here, once, so a property cannot reach one presenter and miss the other -- the defect class behind orphan comments, and behind an advanced-tagged section silently dropping its non-advanced children from both halves."""
    entries: list[_Entry] = []
    for name, field in model_cls.model_fields.items():
        if _is_generated(field) or _hides(hidden, path, name):
            continue
        section_cls = _section_type(field.annotation)
        if section_cls is not None and _is_advanced(field):
            raise AssertionError(
                f"advanced is leaf-only, but section {'.'.join(path + (name,))} "
                "is tagged: tag its leaves instead (a tagged section would drop "
                "its non-advanced children from both halves of the render)"
            )
        entries.append(
            _Entry(
                name=name,
                path=path + (name,),
                field=field,
                advanced=_is_advanced(field),
                children=(
                    _walk(section_cls, path + (name,), hidden)
                    if section_cls is not None
                    else None
                ),
            )
        )
    return tuple(entries)


def _body_map(entries: "tuple[_Entry, ...]") -> "CommentedMap":
    from ruamel.yaml.comments import CommentedMap

    cm = CommentedMap()
    for entry in entries:
        if entry.children is None and entry.advanced:
            continue  # leaf carried by the bottom Advanced block instead
        if entry.children is not None:
            rendered = _body_map(entry.children)
            if not rendered:
                # Nothing in it survived the walk's filters plus this skip --
                # every child is advanced, generated or hidden. An empty
                # `name: {}` stub would teach the reader nothing; an advanced
                # child is already carried by the bottom block, and a generated
                # or hidden one belongs in no user example at all.
                continue
            cm[entry.name] = rendered
        elif entry.path == ("search", "input"):
            cm[entry.name] = _default_search_input()
        else:
            cm[entry.name] = _leaf_value(entry.field)
        first = len(cm) == 1
        lines = _comment_lines(entry.field, entry.path)
        if lines:
            before = "\n".join(lines)
            # Offset a multi-line example block from the preceding sibling with a
            # blank line (a leading newline renders as a true blank, not `#`), but
            # not when this is the first key in its mapping.
            if not first and _has_example(entry.field):
                before = "\n" + before
            cm.yaml_set_comment_before_after_key(
                entry.name,
                before=before,
                indent=(len(entry.path) - 1) * _EXAMPLE_YAML_INDENT,
            )
    return cm


def _represent_none_as_null(
    representer: "RoundTripRepresenter", _data: None
) -> "ScalarNode":
    # ruamel renders None as an empty value by default; emit an explicit `null`
    # so the starter config reads as a clear, fillable reference.
    return representer.represent_scalar("tag:yaml.org,2002:null", "null")


def _yaml() -> "YAML":
    from ruamel.yaml import YAML

    y = YAML()
    y.indent(
        mapping=_EXAMPLE_YAML_INDENT,
        sequence=_EXAMPLE_YAML_INDENT + 2,
        offset=_EXAMPLE_YAML_INDENT,
    )
    y.width = 4096  # never wrap a long scalar/comment
    y.representer.add_representer(type(None), _represent_none_as_null)
    # A value object reused across keys (one starter budget shared by several
    # roles) must render as the plain value each time, never as an &id anchor
    # plus *id aliases -- the starter config is hand-edited YAML, and an alias
    # inside its REQUIRED block ships syntax most users cannot read.
    y.representer.ignore_aliases = lambda _data: True
    return y


def _advanced_leaves(entries: "tuple[_Entry, ...]") -> "list[_Entry]":
    leaves: list[_Entry] = []
    for entry in entries:
        if entry.children is not None:
            leaves.extend(_advanced_leaves(entry.children))
        elif entry.advanced:
            leaves.append(entry)
    return leaves


def _advanced_leaf_value(field: FieldInfo) -> object:
    """Value rendered for an advanced field in the bottom block. A derived field defaults to None (= auto-derive), for which a literal `null` is a no-op override; when such a field carries json_schema_extra['example'], render that concrete example so the uncommented line is a usable override. Fields without an example keep their declared default (unchanged)."""
    extra = field.json_schema_extra
    if isinstance(extra, dict) and "example" in extra:
        return extra["example"]
    return _leaf_value(field)


def _advanced_map(entries: "tuple[_Entry, ...]") -> "CommentedMap":
    """Nested CommentedMap mirroring each advanced leaf's concern path, each leaf valued at its declared default (or its example), with the description PLUS the same choice and bound hints the body half shows as the eol comment, so neither half under-documents a field. Dumped + fully commented this is the bottom block; uncommenting a concern header + a line yields valid YAML AgentConfig accepts."""
    from ruamel.yaml.comments import CommentedMap

    root = CommentedMap()
    for entry in _advanced_leaves(entries):
        node = root
        for seg in entry.path[:-1]:
            if seg not in node:
                node[seg] = CommentedMap()
            node = node[seg]
        node[entry.name] = _advanced_leaf_value(entry.field)
        parts: list[str] = []
        if entry.field.description:
            parts.append(entry.field.description.strip().replace("\n", " "))
        if entry.path == ("search", "kind"):
            parts.extend(_search_kind_lines())
        choices = _offered_choices(entry.field, entry.path)
        if choices:
            parts.append("one of: " + " | ".join(choices))
        bounds = _bounds(entry.field)
        if bounds:
            parts.append(bounds)
        if parts:
            node.yaml_add_eol_comment("; ".join(parts), entry.name)
    return root


def render_example_yaml(
    hidden: tuple[str, ...] = (),
    *,
    include_maintainer_note: bool,
) -> str:
    """The full starter config: selected header text + the rendered ``AgentConfig`` + the bottom Advanced block (commented overrides for advanced fields). The Advanced block is empty when the model declares no advanced field.

    ``hidden`` names dotted config paths to leave out of both halves. ``include_maintainer_note`` adds the regeneration instructions that the committed reference carries."""
    entries = _walk(AgentConfig, (), hidden)
    body = io.StringIO()
    _yaml().dump(_body_map(entries), body)

    advanced = ""
    root = _advanced_map(entries)
    if root:
        buf = io.StringIO()
        _yaml().dump(root, buf)
        commented = "".join(
            f"# {ln}\n" if ln else "#\n" for ln in buf.getvalue().splitlines()
        )
        advanced = (
            "\n# Advanced — rarely changed; uncomment a line to override its default.\n"
            + commented
        )
    header = _EXAMPLE_YAML_BANNER
    if include_maintainer_note:
        header += _EXAMPLE_YAML_MAINTAINER_NOTE
    header += _EXAMPLE_YAML_USER_GUIDE
    return header + body.getvalue() + advanced


def example_yaml_text() -> str:
    """The starter config printed by ``aibuildai config``."""
    return render_example_yaml(include_maintainer_note=True)


def _example_yaml_path() -> Path:
    # This module lives in startup/; the generated reference lives at repo root.
    return Path(__file__).resolve().parent.parent / "example.yaml"


if __name__ == "__main__":
    _example_yaml_path().write_text(
        render_example_yaml(include_maintainer_note=True),
        encoding="utf-8",
    )
    print(f"wrote {_example_yaml_path()}")

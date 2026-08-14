from __future__ import annotations

import json
import re
from dataclasses import dataclass
from pathlib import PurePosixPath
from typing import Any, Callable

from .graph_model import GraphNode, GraphSnapshot, digest_data


_RELATION_KINDS = {"IMPORTS_FILE", "CALLS", "TESTS_FILE", "USES_FILE"}


def is_current_file_node(node: GraphNode | None) -> bool:
    if node is None or node.kind != "file":
        return False
    receipt = node.facts.get("receipt") if isinstance(node.facts.get("receipt"), dict) else {}
    return receipt.get("present_in_current_inventory") is not False


@dataclass(frozen=True)
class ComponentManifestProvider:
    """One source-declared component format.

    Providers own format recognition and parsing. The projection consumes only
    their materialized facts, so adding an ecosystem does not change topology
    code or make directories into implicit component boundaries.
    """

    id: str
    matches_path: Callable[[PurePosixPath], bool]
    read_name: Callable[[str], object]
    read_components: Callable[[str], object] | None = None
    revision: int = 1

    def __post_init__(self) -> None:
        if not self.id or self.id.strip() != self.id:
            raise ValueError("component manifest provider id must be non-empty and trimmed")
        if type(self.revision) is not int or self.revision < 1:
            raise ValueError("component manifest provider revision must be a positive integer")

    def declarations(self, text: str) -> tuple[ComponentManifestDeclaration, ...]:
        if self.read_components is not None:
            raw = self.read_components(text)
            if not isinstance(raw, list):
                return ()
            declarations: list[ComponentManifestDeclaration] = []
            try:
                for item in raw:
                    if not isinstance(item, dict) or set(item) != {"name", "root"}:
                        return ()
                    declarations.append(
                        ComponentManifestDeclaration(
                            self.id,
                            item["name"],
                            relative_root=item["root"],
                        )
                    )
            except (TypeError, ValueError):
                return ()
            canonical = sorted(set(declarations), key=lambda item: (item.relative_root, item.name))
            if len(canonical) != len(declarations):
                return ()
            roots = [item.relative_root for item in canonical]
            if len(roots) != len(set(roots)):
                return ()
            return tuple(canonical)
        name = self.read_name(text)
        if not isinstance(name, str):
            return ()
        name = name.strip()
        if not name:
            return ()
        return (ComponentManifestDeclaration(self.id, name),)

    def manifest_fact(self, text: str) -> dict[str, Any] | None:
        declarations = self.declarations(text)
        if not declarations:
            return None
        if len(declarations) == 1 and not declarations[0].relative_root:
            return declarations[0].to_fact()
        return {
            "provider": self.id,
            "components": [declaration.to_component_fact() for declaration in declarations],
        }


@dataclass(frozen=True)
class ComponentManifestDeclaration:
    provider_id: str
    name: str
    relative_root: str = ""

    def __post_init__(self) -> None:
        if (
            not isinstance(self.provider_id, str)
            or not self.provider_id
            or self.provider_id.strip() != self.provider_id
        ):
            raise ValueError("component manifest provider id must be non-empty and trimmed")
        if not isinstance(self.name, str) or not self.name or self.name.strip() != self.name:
            raise ValueError("component manifest name must be non-empty and trimmed")
        if not _canonical_relative_root(self.relative_root):
            raise ValueError("component manifest root must be canonical and relative")

    def to_fact(self) -> dict[str, str]:
        return {"provider": self.provider_id, "name": self.name}

    def to_component_fact(self) -> dict[str, str]:
        return {"name": self.name, "root": self.relative_root}


@dataclass(frozen=True)
class ComponentManifestRegistry:
    """Immutable provider composition used at Graph materialization time."""

    providers: tuple[ComponentManifestProvider, ...] = ()

    def __post_init__(self) -> None:
        provider_ids = [provider.id for provider in self.providers]
        if len(provider_ids) != len(set(provider_ids)):
            raise ValueError("component manifest provider ids must be unique")

    def provider_for_path(self, path: str | PurePosixPath) -> ComponentManifestProvider | None:
        manifest_path = PurePosixPath(path)
        matches = [provider for provider in self.providers if provider.matches_path(manifest_path)]
        if len(matches) > 1:
            provider_ids = ", ".join(sorted(provider.id for provider in matches))
            raise ValueError(f"multiple component manifest providers matched {manifest_path}: {provider_ids}")
        return matches[0] if matches else None

    def manifest_fact(self, path: str | PurePosixPath, text: str) -> dict[str, Any] | None:
        provider = self.provider_for_path(path)
        return provider.manifest_fact(text) if provider is not None else None

    @property
    def identity(self) -> dict[str, Any]:
        return {
            "schema_version": 1,
            "providers": [
                {
                    "id": provider.id,
                    "revision": provider.revision,
                }
                for provider in self.providers
            ],
        }


def _matches_name(name: str) -> Callable[[PurePosixPath], bool]:
    return lambda path: path.name == name


def _canonical_relative_root(value: object) -> bool:
    if not isinstance(value, str) or value != value.strip() or "\\" in value:
        return False
    if not value:
        return True
    path = PurePosixPath(value)
    return (
        not path.is_absolute()
        and str(path) == value
        and all(part not in {"", ".", ".."} for part in path.parts)
    )


def _json_project_name(text: str) -> object:
    duplicate_keys = False

    def reject_duplicates(items: list[tuple[str, object]]) -> dict[str, object]:
        nonlocal duplicate_keys
        value: dict[str, object] = {}
        for key, item in items:
            if key in value:
                duplicate_keys = True
            value[key] = item
        return value

    try:
        value = json.loads(
            text.removeprefix("\ufeff"),
            object_pairs_hook=reject_duplicates,
        )
    except (json.JSONDecodeError, TypeError, UnicodeError):
        return ""
    return value.get("name") if isinstance(value, dict) and not duplicate_keys else ""


def _pyproject_name(text: str) -> object:
    try:
        import tomllib

        value = tomllib.loads(text)
    except (ValueError, TypeError):
        return ""
    if not isinstance(value, dict):
        return ""
    project = value.get("project")
    if isinstance(project, dict) and project.get("name"):
        return project["name"]
    tool = value.get("tool")
    poetry = tool.get("poetry") if isinstance(tool, dict) else None
    return poetry.get("name") if isinstance(poetry, dict) else ""


def _yaml_static_scalar(value: str) -> str:
    raw = value.strip()
    if not raw:
        return ""
    if raw[0] == "'":
        parsed: list[str] = []
        index = 1
        while index < len(raw):
            if raw[index] != "'":
                parsed.append(raw[index])
                index += 1
                continue
            if index + 1 < len(raw) and raw[index + 1] == "'":
                parsed.append("'")
                index += 2
                continue
            remainder = raw[index + 1 :]
            if remainder and re.fullmatch(r"\s+#.*", remainder) is None:
                return ""
            return "".join(parsed)
        return ""
    if raw[0] == '"':
        index = 1
        escaped = False
        while index < len(raw):
            char = raw[index]
            if escaped:
                escaped = False
            elif char == "\\":
                escaped = True
            elif char == '"':
                break
            index += 1
        if index == len(raw):
            return ""
        remainder = raw[index + 1 :]
        if remainder and re.fullmatch(r"\s+#.*", remainder) is None:
            return ""
        try:
            parsed = json.loads(raw[: index + 1])
        except (json.JSONDecodeError, TypeError):
            return ""
        return parsed if isinstance(parsed, str) else ""

    for index, char in enumerate(raw):
        if char == "#" and index > 0 and raw[index - 1].isspace():
            raw = raw[:index].rstrip()
            break
    if (
        not raw
        or raw[0] in "|>!&*{}[],#%@`"
        or re.match(r"[-?:](?:\s|$)", raw)
        or re.search(r"(?:^|\s)[!&*][^\s]*", raw)
        or re.search(r":\s", raw)
        or any(char in raw for char in "{}[]")
        or "\r" in raw
        or "\n" in raw
    ):
        return ""
    return raw


def _yaml_top_level_name(text: str) -> str:
    lines = text.removeprefix("\ufeff").splitlines()
    values = [
        (index, line.partition(":")[2])
        for index, line in enumerate(lines)
        if line.startswith("name:")
    ]
    if len(values) != 1:
        return ""
    index, value = values[0]
    for following in lines[index + 1 :]:
        if not following.strip() or following.lstrip().startswith("#"):
            continue
        if following[0].isspace():
            return ""
        break
    return _yaml_static_scalar(value)


def _cargo_package_name(text: str) -> object:
    try:
        import tomllib

        value = tomllib.loads(text)
    except (ValueError, TypeError):
        return ""
    package = value.get("package") if isinstance(value, dict) else None
    return package.get("name") if isinstance(package, dict) else ""


def _go_module_name(text: str) -> str:
    values = [line.removeprefix("module ").strip() for line in text.splitlines() if line.startswith("module ")]
    if len(values) != 1:
        return ""
    value = values[0]
    return value if value and not value.startswith(("=>", "//")) and not any(char.isspace() for char in value) else ""


def _balanced_call_body(text: str, start: int) -> tuple[str, int] | None:
    index = start
    depth = 1
    quote = ""
    while index < len(text) and depth:
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif char == "(":
            depth += 1
        elif char == ")":
            depth -= 1
            if depth == 0:
                return text[start:index], index + 1
        index += 1
    return None


def _swift_statement_ends(text: str, index: int) -> bool:
    while index < len(text) and text[index] in " \t":
        index += 1
    return index == len(text) or text[index] in ";\r\n"


def _swift_top_level_parts(text: str) -> list[str] | None:
    parts: list[str] = []
    stack: list[str] = []
    pairs = {")": "(", "]": "[", "}": "{"}
    start = 0
    index = 0
    quote = ""
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\":
                index += 2
                continue
            if char == quote:
                quote = ""
        elif char in {'"', "'"}:
            quote = char
        elif char in "([{":
            stack.append(char)
        elif char in ")]}":
            if not stack or stack.pop() != pairs[char]:
                return None
        elif char == "," and not stack:
            part = text[start:index].strip()
            if not part:
                return None
            parts.append(part)
            start = index + 1
        index += 1
    if quote or stack:
        return None
    tail = text[start:].strip()
    if tail:
        parts.append(tail)
    return parts


def _swift_labeled_arguments(text: str) -> dict[str, str] | None:
    parts = _swift_top_level_parts(text)
    if parts is None:
        return None
    arguments: dict[str, str] = {}
    for part in parts:
        match = re.fullmatch(
            r"(?P<label>[A-Za-z_][A-Za-z0-9_]*)\s*:\s*(?P<value>.+)",
            part,
            flags=re.DOTALL,
        )
        if match is None or match.group("label") in arguments:
            return None
        arguments[match.group("label")] = match.group("value").strip()
    return arguments


def _swift_static_string(value: str | None) -> str:
    if not isinstance(value, str):
        return ""
    match = re.fullmatch(r'"(?P<value>[^"\\\r\n]+)"', value.strip())
    return match.group("value") if match is not None else ""


def _swift_target_declarations(value: str) -> list[dict[str, str]] | None:
    expression = value.strip()
    if len(expression) < 2 or expression[0] != "[" or expression[-1] != "]":
        return None
    elements = _swift_top_level_parts(expression[1:-1])
    if elements is None:
        return None
    declarations: list[dict[str, str]] = []
    for element in elements:
        start = re.match(
            r"\.(?P<kind>target|testTarget|executableTarget)\s*\(",
            element,
        )
        if start is None:
            return None
        call = _balanced_call_body(element, start.end())
        if call is None:
            return None
        body, end = call
        if element[end:].strip():
            return None
        arguments = _swift_labeled_arguments(body)
        if arguments is None:
            return None
        name = _swift_static_string(arguments.get("name"))
        if not name:
            return None
        path_value = arguments.get("path")
        relative_root = _swift_static_string(path_value) if path_value is not None else ""
        if path_value is not None and not relative_root:
            return None
        if not relative_root:
            relative_root = (
                f"Tests/{name}"
                if start.group("kind") == "testTarget"
                else f"Sources/{name}"
            )
        declarations.append({"name": name, "root": relative_root})
    return declarations


def _swift_package_components(text: str) -> object:
    cleaned = _strip_c_style_comments(text)
    calls = list(
        re.finditer(
            r"(?m)^[ \t]*(?:let|var)[ \t]+package[ \t]*=[ \t]*Package\s*\(",
            cleaned,
        )
    )
    if len(calls) != 1:
        return []
    call = _balanced_call_body(cleaned, calls[0].end())
    if call is None:
        return []
    body, end = call
    if not _swift_statement_ends(cleaned, end):
        return []
    arguments = _swift_labeled_arguments(body)
    if arguments is None:
        return []
    name = _swift_static_string(arguments.get("name"))
    if not name:
        return []
    declarations = [{"name": name, "root": ""}]
    if "targets" not in arguments:
        return declarations
    targets = _swift_target_declarations(arguments["targets"])
    return [*declarations, *targets] if targets is not None else []


def _strip_c_style_comments(text: str) -> str:
    result: list[str] = []
    index = 0
    quote = ""
    while index < len(text):
        char = text[index]
        following = text[index + 1] if index + 1 < len(text) else ""
        if quote:
            result.append(char)
            if char == "\\" and index + 1 < len(text):
                index += 1
                result.append(text[index])
            elif char == quote:
                quote = ""
            index += 1
            continue
        triple_quote = text[index : index + 3]
        if triple_quote in {'"""', "'''"}:
            result.extend("   ")
            index += 3
            while index < len(text) and text[index : index + 3] != triple_quote:
                result.append(text[index] if text[index] in "\r\n" else " ")
                index += 1
            if index < len(text):
                result.extend("   ")
                index += 3
            continue
        if char in {'"', "'"}:
            quote = char
            result.append(char)
            index += 1
            continue
        if char == "/" and following == "/":
            index += 2
            while index < len(text) and text[index] not in "\r\n":
                index += 1
            continue
        if char == "/" and following == "*":
            depth = 1
            index += 2
            while index < len(text) and depth:
                pair = text[index : index + 2]
                if pair == "/*":
                    result.extend("  ")
                    depth += 1
                    index += 2
                    continue
                if pair == "*/":
                    result.extend("  ")
                    depth -= 1
                    index += 2
                    continue
                result.append(text[index] if text[index] in "\r\n" else " ")
                index += 1
            if depth:
                return ""
            continue
        result.append(char)
        index += 1
    return "".join(result)


def _strip_string_literals(text: str) -> str:
    result: list[str] = []
    index = 0
    quote = ""
    while index < len(text):
        char = text[index]
        if quote:
            if char == "\\" and index + 1 < len(text):
                result.extend("  ")
                index += 2
                continue
            if char == quote:
                quote = ""
            result.append(char if char in "\r\n" else " ")
            index += 1
            continue
        if char in {'"', "'"}:
            quote = char
            result.append(" ")
        else:
            result.append(char)
        index += 1
    return "".join(result)


def _quoted_arguments(value: str) -> list[str] | None:
    values: list[str] = []
    index = 0
    while index < len(value):
        while index < len(value) and value[index].isspace():
            index += 1
        if index >= len(value):
            return values
        quote = value[index]
        if quote not in {'"', "'"}:
            return None
        index += 1
        token: list[str] = []
        while index < len(value) and value[index] != quote:
            if value[index] == "\\" or value[index] in "\r\n":
                return None
            token.append(value[index])
            index += 1
        if index >= len(value):
            return None
        values.append("".join(token))
        index += 1
        while index < len(value) and value[index].isspace():
            index += 1
        if index >= len(value):
            return values
        if value[index] != ",":
            return None
        index += 1
    return values


def _parenthesized_calls(text: str, name: str) -> list[str] | None:
    calls: list[str] = []
    pattern = re.compile(rf"(?m)^[ \t]*{re.escape(name)}\s*\(")
    for match in pattern.finditer(text):
        index = match.end()
        start = index
        depth = 1
        quote = ""
        while index < len(text) and depth:
            char = text[index]
            if quote:
                if char == "\\":
                    index += 2
                    continue
                if char == quote:
                    quote = ""
            elif char in {'"', "'"}:
                quote = char
            elif char == "(":
                depth += 1
            elif char == ")":
                depth -= 1
                if depth == 0:
                    calls.append(text[start:index])
                    break
            index += 1
        if depth:
            return None
    return calls


def _top_level_statement_starts(text: str) -> set[int] | None:
    """Return line statement offsets, failing closed on unbalanced blocks."""

    starts: set[int] = set()
    depth = 0
    line_start = 0
    for index, char in enumerate(_strip_string_literals(text)):
        if index == line_start:
            starts.add(index) if depth == 0 else None
        if char == "{":
            depth += 1
        elif char == "}":
            depth -= 1
            if depth < 0:
                return None
        if char == "\n":
            line_start = index + 1
    if depth:
        return None
    if line_start == len(text):
        starts.add(line_start) if depth == 0 else None
    return starts


def _at_top_level(text: str, offset: int, starts: set[int]) -> bool:
    return text.rfind("\n", 0, offset) + 1 in starts


_GRADLE_PROJECT_PATH_RE = re.compile(r":[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)*")
_GRADLE_PROJECT_DIR_ASSIGNMENT_RE = re.compile(
    r"(?m)^[ \t]*project\s*\(\s*(?P<q>['\"])(?P<module>:[A-Za-z0-9_.-]+(?::[A-Za-z0-9_.-]+)*)(?P=q)\s*\)"
    r"\s*\.projectDir\s*=\s*(?P<rhs>[^\r\n]*)$"
)
_GRADLE_STATIC_FILE_CALL_RE = re.compile(
    r"file\s*\(\s*(?P<q>['\"])(?P<path>[^'\"\\\r\n]+)(?P=q)\s*\)\s*;?\s*"
)


def _gradle_components(text: str) -> object:
    cleaned = _strip_c_style_comments(text)
    syntax = _strip_string_literals(cleaned)
    top_level_starts = _top_level_statement_starts(cleaned)
    if top_level_starts is None:
        return []
    declaration_patterns = (
        r"\brootProject\.name\s*=",
        r"\binclude(?:\s*\(|[ \t]+)",
        r"\bproject\s*\(",
    )
    if any(
        not _at_top_level(syntax, match.start(), top_level_starts)
        for pattern in declaration_patterns
        for match in re.finditer(pattern, syntax)
    ):
        return []
    if "rootProject.name" in text and not re.search(
        r"(?m)^[ \t]*rootProject\.name\s*=",
        syntax,
    ):
        return []
    if any(
        syntax[syntax.rfind("\n", 0, match.start()) + 1 : match.start()].strip()
        for match in re.finditer(r"\binclude(?:\s*\(|[ \t]+)", syntax)
    ):
        return []
    root_matches = re.findall(
        r"(?m)^[ \t]*rootProject\.name\s*=\s*(?P<quote>['\"])(?P<name>[^'\"\\\r\n]+)(?P=quote)",
        cleaned,
    )
    root_names = {name for _quote, name in root_matches if name.strip() == name and name}
    if len(root_names) > 1 or len(root_matches) != len(re.findall(r"\brootProject\.name\s*=", syntax)):
        return []

    modules: set[str] = set()
    calls = _parenthesized_calls(cleaned, "include")
    if calls is None:
        return []
    for call in calls:
        values = _quoted_arguments(call)
        if values is None:
            return []
        modules.update(values)
    for match in re.finditer(
        r"(?m)^[ \t]*include(?![ \t]*\()[ \t]+(?P<args>[^\r\n]+)$",
        cleaned,
    ):
        values = _quoted_arguments(match.group("args"))
        if values is None:
            return []
        modules.update(values)
    if any(_GRADLE_PROJECT_PATH_RE.fullmatch(module) is None for module in modules):
        return []
    include_count = len(calls) + len(
        list(
            re.finditer(
                r"(?m)^[ \t]*include(?![ \t]*\()[ \t]+(?P<args>[^\r\n]+)$",
                cleaned,
            )
        )
    )
    if include_count != len(re.findall(r"\binclude\s*(?:\(|[ \t])", syntax)):
        return []

    assignments = list(_GRADLE_PROJECT_DIR_ASSIGNMENT_RE.finditer(cleaned))
    if len(assignments) != len(re.findall(r"\.projectDir\s*=", syntax)):
        return []

    mappings: dict[str, str] = {}
    for assignment in assignments:
        module = assignment.group("module")
        if module not in modules:
            return []
        value = _GRADLE_STATIC_FILE_CALL_RE.fullmatch(assignment.group("rhs"))
        if value is None or not _canonical_relative_root(value.group("path")):
            return []
        path = value.group("path")
        previous = mappings.get(module)
        if previous is not None and previous != path:
            return []
        mappings[module] = path

    declarations: list[dict[str, str]] = []
    if root_names:
        declarations.append({"name": next(iter(root_names)), "root": ""})
    declarations.extend(
        {
            "name": module,
            "root": mappings.get(module, module.removeprefix(":").replace(":", "/")),
        }
        for module in sorted(modules)
    )
    return declarations


DEFAULT_COMPONENT_MANIFEST_REGISTRY = ComponentManifestRegistry(
    (
        ComponentManifestProvider("package.json", _matches_name("package.json"), _json_project_name),
        ComponentManifestProvider("pyproject.toml", _matches_name("pyproject.toml"), _pyproject_name),
        ComponentManifestProvider("pubspec.yaml", _matches_name("pubspec.yaml"), _yaml_top_level_name, revision=3),
        ComponentManifestProvider("Cargo.toml", _matches_name("Cargo.toml"), _cargo_package_name),
        ComponentManifestProvider("go.mod", _matches_name("go.mod"), _go_module_name, revision=2),
        ComponentManifestProvider("unity.asmdef", lambda path: path.suffix == ".asmdef", _json_project_name),
        ComponentManifestProvider(
            "Package.swift",
            _matches_name("Package.swift"),
            lambda _text: "",
            read_components=_swift_package_components,
            revision=5,
        ),
        ComponentManifestProvider(
            "gradle.settings",
            lambda path: path.name in {"settings.gradle", "settings.gradle.kts"},
            lambda _text: "",
            read_components=_gradle_components,
            revision=4,
        ),
    )
)


def component_projection(snapshot: GraphSnapshot) -> dict[str, Any]:
    """Project materialized package membership onto existing subjects and relations."""

    memberships = _component_memberships(snapshot)
    nodes = {node.id: node for node in snapshot.nodes}
    symbol_files = {
        edge.to_id: edge.from_id
        for edge in snapshot.edges
        if edge.kind == "DEFINES" and is_current_file_node(nodes.get(edge.from_id))
    }
    subjects: dict[str, list[str]] = {}
    for node in snapshot.nodes:
        path = _node_path(node, nodes=nodes, symbol_files=symbol_files)
        if path and path in memberships:
            subjects[node.id] = memberships[path]

    relations: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    for edge in snapshot.edges:
        if edge.kind not in _RELATION_KINDS or edge.assertion != "resolved":
            continue
        from_ids = subjects.get(edge.from_id, [])
        to_ids = subjects.get(edge.to_id, [])
        if not from_ids or not to_ids or set(from_ids) == set(to_ids):
            continue
        crossed = sorted(set(from_ids) ^ set(to_ids))
        relations[(edge.kind, edge.from_id, edge.to_id, edge.assertion, edge.source)] = {
            "edge": edge.kind,
            "from_id": edge.from_id,
            "to_id": edge.to_id,
            "from_path": _node_path(
                nodes[edge.from_id],
                nodes=nodes,
                symbol_files=symbol_files,
            ),
            "to_path": _node_path(
                nodes[edge.to_id],
                nodes=nodes,
                symbol_files=symbol_files,
            ),
            "from_component_ids": from_ids,
            "to_component_ids": to_ids,
            "crossed_component_ids": crossed,
        }
    components = sorted({component_id for ids in memberships.values() for component_id in ids})
    basis = {
        "components": components,
        "paths": {key: value for key, value in sorted(memberships.items())},
        "subjects": {key: value for key, value in sorted(subjects.items())},
        "relations": [relations[key] for key in sorted(relations)],
    }
    return {**basis, "projection_digest": digest_data(basis)}


def annotate_graph_query_components(
    payload: dict[str, Any],
    projection: dict[str, Any],
    *,
    crossing_limit: int = 8,
) -> dict[str, Any]:
    if type(crossing_limit) is not int or crossing_limit <= 0:
        raise ValueError("crossing_limit must be a positive integer")
    subject_components = projection.get("subjects") if isinstance(projection.get("subjects"), dict) else {}
    relations = projection.get("relations") if isinstance(projection.get("relations"), list) else []

    def annotate_subject(value: Any) -> None:
        if not isinstance(value, dict):
            return
        node_id = str(value.get("id") or "")
        component_ids = subject_components.get(node_id)
        if isinstance(component_ids, list) and component_ids:
            value["component_ids"] = list(component_ids)

    for key in ("matches", "candidates", "nodes"):
        for item in payload.get(key, []):
            annotate_subject(item)
    for path in payload.get("paths", []):
        if not isinstance(path, dict):
            continue
        annotate_subject(path.get("from"))
        annotate_subject(path.get("to"))
    visible_relations = {
        (
            str(path.get("edge") or path.get("kind") or ""),
            str((path.get("from") or {}).get("id") or path.get("from_id") or ""),
            str((path.get("to") or {}).get("id") or path.get("to_id") or ""),
        )
        for path in payload.get("paths", [])
        if isinstance(path, dict)
    }
    visible_relations.update(
        (
            str(edge.get("kind") or edge.get("edge") or ""),
            str(edge.get("from") or edge.get("from_id") or ""),
            str(edge.get("to") or edge.get("to_id") or ""),
        )
        for edge in payload.get("edges", [])
        if isinstance(edge, dict)
    )
    eligible = [
        relation
        for relation in relations
        if isinstance(relation, dict)
        and (
            str(relation.get("edge") or ""),
            str(relation.get("from_id") or ""),
            str(relation.get("to_id") or ""),
        )
        in visible_relations
    ]
    payload["component_crossings"] = eligible[:crossing_limit]
    payload["component_crossing_count"] = len(eligible)
    payload["component_crossings_truncated"] = len(eligible) > crossing_limit
    return payload


def annotate_context_projection_components(
    projection: dict[str, Any],
    component_data: dict[str, Any],
) -> dict[str, Any]:
    """Add memberships/crossings to an existing Context Graph projection."""

    path_components = component_data.get("paths") if isinstance(component_data.get("paths"), dict) else {}
    crossings = component_data.get("relations") if isinstance(component_data.get("relations"), list) else []
    by_edge = {
        (
            str(item.get("edge") or ""),
            str(item.get("from_id") or ""),
            str(item.get("to_id") or ""),
        ): item
        for item in crossings
        if isinstance(item, dict)
    }
    visible_crossings: list[dict[str, Any]] = []
    for relation in projection.get("relations", []):
        if not isinstance(relation, dict):
            continue
        crossing = by_edge.get(
            (
                str(relation.get("edge") or ""),
                str(relation.get("from_id") or ""),
                str(relation.get("to_id") or ""),
            )
        )
        if crossing is None:
            continue
        relation.update(
            {
                "from_component_ids": crossing["from_component_ids"],
                "to_component_ids": crossing["to_component_ids"],
                "crossed_component_ids": crossing["crossed_component_ids"],
            }
        )
        visible_crossings.append(crossing)
    for anchor in projection.get("seed_anchors", []):
        if not isinstance(anchor, dict):
            continue
        path = str(anchor.get("path") or "")
        component_ids = path_components.get(path)
        if component_ids:
            anchor["component_ids"] = component_ids
    projection["component_crossings"] = visible_crossings
    projection["component_crossing_count"] = len(visible_crossings)
    projection["component_crossings_truncated"] = False
    return projection


def _node_path(
    node: GraphNode,
    *,
    nodes: dict[str, GraphNode],
    symbol_files: dict[str, str],
) -> str:
    if is_current_file_node(node):
        return str(node.identity.get("path") or "")
    if node.kind == "symbol":
        owner = nodes.get(symbol_files.get(node.id, ""))
        return str(owner.identity.get("path") or "") if owner is not None else ""
    return ""


def _component_memberships(snapshot: GraphSnapshot) -> dict[str, list[str]]:
    file_nodes = {
        str(node.identity.get("path") or ""): node
        for node in snapshot.nodes
        if is_current_file_node(node) and str(node.identity.get("path") or "")
    }
    current_paths = sorted(file_nodes)
    roots: dict[str, set[str]] = {}
    for path, manifest_node in sorted(file_nodes.items()):
        pure_path = PurePosixPath(path)
        manifest = (
            manifest_node.facts.get("component_manifest")
            if isinstance(manifest_node.facts.get("component_manifest"), dict)
            else {}
        )
        provider_id = manifest.get("provider")
        if not isinstance(provider_id, str) or not provider_id or provider_id.strip() != provider_id:
            continue
        manifest_root = "" if pure_path.parent == PurePosixPath(".") else pure_path.parent.as_posix()
        raw_components = manifest.get("components")
        if raw_components is None:
            raw_components = [{"name": manifest.get("name"), "root": ""}]
        if not isinstance(raw_components, list):
            continue
        declarations: list[tuple[str, str]] = []
        valid = True
        for component in raw_components:
            if not isinstance(component, dict) or set(component) != {"name", "root"}:
                valid = False
                break
            declared = component.get("name")
            relative_root = component.get("root")
            if (
                not isinstance(declared, str)
                or not declared
                or declared.strip() != declared
                or not _canonical_relative_root(relative_root)
            ):
                valid = False
                break
            component_root = "/".join(
                part
                for part in (manifest_root, relative_root)
                if part
            )
            declarations.append((component_root, declared))
        if not valid or len(declarations) != len(set(declarations)):
            continue
        for component_root, declared in declarations:
            roots.setdefault(component_root, set()).add(
                f"component:{provider_id}:{component_root or '.'}:{declared}"
            )
    memberships: dict[str, list[str]] = {}
    for path in current_paths:
        parts = PurePosixPath(path).parts
        ancestors = ["", *(PurePosixPath(*parts[:index]).as_posix() for index in range(1, len(parts) + 1))]
        if matching_roots := roots.keys() & ancestors:
            memberships[path] = sorted(
                component_id
                for root in matching_roots
                for component_id in roots[root]
            )
    return memberships

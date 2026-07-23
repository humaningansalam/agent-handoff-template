from __future__ import annotations

import ast
import fnmatch
import json
import shlex
from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Generic, Iterable, TypeVar
from urllib.parse import urlparse

from .code_index import CodeIndexEntry
from .tasks import Problem


STRUCTURED_RELATION_INPUT_VERSION = 4
STRUCTURED_EDGE_KIND = "USES_FILE"
_DART_SUPABASE_MODULES = frozenset(
    {
        "package:supabase/supabase.dart",
        "package:supabase_flutter/supabase_flutter.dart",
    }
)


class StructuredRelationType(StrEnum):
    DOCKER_COPY_SOURCE = "docker_copy_source"
    COMPOSE_DOCKERFILE = "compose_dockerfile"
    COMPOSE_ENV_FILE = "compose_env_file"
    COMPOSE_CONFIG_FILE = "compose_config_file"
    WORKFLOW_LOCAL_ACTION = "workflow_local_action"
    WORKFLOW_RUN_FILE = "workflow_run_file"
    SHELL_SOURCE_FILE = "shell_source_file"
    SHELL_RUN_FILE = "shell_run_file"
    SQL_SCHEMA_DEPENDENCY = "sql_schema_dependency"
    SQL_SEED_DEPENDENCY = "sql_seed_dependency"
    SQL_RPC_DEPENDENCY = "sql_rpc_dependency"


@dataclass(frozen=True, order=True)
class StructuredRelationEvidence:
    relation: StructuredRelationType
    reference: str
    line: int
    confidence: str = "high"
    operation: str = ""

    def to_dict(self) -> dict[str, object]:
        data: dict[str, object] = {
            "relation": self.relation.value,
            "reference": self.reference,
            "line": self.line,
            "confidence": self.confidence,
        }
        if self.operation:
            data["operation"] = self.operation
        return data


@dataclass(frozen=True)
class StructuredFileRelation:
    from_path: str
    to_path: str
    evidence: tuple[StructuredRelationEvidence, ...]

    def to_dict(self) -> dict[str, object]:
        return {
            "from_path": self.from_path,
            "to_path": self.to_path,
            "evidence": [item.to_dict() for item in self.evidence],
        }


@dataclass(frozen=True)
class StructuredRelationResult:
    relations: tuple[StructuredFileRelation, ...]
    eligible_paths: tuple[str, ...]
    analyzed_paths: tuple[str, ...]
    failed_paths: tuple[str, ...]
    problems: tuple[Problem, ...]

    def to_meta(self) -> dict[str, object]:
        return {
            "input_version": STRUCTURED_RELATION_INPUT_VERSION,
            "eligible_paths": list(self.eligible_paths),
            "analyzed_paths": list(self.analyzed_paths),
            "failed_paths": list(self.failed_paths),
            "relation_count": len(self.relations),
        }


_T = TypeVar("_T")


class _ParseStatus(StrEnum):
    PARSED = "parsed"
    FAILED = "failed"


@dataclass(frozen=True)
class _ParseResult(Generic[_T]):
    status: _ParseStatus
    value: _T | None = None
    code: str = ""
    message: str = ""
    line: int = 0


def _parsed(value: _T) -> _ParseResult[_T]:
    return _ParseResult(_ParseStatus.PARSED, value=value)


def _parse_failed(code: str, message: str, line: int = 0) -> _ParseResult[object]:
    return _ParseResult(_ParseStatus.FAILED, code=code, message=message, line=line)


class _FileResolutionStatus(StrEnum):
    RESOLVED_ONE = "resolved_one"
    RESOLVED_GLOB_MANY = "resolved_glob_many"
    AMBIGUOUS = "ambiguous"
    DYNAMIC = "dynamic"
    NOT_FOUND = "not_found"
    INVALID = "invalid"


@dataclass(frozen=True)
class _FileResolution:
    status: _FileResolutionStatus
    targets: tuple[str, ...] = ()


@dataclass(frozen=True)
class _ResolvedFileFact:
    target: str
    relation: StructuredRelationType
    reference: str
    line: int


@dataclass(frozen=True)
class _DockerBuildContext:
    dockerfile: str
    context: str


@dataclass(frozen=True)
class _FileFacts:
    references: tuple[_ResolvedFileFact, ...] = ()
    docker_builds: tuple[_DockerBuildContext, ...] = ()


@dataclass(frozen=True)
class _DockerSource:
    reference: str
    line: int
    glob: bool


class _YamlKind(StrEnum):
    MAPPING = "mapping"
    SEQUENCE = "sequence"
    SCALAR = "scalar"


@dataclass
class _YamlNode:
    kind: _YamlKind
    line: int
    scalar: str = ""
    mapping: dict[str, _YamlNode] = field(default_factory=dict)
    sequence: list[_YamlNode] = field(default_factory=list)


class _SyntaxFailure(ValueError):
    def __init__(self, message: str, *, line: int = 0) -> None:
        super().__init__(message)
        self.line = line


class _SqlObjectKind(StrEnum):
    TABLE = "table"
    ROUTINE = "routine"


class _SqlOperation(StrEnum):
    CREATE = "create"
    REPLACE = "replace"
    ALTER = "alter"
    INSERT = "insert"
    UPDATE = "update"
    DELETE = "delete"
    FROM = "from"
    JOIN = "join"
    REFERENCES = "references"
    CALL = "call"
    EXECUTE = "execute"
    SELECT = "select"
    PERFORM = "perform"
    CLIENT_RPC = "client_rpc"


@dataclass(frozen=True, order=True)
class _SqlIdentifierPart:
    canonical: str
    display: str
    quoted: bool = False


@dataclass(frozen=True, order=True)
class _SqlIdentifier:
    parts: tuple[_SqlIdentifierPart, ...]

    @property
    def key(self) -> tuple[str, ...]:
        return tuple(part.canonical for part in self.parts)

    @property
    def short_key(self) -> str:
        return self.parts[-1].canonical if self.parts else ""

    @property
    def display(self) -> str:
        return ".".join(part.display for part in self.parts)


@dataclass(frozen=True, order=True)
class _SqlRoutineSignature:
    parameter_types: tuple[str, ...]

    @property
    def arity(self) -> int:
        return len(self.parameter_types)


@dataclass(frozen=True)
class _SqlFact:
    path: str
    line: int
    object_kind: _SqlObjectKind
    identity: _SqlIdentifier
    operation: _SqlOperation
    definition: bool = False
    replace: bool = False
    routine_signature: _SqlRoutineSignature | None = None
    argument_count: int | None = None


class _TokenKind(StrEnum):
    IDENTIFIER = "identifier"
    STRING = "string"
    SYMBOL = "symbol"


@dataclass(frozen=True)
class _Token:
    kind: _TokenKind
    value: str
    line: int
    quoted: bool = False
    static: bool = True


class _ClientBinding(StrEnum):
    UNKNOWN = "unknown"
    CLIENT = "client"
    FACTORY = "factory"
    MODULE = "module"
    CLIENT_TYPE = "client_type"


class _PythonScopeKind(StrEnum):
    MODULE = "module"
    CLASS = "class"
    FUNCTION = "function"
    COMPREHENSION = "comprehension"


@dataclass(frozen=True)
class _RpcCall:
    routine: _SqlIdentifier
    line: int


class _ScriptOperandStatus(StrEnum):
    FILE = "file"
    INLINE = "inline"
    MODULE = "module"
    UNSUPPORTED = "unsupported"
    MISSING = "missing"


@dataclass(frozen=True)
class _ScriptOperand:
    status: _ScriptOperandStatus
    value: str = ""


@dataclass(frozen=True)
class _ShellCommand:
    tokens: tuple[str, ...]
    line: int


def build_structured_file_relations(repo: Path, entries: list[CodeIndexEntry]) -> StructuredRelationResult:
    entry_by_path = {entry.path: entry for entry in entries}
    known_paths = {
        entry.path
        for entry in entries
        if entry.classification != "excluded"
    }
    supported_paths = {
        entry.path
        for entry in entries
        if entry.classification != "excluded" and _structured_file_kind(entry.path)
    }
    rpc_source_paths = {
        entry.path
        for entry in entries
        if entry.classification != "excluded" and entry.language in {"python", "javascript", "typescript", "dart"}
    }
    rpc_candidate_paths = {
        path
        for path in rpc_source_paths
        if entry_by_path[path].parse_status == "ok" and _entry_has_supported_rpc_client_import(entry_by_path[path])
    }
    eligible_paths = supported_paths | rpc_source_paths
    analyzed_paths: set[str] = set()
    failed_paths: set[str] = set()
    problems: list[Problem] = []
    relation_evidence: dict[tuple[str, str], set[StructuredRelationEvidence]] = {}
    sql_definitions: list[_SqlFact] = []
    sql_references: list[_SqlFact] = []
    docker_sources: dict[str, tuple[_DockerSource, ...]] = {}
    docker_contexts: dict[str, set[str]] = {}
    texts: dict[str, str] = {}

    def add_relation(from_path: str, to_path: str, evidence: StructuredRelationEvidence) -> None:
        if not from_path or not to_path or from_path == to_path:
            return
        if from_path not in known_paths or to_path not in known_paths:
            return
        relation_evidence.setdefault((from_path, to_path), set()).add(evidence)

    def accept(path: str, result: _ParseResult[_T]) -> _T | None:
        if result.status == _ParseStatus.FAILED:
            failed_paths.add(path)
            problem_path = f"{path}:{result.line}" if result.line else path
            problems.append(Problem("warning", result.code, result.message, problem_path))
            return None
        analyzed_paths.add(path)
        return result.value

    for path in sorted(supported_paths | rpc_candidate_paths):
        text = _read_source(repo, path, problems=problems, failed_paths=failed_paths)
        if text is not None:
            texts[path] = text

    def add_file_facts(source_path: str, facts: _FileFacts) -> None:
        for fact in facts.references:
            add_relation(
                source_path,
                fact.target,
                StructuredRelationEvidence(fact.relation, fact.reference, fact.line),
            )
        for build in facts.docker_builds:
            docker_contexts.setdefault(build.dockerfile, set()).add(build.context)

    for path in sorted(supported_paths):
        text = texts.get(path)
        if text is None:
            continue
        kind = _structured_file_kind(path)
        if kind == "dockerfile":
            value = accept(path, _dockerfile_sources(text))
            if value is not None:
                docker_sources[path] = value
        elif kind == "compose":
            value = accept(path, _compose_facts(text, path=path, known_paths=known_paths))
            if value is not None:
                add_file_facts(path, value)
        elif kind == "workflow":
            value = accept(path, _workflow_facts(text, path=path, known_paths=known_paths))
            if value is not None:
                add_file_facts(path, value)
        elif kind == "shell":
            value = accept(
                path,
                _shell_facts(
                    text,
                    path=path,
                    known_paths=known_paths,
                    bases=_default_shell_bases(path),
                ),
            )
            if value is not None:
                add_file_facts(path, value)
        elif kind == "sql":
            value = accept(path, _sql_facts(text, path=path))
            if value is not None:
                definitions, references = value
                sql_definitions.extend(definitions)
                sql_references.extend(references)

    for path in sorted(rpc_source_paths):
        entry = entry_by_path[path]
        if entry.parse_status != "ok":
            accept(
                path,
                _parse_failed(
                    "graph_structured_client_parse_failed",
                    entry.parse_error or "source parser did not produce a valid syntax tree",
                ),
            )
            continue
        if path not in rpc_candidate_paths:
            analyzed_paths.add(path)
            continue
        text = texts.get(path)
        if text is None:
            continue
        calls = accept(path, _rpc_calls(text, language=entry.language))
        if calls is None:
            continue
        for call in calls:
            sql_references.append(
                _SqlFact(
                    path=path,
                    line=call.line,
                    object_kind=_SqlObjectKind.ROUTINE,
                    identity=call.routine,
                    operation=_SqlOperation.CLIENT_RPC,
                )
            )

    for dockerfile, sources in sorted(docker_sources.items()):
        contexts = sorted(docker_contexts.get(dockerfile, set()))
        if not contexts:
            continue
        for source in sources:
            resolutions = [
                _resolve_file_reference(
                    source.reference,
                    base=context,
                    known_paths=known_paths,
                    allow_glob=source.glob,
                )
                for context in contexts
            ]
            target_sets = {
                resolution.targets
                for resolution in resolutions
                if resolution.status in {_FileResolutionStatus.RESOLVED_ONE, _FileResolutionStatus.RESOLVED_GLOB_MANY}
            }
            if len(target_sets) != 1 or any(
                resolution.status not in {_FileResolutionStatus.RESOLVED_ONE, _FileResolutionStatus.RESOLVED_GLOB_MANY}
                for resolution in resolutions
            ):
                continue
            for target in next(iter(target_sets)):
                add_relation(
                    dockerfile,
                    target,
                    StructuredRelationEvidence(
                        StructuredRelationType.DOCKER_COPY_SOURCE,
                        source.reference,
                        source.line,
                    ),
                )

    for source, target, evidence in _resolve_sql_relations(sql_definitions, sql_references):
        add_relation(source, target, evidence)

    relations = tuple(
        StructuredFileRelation(from_path, to_path, tuple(sorted(evidence)))
        for (from_path, to_path), evidence in sorted(relation_evidence.items())
    )
    return StructuredRelationResult(
        relations=relations,
        eligible_paths=tuple(sorted(eligible_paths)),
        analyzed_paths=tuple(sorted(analyzed_paths - failed_paths)),
        failed_paths=tuple(sorted(failed_paths)),
        problems=tuple(problems),
    )


def _read_source(repo: Path, path: str, *, problems: list[Problem], failed_paths: set[str]) -> str | None:
    try:
        return (repo / path).read_text(encoding="utf-8")
    except UnicodeDecodeError:
        failed_paths.add(path)
        problems.append(Problem("warning", "graph_structured_source_non_utf8", "structured relation source is not UTF-8", path))
    except OSError as exc:
        failed_paths.add(path)
        problems.append(Problem("warning", "graph_structured_source_unreadable", str(exc), path))
    return None


def _structured_file_kind(path: str) -> str:
    pure = PurePosixPath(path)
    name = pure.name.casefold()
    suffix = pure.suffix.casefold()
    parts = tuple(part.casefold() for part in pure.parts)
    if suffix == ".sql":
        return "sql"
    if name == "dockerfile" or name.startswith("dockerfile."):
        return "dockerfile"
    if suffix in {".yml", ".yaml"} and (
        name in {"compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml"}
        or name.startswith("compose.")
        or name.startswith("docker-compose.")
    ):
        return "compose"
    if suffix in {".yml", ".yaml"} and len(parts) >= 3 and parts[:2] == (".github", "workflows"):
        return "workflow"
    if suffix in {".sh", ".bash", ".zsh"}:
        return "shell"
    return ""


def _normalize_join(base: str, raw: str) -> str:
    value = raw.strip().replace("\\", "/")
    if not value or value.startswith("/") or "://" in value:
        return ""
    parts: list[str] = []
    for part in PurePosixPath(base, value).parts:
        if part in {"", "."}:
            continue
        if part == "..":
            if not parts:
                return ""
            parts.pop()
            continue
        parts.append(part)
    return "/".join(parts)


def _is_dynamic_reference(value: str) -> bool:
    return any(marker in value for marker in ("$", "`"))


def _resolve_file_reference(
    raw: str,
    *,
    base: str,
    known_paths: set[str],
    allow_glob: bool = False,
) -> _FileResolution:
    reference = raw.strip().strip("\"'")
    if not reference:
        return _FileResolution(_FileResolutionStatus.INVALID)
    if _is_dynamic_reference(reference):
        return _FileResolution(_FileResolutionStatus.DYNAMIC)
    candidate = _normalize_join(base, reference)
    if not candidate:
        return _FileResolution(_FileResolutionStatus.INVALID)
    has_glob = any(marker in candidate for marker in ("*", "?", "["))
    if has_glob:
        if not allow_glob:
            return _FileResolution(_FileResolutionStatus.INVALID)
        matches = tuple(sorted(path for path in known_paths if fnmatch.fnmatchcase(path, candidate)))
        if not matches:
            return _FileResolution(_FileResolutionStatus.NOT_FOUND)
        status = _FileResolutionStatus.RESOLVED_ONE if len(matches) == 1 else _FileResolutionStatus.RESOLVED_GLOB_MANY
        return _FileResolution(status, matches)
    if candidate in known_paths:
        return _FileResolution(_FileResolutionStatus.RESOLVED_ONE, (candidate,))
    return _FileResolution(_FileResolutionStatus.NOT_FOUND)


def _resolve_across_bases(
    raw: str,
    *,
    bases: tuple[str, ...],
    known_paths: set[str],
    allow_glob: bool = False,
) -> _FileResolution:
    unique_bases = tuple(dict.fromkeys(bases))
    resolutions = [
        _resolve_file_reference(raw, base=base, known_paths=known_paths, allow_glob=allow_glob)
        for base in unique_bases
    ]
    terminal = [
        result
        for result in resolutions
        if result.status in {_FileResolutionStatus.DYNAMIC, _FileResolutionStatus.INVALID}
    ]
    if terminal:
        return terminal[0]
    target_sets = {
        result.targets
        for result in resolutions
        if result.status in {_FileResolutionStatus.RESOLVED_ONE, _FileResolutionStatus.RESOLVED_GLOB_MANY}
    }
    if not target_sets:
        return _FileResolution(_FileResolutionStatus.NOT_FOUND)
    if len(target_sets) > 1:
        return _FileResolution(_FileResolutionStatus.AMBIGUOUS)
    targets = next(iter(target_sets))
    status = _FileResolutionStatus.RESOLVED_ONE if len(targets) == 1 else _FileResolutionStatus.RESOLVED_GLOB_MANY
    return _FileResolution(status, targets)


def _resolve_directory(raw: str, *, bases: tuple[str, ...]) -> _FileResolution:
    reference = raw.strip().strip("\"'")
    if not reference:
        return _FileResolution(_FileResolutionStatus.INVALID)
    if _is_dynamic_reference(reference):
        return _FileResolution(_FileResolutionStatus.DYNAMIC)
    candidates = {
        _normalize_join(base, reference)
        for base in tuple(dict.fromkeys(bases))
    }
    candidates.discard("")
    if reference in {".", "./"}:
        candidates.update(base for base in bases)
    candidates = {candidate.strip("/") for candidate in candidates}
    if not candidates:
        return _FileResolution(_FileResolutionStatus.INVALID)
    if len(candidates) > 1:
        return _FileResolution(_FileResolutionStatus.AMBIGUOUS)
    return _FileResolution(_FileResolutionStatus.RESOLVED_ONE, (next(iter(candidates)),))


def _dockerfile_sources(text: str) -> _ParseResult[tuple[_DockerSource, ...]]:
    instructions = _dockerfile_instructions(text)
    if instructions.status == _ParseStatus.FAILED:
        return instructions
    sources: list[_DockerSource] = []
    for instruction, payload, line in instructions.value or ():
        if instruction not in {"copy", "add"}:
            continue
        try:
            if payload.lstrip().startswith("["):
                values = json.loads(payload)
                if not isinstance(values, list) or not all(isinstance(value, str) for value in values):
                    return _parse_failed("graph_structured_docker_parse_failed", "Docker JSON instruction must contain only strings", line)
                tokens = list(values)
                has_external_stage = False
            else:
                tokens = shlex.split(payload, comments=False, posix=True)
                has_external_stage = any(token.startswith("--from=") for token in tokens)
                tokens = [token for token in tokens if not token.startswith("--")]
        except (ValueError, json.JSONDecodeError) as exc:
            return _parse_failed("graph_structured_docker_parse_failed", str(exc), line)
        if has_external_stage or len(tokens) < 2:
            continue
        for source in tokens[:-1]:
            if source in {".", "./"}:
                continue
            sources.append(_DockerSource(source, line, any(marker in source for marker in ("*", "?", "["))))
    return _parsed(tuple(sources))


def _dockerfile_instructions(text: str) -> _ParseResult[tuple[tuple[str, str, int], ...]]:
    instructions: list[tuple[str, str, int]] = []
    pending = ""
    start_line = 0
    for line_number, raw in enumerate(text.splitlines(), start=1):
        stripped = raw.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if not pending:
            start_line = line_number
        continued = stripped.endswith("\\")
        fragment = stripped[:-1].rstrip() if continued else stripped
        pending = f"{pending} {fragment}".strip()
        if continued:
            continue
        instruction, separator, payload = pending.partition(" ")
        if not separator:
            return _parse_failed("graph_structured_docker_parse_failed", "Docker instruction is missing a payload", start_line)
        instructions.append((instruction.casefold(), payload.strip(), start_line))
        pending = ""
    if pending:
        return _parse_failed("graph_structured_docker_parse_failed", "Docker continuation is incomplete", start_line)
    return _parsed(tuple(instructions))


class _YamlParser:
    def __init__(self, text: str) -> None:
        self.lines = text.splitlines()
        self.index = 0

    def parse(self) -> _YamlNode:
        peek = self._peek()
        if peek is None:
            return _YamlNode(_YamlKind.MAPPING, 1)
        _index, indent, _content, line = peek
        if indent != 0:
            raise _SyntaxFailure("YAML root must start at indentation zero", line=line)
        node = self._parse_block(indent)
        if self._peek() is not None:
            extra = self._peek()
            raise _SyntaxFailure("YAML contains an unexpected trailing block", line=extra[3] if extra else 0)
        return node

    def _peek(self) -> tuple[int, int, str, int] | None:
        cursor = self.index
        while cursor < len(self.lines):
            raw = self.lines[cursor]
            if raw.startswith("\t") or "\t" in raw[: len(raw) - len(raw.lstrip())]:
                raise _SyntaxFailure("YAML indentation must use spaces", line=cursor + 1)
            stripped = raw.strip()
            if not stripped or stripped.startswith("#") or stripped in {"---", "..."}:
                cursor += 1
                continue
            indent = len(raw) - len(raw.lstrip(" "))
            content = _strip_yaml_comment(raw[indent:]).rstrip()
            if not content:
                cursor += 1
                continue
            return cursor, indent, content, cursor + 1
        return None

    def _parse_block(self, indent: int) -> _YamlNode:
        peek = self._peek()
        if peek is None or peek[1] != indent:
            raise _SyntaxFailure("YAML block indentation is inconsistent", line=peek[3] if peek else 0)
        if peek[2] == "-" or peek[2].startswith("- "):
            return self._parse_sequence(indent)
        return self._parse_mapping(indent)

    def _parse_mapping(self, indent: int) -> _YamlNode:
        mapping: dict[str, _YamlNode] = {}
        first_line = 0
        while True:
            peek = self._peek()
            if peek is None or peek[1] < indent:
                break
            index, current_indent, content, line = peek
            if current_indent != indent or content == "-" or content.startswith("- "):
                break
            key, value = _split_yaml_mapping(content)
            if not key:
                raise _SyntaxFailure("YAML mapping entry is invalid", line=line)
            if key in mapping:
                raise _SyntaxFailure(f"YAML mapping key is duplicated: {key}", line=line)
            first_line = first_line or line
            self.index = index + 1
            mapping[key] = self._parse_mapping_value(value, parent_indent=indent, line=line)
        if not mapping:
            peek = self._peek()
            raise _SyntaxFailure("YAML mapping block is empty", line=peek[3] if peek else 0)
        return _YamlNode(_YamlKind.MAPPING, first_line, mapping=mapping)

    def _parse_mapping_value(self, value: str, *, parent_indent: int, line: int) -> _YamlNode:
        if value in {"|", "|-", "|+", ">", ">-", ">+"}:
            return self._parse_block_scalar(parent_indent=parent_indent, line=line, folded=value.startswith(">"))
        if value:
            return _yaml_inline_node(value, line=line)
        peek = self._peek()
        if peek is not None and peek[1] > parent_indent:
            return self._parse_block(peek[1])
        return _YamlNode(_YamlKind.SCALAR, line, scalar="")

    def _parse_sequence(self, indent: int) -> _YamlNode:
        values: list[_YamlNode] = []
        first_line = 0
        while True:
            peek = self._peek()
            if peek is None or peek[1] < indent:
                break
            index, current_indent, content, line = peek
            if current_indent != indent or not (content == "-" or content.startswith("- ")):
                break
            first_line = first_line or line
            payload = content[1:].strip()
            self.index = index + 1
            if not payload:
                child = self._peek()
                if child is None or child[1] <= indent:
                    values.append(_YamlNode(_YamlKind.SCALAR, line, scalar=""))
                else:
                    values.append(self._parse_block(child[1]))
                continue
            key, mapped_value = _split_yaml_mapping(payload)
            if not key:
                values.append(_yaml_inline_node(payload, line=line))
                child = self._peek()
                if child is not None and child[1] > indent:
                    raise _SyntaxFailure("YAML scalar sequence item cannot own a nested block", line=child[3])
                continue
            item_mapping: dict[str, _YamlNode] = {
                key: self._parse_mapping_value(mapped_value, parent_indent=indent, line=line)
            }
            continuation = self._peek()
            if continuation is not None and continuation[1] > indent:
                continuation_node = self._parse_block(continuation[1])
                if continuation_node.kind != _YamlKind.MAPPING:
                    raise _SyntaxFailure("YAML sequence mapping continuation must be a mapping", line=continuation_node.line)
                for continuation_key, continuation_value in continuation_node.mapping.items():
                    if continuation_key in item_mapping:
                        raise _SyntaxFailure(f"YAML mapping key is duplicated: {continuation_key}", line=continuation_value.line)
                    item_mapping[continuation_key] = continuation_value
            values.append(_YamlNode(_YamlKind.MAPPING, line, mapping=item_mapping))
        return _YamlNode(_YamlKind.SEQUENCE, first_line or 1, sequence=values)

    def _parse_block_scalar(self, *, parent_indent: int, line: int, folded: bool) -> _YamlNode:
        cursor = self.index
        collected: list[str] = []
        indents: list[int] = []
        while cursor < len(self.lines):
            raw = self.lines[cursor]
            stripped = raw.strip()
            indent = len(raw) - len(raw.lstrip(" "))
            if stripped and indent <= parent_indent:
                break
            collected.append(raw)
            if stripped:
                indents.append(indent)
            cursor += 1
        trim = min(indents) if indents else parent_indent + 2
        values = [raw[trim:] if len(raw) >= trim else "" for raw in collected]
        self.index = cursor
        scalar = " ".join(value.strip() for value in values) if folded else "\n".join(values)
        return _YamlNode(_YamlKind.SCALAR, line, scalar=scalar)


def _parse_yaml(text: str) -> _ParseResult[_YamlNode]:
    try:
        return _parsed(_YamlParser(text).parse())
    except _SyntaxFailure as exc:
        return _parse_failed("graph_structured_yaml_parse_failed", str(exc), exc.line)


def _strip_yaml_comment(value: str) -> str:
    quote = ""
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {'"', "'"}:
            quote = "" if quote == char else char if not quote else quote
            continue
        if char == "#" and not quote and (index == 0 or value[index - 1].isspace()):
            return value[:index]
    return value


def _split_yaml_mapping(value: str) -> tuple[str, str]:
    quote = ""
    escaped = False
    for index, char in enumerate(value):
        if escaped:
            escaped = False
            continue
        if char == "\\" and quote == '"':
            escaped = True
            continue
        if char in {'"', "'"}:
            quote = "" if quote == char else char if not quote else quote
        elif char == ":" and not quote:
            key = value[:index].strip().strip('"\'')
            return key, value[index + 1 :].strip()
    return "", ""


def _yaml_inline_node(value: str, *, line: int) -> _YamlNode:
    stripped = value.strip()
    if stripped.startswith("[") and stripped.endswith("]"):
        lexer = shlex.shlex(stripped[1:-1], posix=True)
        lexer.whitespace = ","
        lexer.whitespace_split = True
        lexer.commenters = ""
        return _YamlNode(
            _YamlKind.SEQUENCE,
            line,
            sequence=[_YamlNode(_YamlKind.SCALAR, line, scalar=token.strip()) for token in lexer if token.strip()],
        )
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == '"':
        try:
            scalar = json.loads(stripped)
        except json.JSONDecodeError as exc:
            raise _SyntaxFailure(str(exc), line=line) from exc
        return _YamlNode(_YamlKind.SCALAR, line, scalar=str(scalar))
    if len(stripped) >= 2 and stripped[0] == stripped[-1] == "'":
        return _YamlNode(_YamlKind.SCALAR, line, scalar=stripped[1:-1].replace("''", "'"))
    return _YamlNode(_YamlKind.SCALAR, line, scalar=stripped)


def _yaml_mapping(node: _YamlNode | None, key: str) -> _YamlNode | None:
    if node is None or node.kind != _YamlKind.MAPPING:
        return None
    return node.mapping.get(key)


def _yaml_scalar_values(node: _YamlNode | None) -> tuple[tuple[str, int], ...]:
    if node is None:
        return ()
    if node.kind == _YamlKind.SCALAR:
        return ((node.scalar, node.line),) if node.scalar else ()
    if node.kind == _YamlKind.SEQUENCE:
        values: list[tuple[str, int]] = []
        for item in node.sequence:
            if item.kind != _YamlKind.SCALAR or not item.scalar:
                return ()
            values.append((item.scalar, item.line))
        return tuple(values)
    return ()


def _compose_facts(text: str, *, path: str, known_paths: set[str]) -> _ParseResult[_FileFacts]:
    parsed = _parse_yaml(text)
    if parsed.status == _ParseStatus.FAILED:
        return parsed
    root = parsed.value
    if root is None or root.kind != _YamlKind.MAPPING:
        return _parse_failed("graph_structured_yaml_parse_failed", "Compose root must be a mapping")
    compose_parent = PurePosixPath(path).parent.as_posix()
    compose_parent = "" if compose_parent == "." else compose_parent
    references: list[_ResolvedFileFact] = []
    builds: list[_DockerBuildContext] = []
    services = _yaml_mapping(root, "services")
    if services is not None and services.kind == _YamlKind.MAPPING:
        for service in services.mapping.values():
            if service.kind != _YamlKind.MAPPING:
                continue
            for value, line in _yaml_scalar_values(_yaml_mapping(service, "env_file")):
                resolution = _resolve_file_reference(value, base=compose_parent, known_paths=known_paths)
                if resolution.status == _FileResolutionStatus.RESOLVED_ONE:
                    references.append(_ResolvedFileFact(resolution.targets[0], StructuredRelationType.COMPOSE_ENV_FILE, value, line))
            extends = _yaml_mapping(service, "extends")
            for value, line in _yaml_scalar_values(_yaml_mapping(extends, "file")):
                resolution = _resolve_file_reference(value, base=compose_parent, known_paths=known_paths)
                if resolution.status == _FileResolutionStatus.RESOLVED_ONE:
                    references.append(_ResolvedFileFact(resolution.targets[0], StructuredRelationType.COMPOSE_CONFIG_FILE, value, line))
            build = _yaml_mapping(service, "build")
            context_value = "."
            context_line = build.line if build is not None else service.line
            dockerfile_value = "Dockerfile"
            dockerfile_line = context_line
            if build is not None and build.kind == _YamlKind.SCALAR:
                context_value = build.scalar
                context_line = build.line
            elif build is not None and build.kind == _YamlKind.MAPPING:
                context_values = _yaml_scalar_values(_yaml_mapping(build, "context"))
                dockerfile_values = _yaml_scalar_values(_yaml_mapping(build, "dockerfile"))
                if context_values:
                    context_value, context_line = context_values[0]
                if dockerfile_values:
                    dockerfile_value, dockerfile_line = dockerfile_values[0]
            elif build is None:
                continue
            context_resolution = _resolve_directory(context_value, bases=(compose_parent,))
            if context_resolution.status != _FileResolutionStatus.RESOLVED_ONE:
                continue
            context_path = context_resolution.targets[0]
            dockerfile_resolution = _resolve_file_reference(
                dockerfile_value,
                base=context_path,
                known_paths=known_paths,
            )
            if dockerfile_resolution.status == _FileResolutionStatus.RESOLVED_ONE:
                dockerfile_path = dockerfile_resolution.targets[0]
                references.append(
                    _ResolvedFileFact(
                        dockerfile_path,
                        StructuredRelationType.COMPOSE_DOCKERFILE,
                        dockerfile_value,
                        dockerfile_line,
                    )
                )
                builds.append(_DockerBuildContext(dockerfile_path, context_path))
    for section_name in ("configs", "secrets"):
        section = _yaml_mapping(root, section_name)
        if section is None or section.kind != _YamlKind.MAPPING:
            continue
        for item in section.mapping.values():
            for value, line in _yaml_scalar_values(_yaml_mapping(item, "file")):
                resolution = _resolve_file_reference(value, base=compose_parent, known_paths=known_paths)
                if resolution.status == _FileResolutionStatus.RESOLVED_ONE:
                    references.append(_ResolvedFileFact(resolution.targets[0], StructuredRelationType.COMPOSE_CONFIG_FILE, value, line))
    return _parsed(_FileFacts(tuple(references), tuple(builds)))


def _workflow_facts(text: str, *, path: str, known_paths: set[str]) -> _ParseResult[_FileFacts]:
    parsed = _parse_yaml(text)
    if parsed.status == _ParseStatus.FAILED:
        return parsed
    root = parsed.value
    if root is None or root.kind != _YamlKind.MAPPING:
        return _parse_failed("graph_structured_yaml_parse_failed", "Workflow root must be a mapping")
    references: list[_ResolvedFileFact] = []
    builds: list[_DockerBuildContext] = []
    workflow_workdir = _workflow_working_directory(_yaml_mapping(root, "defaults"))
    jobs = _yaml_mapping(root, "jobs")
    if jobs is None or jobs.kind != _YamlKind.MAPPING:
        return _parsed(_FileFacts())
    for job in jobs.mapping.values():
        if job.kind != _YamlKind.MAPPING:
            continue
        job_uses = _yaml_scalar_values(_yaml_mapping(job, "uses"))
        for value, line in job_uses:
            target = _resolve_workflow_uses(value, known_paths=known_paths)
            if target:
                references.append(_ResolvedFileFact(target, StructuredRelationType.WORKFLOW_LOCAL_ACTION, value, line))
        job_workdir = _workflow_working_directory(_yaml_mapping(job, "defaults")) or workflow_workdir
        steps = _yaml_mapping(job, "steps")
        if steps is None or steps.kind != _YamlKind.SEQUENCE:
            continue
        for step in steps.sequence:
            if step.kind != _YamlKind.MAPPING:
                continue
            for value, line in _yaml_scalar_values(_yaml_mapping(step, "uses")):
                target = _resolve_workflow_uses(value, known_paths=known_paths)
                if target:
                    references.append(_ResolvedFileFact(target, StructuredRelationType.WORKFLOW_LOCAL_ACTION, value, line))
            run_values = _yaml_scalar_values(_yaml_mapping(step, "run"))
            if not run_values:
                continue
            step_workdir_values = _yaml_scalar_values(_yaml_mapping(step, "working-directory"))
            working_directory = step_workdir_values[0][0] if step_workdir_values else job_workdir
            directory_resolution = _resolve_directory(working_directory or ".", bases=("",))
            if directory_resolution.status != _FileResolutionStatus.RESOLVED_ONE:
                continue
            base = directory_resolution.targets[0]
            run_text, run_line = run_values[0]
            shell = _shell_facts(
                run_text,
                path=path,
                known_paths=known_paths,
                bases=(base,),
                line_offset=run_line - 1,
            )
            if shell.status == _ParseStatus.FAILED:
                return shell
            for fact in (shell.value or _FileFacts()).references:
                references.append(
                    _ResolvedFileFact(
                        fact.target,
                        StructuredRelationType.WORKFLOW_RUN_FILE,
                        fact.reference,
                        fact.line,
                    )
                )
            builds.extend((shell.value or _FileFacts()).docker_builds)
    return _parsed(_FileFacts(tuple(references), tuple(builds)))


def _workflow_working_directory(defaults: _YamlNode | None) -> str:
    run = _yaml_mapping(defaults, "run")
    values = _yaml_scalar_values(_yaml_mapping(run, "working-directory"))
    return values[0][0] if values else ""


def _resolve_workflow_uses(value: str, *, known_paths: set[str]) -> str:
    if not value.startswith("./") or _is_dynamic_reference(value):
        return ""
    direct = _resolve_file_reference(value, base="", known_paths=known_paths)
    if direct.status == _FileResolutionStatus.RESOLVED_ONE:
        return direct.targets[0]
    action_base = _normalize_join("", value)
    candidates = tuple(
        candidate
        for candidate in (f"{action_base}/action.yml", f"{action_base}/action.yaml")
        if candidate in known_paths
    )
    return candidates[0] if len(candidates) == 1 else ""


def _default_shell_bases(path: str) -> tuple[str, ...]:
    parent = PurePosixPath(path).parent.as_posix()
    parent = "" if parent == "." else parent
    return tuple(dict.fromkeys((parent, "")))


def _shell_facts(
    text: str,
    *,
    path: str,
    known_paths: set[str],
    bases: tuple[str, ...],
    line_offset: int = 0,
) -> _ParseResult[_FileFacts]:
    parsed = _shell_commands(text, line_offset=line_offset)
    if parsed.status == _ParseStatus.FAILED:
        return parsed
    references: list[_ResolvedFileFact] = []
    builds: list[_DockerBuildContext] = []
    for command in parsed.value or ():
        tokens = list(command.tokens)
        while tokens and _is_assignment(tokens[0]):
            tokens.pop(0)
        while tokens and tokens[0] in {"env", "command", "sudo"}:
            tokens.pop(0)
            while tokens and tokens[0].startswith("-"):
                tokens.pop(0)
        if not tokens:
            continue
        executable = tokens[0]
        pending: list[tuple[str, StructuredRelationType]] = []
        if executable in {"source", "."} and len(tokens) > 1:
            pending.append((tokens[1], StructuredRelationType.SHELL_SOURCE_FILE))
        elif executable in {"bash", "sh", "zsh", "python", "python3", "node", "dart"}:
            operand = _script_operand(tokens)
            if operand.status == _ScriptOperandStatus.FILE:
                pending.append((operand.value, StructuredRelationType.SHELL_RUN_FILE))
        elif executable.startswith("./") or executable.startswith("../") or "/" in executable:
            pending.append((executable, StructuredRelationType.SHELL_RUN_FILE))
        elif executable == "docker" and len(tokens) > 1:
            if tokens[1] == "build":
                build = _docker_build_from_shell(tokens[2:], bases=bases, known_paths=known_paths)
                if build is not None:
                    builds.append(build)
                    pending.append((build.dockerfile, StructuredRelationType.SHELL_RUN_FILE))
            elif tokens[1] == "compose":
                compose_file = _option_value(tokens[2:], "-f", "--file")
                if compose_file:
                    pending.append((compose_file, StructuredRelationType.SHELL_RUN_FILE))
                else:
                    defaults = [
                        name
                        for name in ("compose.yml", "compose.yaml", "docker-compose.yml", "docker-compose.yaml")
                        if _resolve_across_bases(name, bases=bases, known_paths=known_paths).status == _FileResolutionStatus.RESOLVED_ONE
                    ]
                    if len(defaults) == 1:
                        pending.append((defaults[0], StructuredRelationType.SHELL_RUN_FILE))
        for reference, relation in pending:
            resolution = _resolve_across_bases(reference, bases=bases, known_paths=known_paths)
            if resolution.status == _FileResolutionStatus.RESOLVED_ONE:
                references.append(_ResolvedFileFact(resolution.targets[0], relation, reference, command.line))
    return _parsed(_FileFacts(tuple(references), tuple(builds)))


def _shell_commands(text: str, *, line_offset: int = 0) -> _ParseResult[tuple[_ShellCommand, ...]]:
    logical_lines: list[tuple[str, int]] = []
    pending = ""
    start_line = 0
    for line_number, raw in enumerate(text.splitlines(), start=1 + line_offset):
        stripped = raw.strip()
        if not pending and (not stripped or stripped.startswith("#")):
            continue
        if not pending:
            start_line = line_number
        continued = stripped.endswith("\\")
        fragment = stripped[:-1].rstrip() if continued else stripped
        pending = f"{pending} {fragment}".strip()
        if continued:
            continue
        logical_lines.append((pending, start_line))
        pending = ""
    if pending:
        return _parse_failed("graph_structured_shell_parse_failed", "shell continuation is incomplete", start_line)
    commands: list[_ShellCommand] = []
    for line, line_number in logical_lines:
        try:
            lexer = shlex.shlex(line, posix=True, punctuation_chars=";&|")
            lexer.commenters = "#"
            lexer.whitespace_split = True
            tokens = list(lexer)
        except ValueError as exc:
            return _parse_failed("graph_structured_shell_parse_failed", str(exc), line_number)
        segment: list[str] = []
        for token in tokens:
            if token and all(char in ";&|" for char in token):
                if segment:
                    commands.append(_ShellCommand(tuple(segment), line_number))
                    segment = []
            else:
                segment.append(token)
        if segment:
            commands.append(_ShellCommand(tuple(segment), line_number))
    return _parsed(tuple(commands))


def _script_operand(tokens: list[str]) -> _ScriptOperand:
    executable = tokens[0]
    args = tokens[1:]
    if executable in {"bash", "sh", "zsh"}:
        value_options = {"--rcfile", "--init-file"}
        for index, arg in enumerate(args):
            if arg == "--":
                return _ScriptOperand(_ScriptOperandStatus.FILE, args[index + 1]) if index + 1 < len(args) else _ScriptOperand(_ScriptOperandStatus.MISSING)
            if arg in {"-c", "--command"} or (arg.startswith("-") and not arg.startswith("--") and "c" in arg[1:]):
                return _ScriptOperand(_ScriptOperandStatus.INLINE)
            if arg in value_options:
                if index + 1 >= len(args):
                    return _ScriptOperand(_ScriptOperandStatus.MISSING)
                continue
            if arg.startswith("-"):
                continue
            if index > 0 and args[index - 1] in value_options:
                continue
            return _ScriptOperand(_ScriptOperandStatus.FILE, arg)
        return _ScriptOperand(_ScriptOperandStatus.MISSING)
    if executable in {"python", "python3"}:
        value_options = {"-W", "-X", "--check-hash-based-pycs"}
        index = 0
        while index < len(args):
            arg = args[index]
            if arg == "--":
                return _ScriptOperand(_ScriptOperandStatus.FILE, args[index + 1]) if index + 1 < len(args) else _ScriptOperand(_ScriptOperandStatus.MISSING)
            if arg == "-c" or arg.startswith("-c"):
                return _ScriptOperand(_ScriptOperandStatus.INLINE)
            if arg == "-m" or arg.startswith("-m"):
                return _ScriptOperand(_ScriptOperandStatus.MODULE)
            if arg in value_options:
                index += 2
                continue
            if arg.startswith("-"):
                index += 1
                continue
            return _ScriptOperand(_ScriptOperandStatus.FILE, arg)
        return _ScriptOperand(_ScriptOperandStatus.MISSING)
    if executable == "node":
        inline_options = {"-e", "--eval", "-p", "--print"}
        value_options = {"-r", "--require", "--loader", "--import", "--inspect-port"}
        index = 0
        while index < len(args):
            arg = args[index]
            if arg == "--":
                return _ScriptOperand(_ScriptOperandStatus.FILE, args[index + 1]) if index + 1 < len(args) else _ScriptOperand(_ScriptOperandStatus.MISSING)
            if arg in inline_options or any(arg.startswith(f"{option}=") for option in inline_options):
                return _ScriptOperand(_ScriptOperandStatus.INLINE)
            if arg in value_options:
                index += 2
                continue
            if any(arg.startswith(f"{option}=") for option in value_options):
                index += 1
                continue
            if arg.startswith("-"):
                index += 1
                continue
            return _ScriptOperand(_ScriptOperandStatus.FILE, arg)
        return _ScriptOperand(_ScriptOperandStatus.MISSING)
    if executable == "dart":
        index = 0
        if args and args[0] == "run":
            index = 1
        while index < len(args):
            arg = args[index]
            if arg == "--":
                return _ScriptOperand(_ScriptOperandStatus.FILE, args[index + 1]) if index + 1 < len(args) else _ScriptOperand(_ScriptOperandStatus.MISSING)
            if arg.startswith("-"):
                index += 1
                continue
            return _ScriptOperand(_ScriptOperandStatus.FILE, arg)
        return _ScriptOperand(_ScriptOperandStatus.MISSING)
    return _ScriptOperand(_ScriptOperandStatus.UNSUPPORTED)


def _docker_build_from_shell(
    tokens: list[str],
    *,
    bases: tuple[str, ...],
    known_paths: set[str],
) -> _DockerBuildContext | None:
    dockerfile_option = _option_value(tokens, "-f", "--file")
    context_token = _last_positional_option(tokens, value_options={"-f", "--file", "-t", "--tag", "--target", "--build-arg"})
    if not context_token:
        return None
    context_resolution = _resolve_directory(context_token, bases=bases)
    if context_resolution.status != _FileResolutionStatus.RESOLVED_ONE:
        return None
    context_path = context_resolution.targets[0]
    if dockerfile_option:
        dockerfile_resolution = _resolve_across_bases(dockerfile_option, bases=bases, known_paths=known_paths)
    else:
        dockerfile_resolution = _resolve_file_reference("Dockerfile", base=context_path, known_paths=known_paths)
    if dockerfile_resolution.status != _FileResolutionStatus.RESOLVED_ONE:
        return None
    return _DockerBuildContext(dockerfile_resolution.targets[0], context_path)


def _last_positional_option(tokens: list[str], *, value_options: set[str]) -> str:
    positionals: list[str] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token in value_options:
            index += 2
            continue
        if any(token.startswith(f"{option}=") for option in value_options):
            index += 1
            continue
        if token.startswith("-"):
            index += 1
            continue
        positionals.append(token)
        index += 1
    return positionals[-1] if positionals else ""


def _is_assignment(value: str) -> bool:
    name, separator, _value = value.partition("=")
    return bool(separator and name and (name[0].isalpha() or name[0] == "_") and all(char.isalnum() or char == "_" for char in name))


def _option_value(tokens: list[str], *names: str) -> str:
    for index, token in enumerate(tokens):
        if token in names and index + 1 < len(tokens):
            return tokens[index + 1]
        for name in names:
            prefix = f"{name}="
            if token.startswith(prefix):
                return token[len(prefix) :]
    return ""


def _sql_facts(text: str, *, path: str) -> _ParseResult[tuple[list[_SqlFact], list[_SqlFact]]]:
    token_result = _sql_tokens(text)
    if token_result.status == _ParseStatus.FAILED:
        return token_result
    definitions: list[_SqlFact] = []
    references: list[_SqlFact] = []
    for statement in _sql_statements(list(token_result.value or ())):
        ctes = _sql_cte_names(statement)
        index = 0
        while index < len(statement):
            token = statement[index]
            value = token.value
            if value == "create":
                cursor = index + 1
                replace = False
                if _token_values(statement, cursor, 2) == ("or", "replace"):
                    replace = True
                    cursor += 2
                while cursor < len(statement) and statement[cursor].value in {"temporary", "temp", "unlogged"}:
                    cursor += 1
                if cursor < len(statement) and statement[cursor].value in {"table", "function", "procedure"}:
                    object_kind = _SqlObjectKind.TABLE if statement[cursor].value == "table" else _SqlObjectKind.ROUTINE
                    cursor += 1
                    while cursor < len(statement) and statement[cursor].value in {"if", "not", "exists"}:
                        cursor += 1
                    identity, cursor = _sql_identifier(statement, cursor)
                    signature = None
                    if identity is not None and object_kind == _SqlObjectKind.ROUTINE:
                        signature, cursor = _sql_routine_signature(statement, cursor)
                    if identity is not None:
                        definitions.append(
                            _SqlFact(
                                path,
                                token.line,
                                object_kind,
                                identity,
                                _SqlOperation.REPLACE if replace else _SqlOperation.CREATE,
                                definition=True,
                                replace=replace,
                                routine_signature=signature,
                            )
                        )
                        if replace:
                            references.append(
                                _SqlFact(
                                    path,
                                    token.line,
                                    object_kind,
                                    identity,
                                    _SqlOperation.REPLACE,
                                    routine_signature=signature,
                                    argument_count=signature.arity if signature is not None else None,
                                )
                            )
            elif value == "alter" and index + 1 < len(statement) and statement[index + 1].value == "table":
                cursor = index + 2
                while cursor < len(statement) and statement[cursor].value in {"if", "exists", "only"}:
                    cursor += 1
                identity, _cursor = _sql_identifier(statement, cursor)
                if identity is not None:
                    references.append(_SqlFact(path, token.line, _SqlObjectKind.TABLE, identity, _SqlOperation.ALTER))
            elif value == "insert" and index + 1 < len(statement) and statement[index + 1].value == "into":
                identity, _cursor = _sql_identifier(statement, index + 2)
                if identity is not None and identity.key not in ctes:
                    references.append(_SqlFact(path, token.line, _SqlObjectKind.TABLE, identity, _SqlOperation.INSERT))
            elif value == "update":
                identity, _cursor = _sql_identifier(statement, index + 1)
                if identity is not None and identity.key not in ctes:
                    references.append(_SqlFact(path, token.line, _SqlObjectKind.TABLE, identity, _SqlOperation.UPDATE))
            elif value == "delete" and index + 1 < len(statement) and statement[index + 1].value == "from":
                identity, _cursor = _sql_identifier(statement, index + 2)
                if identity is not None and identity.key not in ctes:
                    references.append(_SqlFact(path, token.line, _SqlObjectKind.TABLE, identity, _SqlOperation.DELETE))
            elif value in {"from", "join", "references"}:
                identity, _cursor = _sql_identifier(statement, index + 1)
                if identity is not None and identity.key not in ctes:
                    references.append(_SqlFact(path, token.line, _SqlObjectKind.TABLE, identity, _SqlOperation(value)))
            elif value == "call":
                identity, cursor = _sql_identifier(statement, index + 1)
                if identity is not None:
                    count, _cursor = _sql_argument_count(statement, cursor)
                    references.append(_SqlFact(path, token.line, _SqlObjectKind.ROUTINE, identity, _SqlOperation.CALL, argument_count=count))
            elif value == "execute" and index + 1 < len(statement) and statement[index + 1].value in {"function", "procedure"}:
                identity, cursor = _sql_identifier(statement, index + 2)
                if identity is not None:
                    count, _cursor = _sql_argument_count(statement, cursor)
                    references.append(_SqlFact(path, token.line, _SqlObjectKind.ROUTINE, identity, _SqlOperation.EXECUTE, argument_count=count))
            elif value in {"select", "perform"}:
                identity, cursor = _sql_identifier(statement, index + 1)
                if identity is not None and cursor < len(statement) and statement[cursor].value == "(":
                    count, _cursor = _sql_argument_count(statement, cursor)
                    references.append(_SqlFact(path, token.line, _SqlObjectKind.ROUTINE, identity, _SqlOperation(value), argument_count=count))
            index += 1
    return _parsed((definitions, references))


def _sql_tokens(text: str, *, line_offset: int = 0) -> _ParseResult[tuple[_Token, ...]]:
    tokens: list[_Token] = []
    index = 0
    line = 1 + line_offset
    while index < len(text):
        char = text[index]
        if char.isspace():
            line += 1 if char == "\n" else 0
            index += 1
            continue
        if text.startswith("--", index):
            end = text.find("\n", index + 2)
            index = len(text) if end < 0 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                return _parse_failed("graph_structured_sql_parse_failed", "SQL block comment is unterminated", line)
            fragment = text[index : end + 2]
            line += fragment.count("\n")
            index = end + 2
            continue
        if char == "'":
            value, index, line, complete = _read_quoted_value(text, index, line, quote="'")
            if not complete:
                return _parse_failed("graph_structured_sql_parse_failed", "SQL string literal is unterminated", line)
            tokens.append(_Token(_TokenKind.STRING, value, line))
            continue
        if char == '"':
            start_line = line
            value, index, line, complete = _read_quoted_value(text, index, line, quote='"')
            if not complete:
                return _parse_failed("graph_structured_sql_parse_failed", "SQL quoted identifier is unterminated", start_line)
            tokens.append(_Token(_TokenKind.IDENTIFIER, value, start_line, quoted=True))
            continue
        if char == "$":
            delimiter_end = text.find("$", index + 1)
            if delimiter_end >= 0 and all(item.isalnum() or item == "_" for item in text[index + 1 : delimiter_end]):
                delimiter = text[index : delimiter_end + 1]
                body_end = text.find(delimiter, delimiter_end + 1)
                if body_end < 0:
                    return _parse_failed("graph_structured_sql_parse_failed", "SQL dollar-quoted body is unterminated", line)
                body = text[delimiter_end + 1 : body_end]
                nested = _sql_tokens(body, line_offset=line - 1)
                if nested.status == _ParseStatus.FAILED:
                    return nested
                tokens.extend(nested.value or ())
                line += text[index : body_end + len(delimiter)].count("\n")
                index = body_end + len(delimiter)
                continue
        if char.isalpha() or char == "_":
            start = index
            while index < len(text) and (text[index].isalnum() or text[index] in {"_", "$"}):
                index += 1
            tokens.append(_Token(_TokenKind.IDENTIFIER, text[start:index].casefold(), line))
            continue
        if char in {".", "(", ")", ";", ",", "=", "[", "]"}:
            tokens.append(_Token(_TokenKind.SYMBOL, char, line))
        index += 1
    return _parsed(tuple(tokens))


def _read_quoted_value(text: str, index: int, line: int, *, quote: str) -> tuple[str, int, int, bool]:
    value: list[str] = []
    index += 1
    while index < len(text):
        char = text[index]
        if char == "\n":
            line += 1
        if char == quote:
            if index + 1 < len(text) and text[index + 1] == quote:
                value.append(quote)
                index += 2
                continue
            return "".join(value), index + 1, line, True
        value.append(char)
        index += 1
    return "".join(value), index, line, False


def _sql_statements(tokens: list[_Token]) -> list[list[_Token]]:
    statements: list[list[_Token]] = []
    current: list[_Token] = []
    depth = 0
    for token in tokens:
        if token.value in {"(", "["}:
            depth += 1
        elif token.value in {")", "]"}:
            depth = max(0, depth - 1)
        if token.value == ";" and depth == 0:
            if current:
                statements.append(current)
            current = []
        else:
            current.append(token)
    if current:
        statements.append(current)
    return statements


def _sql_cte_names(tokens: list[_Token]) -> set[tuple[str, ...]]:
    if not tokens or tokens[0].value != "with":
        return set()
    cursor = 1
    if cursor < len(tokens) and tokens[cursor].value == "recursive":
        cursor += 1
    names: set[tuple[str, ...]] = set()
    while cursor < len(tokens):
        identity, cursor = _sql_identifier(tokens, cursor)
        if identity is None or len(identity.parts) != 1:
            break
        names.add(identity.key)
        if cursor < len(tokens) and tokens[cursor].value == "(":
            _count, cursor = _sql_argument_count(tokens, cursor)
        if cursor >= len(tokens) or tokens[cursor].value != "as":
            break
        cursor += 1
        if cursor >= len(tokens) or tokens[cursor].value != "(":
            break
        cursor = _skip_balanced(tokens, cursor)
        if cursor >= len(tokens) or tokens[cursor].value != ",":
            break
        cursor += 1
    return names


def _token_values(tokens: list[_Token], start: int, count: int) -> tuple[str, ...]:
    return tuple(token.value for token in tokens[start : start + count])


def _sql_identifier(tokens: list[_Token], start: int) -> tuple[_SqlIdentifier | None, int]:
    if start >= len(tokens) or tokens[start].kind != _TokenKind.IDENTIFIER:
        return None, start
    parts = [_sql_identifier_part(tokens[start])]
    cursor = start + 1
    while cursor + 1 < len(tokens) and tokens[cursor].value == "." and tokens[cursor + 1].kind == _TokenKind.IDENTIFIER:
        parts.append(_sql_identifier_part(tokens[cursor + 1]))
        cursor += 2
    return _SqlIdentifier(tuple(parts)), cursor


def _sql_identifier_part(token: _Token) -> _SqlIdentifierPart:
    canonical = token.value if token.quoted else token.value.casefold()
    display = f'"{token.value.replace(chr(34), chr(34) * 2)}"' if token.quoted else canonical
    return _SqlIdentifierPart(canonical, display, token.quoted)


_SQL_TYPE_START = {
    "bigint", "bigserial", "bit", "boolean", "bytea", "char", "character", "date", "decimal",
    "double", "inet", "integer", "interval", "json", "jsonb", "numeric", "real", "smallint",
    "smallserial", "text", "time", "timestamp", "uuid", "varchar", "varying", "xml",
}


def _sql_routine_signature(tokens: list[_Token], start: int) -> tuple[_SqlRoutineSignature | None, int]:
    if start >= len(tokens) or tokens[start].value != "(":
        return None, start
    end = _skip_balanced(tokens, start)
    if end <= start + 1:
        return _SqlRoutineSignature(()), end
    inner = tokens[start + 1 : end - 1]
    parameters = _split_top_level(inner, ",")
    types = tuple(_sql_parameter_type(parameter) for parameter in parameters if parameter)
    return _SqlRoutineSignature(types), end


def _sql_parameter_type(tokens: list[_Token]) -> str:
    values = [token for token in tokens if token.value not in {"in", "out", "inout", "variadic"}]
    for index, token in enumerate(values):
        if token.value in {"default", "="}:
            values = values[:index]
            break
    if len(values) >= 2 and values[0].kind == _TokenKind.IDENTIFIER and values[0].value not in _SQL_TYPE_START:
        values = values[1:]
    return " ".join(token.value for token in values).strip()


def _sql_argument_count(tokens: list[_Token], start: int) -> tuple[int | None, int]:
    if start >= len(tokens) or tokens[start].value != "(":
        return None, start
    end = _skip_balanced(tokens, start)
    inner = tokens[start + 1 : end - 1]
    if not inner:
        return 0, end
    return len(_split_top_level(inner, ",")), end


def _skip_balanced(tokens: list[_Token], start: int) -> int:
    if start >= len(tokens) or tokens[start].value != "(":
        return start
    depth = 0
    for cursor in range(start, len(tokens)):
        if tokens[cursor].value == "(":
            depth += 1
        elif tokens[cursor].value == ")":
            depth -= 1
            if depth == 0:
                return cursor + 1
    return len(tokens)


def _split_top_level(tokens: list[_Token], separator: str) -> list[list[_Token]]:
    values: list[list[_Token]] = []
    current: list[_Token] = []
    depth = 0
    for token in tokens:
        if token.value in {"(", "["}:
            depth += 1
        elif token.value in {")", "]"}:
            depth = max(0, depth - 1)
        if token.value == separator and depth == 0:
            values.append(current)
            current = []
        else:
            current.append(token)
    values.append(current)
    return values


def _rpc_calls(text: str, *, language: str) -> _ParseResult[tuple[_RpcCall, ...]]:
    if language == "python":
        return _python_rpc_calls(text)
    if language in {"javascript", "typescript"}:
        return _javascript_rpc_calls(text)
    if language == "dart":
        return _dart_rpc_calls(text)
    return _parsed(())


def _entry_has_supported_rpc_client_import(entry: CodeIndexEntry) -> bool:
    if entry.language == "python":
        return any(_python_supabase_module(occurrence.module) for occurrence in entry.import_occurrences)
    if entry.language in {"javascript", "typescript"}:
        return any(_javascript_supabase_module(specifier) for specifier in entry.imports)
    if entry.language == "dart":
        return any(specifier in _DART_SUPABASE_MODULES for specifier in entry.imports)
    return False


class _PythonRpcVisitor:
    def __init__(self) -> None:
        self.scopes: list[dict[str, _ClientBinding]] = [{}]
        self.scope_kinds: list[_PythonScopeKind] = [_PythonScopeKind.MODULE]
        self.calls: list[_RpcCall] = []

    def visit_module(self, tree: ast.Module) -> None:
        self._visit_statements(tree.body)

    def _visit_statements(self, statements: list[ast.stmt]) -> None:
        for statement in statements:
            self.visit(statement)

    def visit(self, node: ast.AST) -> None:
        method = getattr(self, f"visit_{type(node).__name__}", self.generic_visit)
        method(node)

    def generic_visit(self, node: ast.AST) -> None:
        for child in ast.iter_child_nodes(node):
            self.visit(child)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        if _python_supabase_module(node.module or ""):
            for alias in node.names:
                bound = alias.asname or alias.name
                if alias.name == "create_client":
                    self.scopes[-1][bound] = _ClientBinding.FACTORY
                elif alias.name in {"Client", "SyncClient", "AsyncClient"}:
                    self.scopes[-1][bound] = _ClientBinding.CLIENT_TYPE

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            if _python_supabase_module(alias.name):
                self.scopes[-1][alias.asname or alias.name.split(".", 1)[0]] = _ClientBinding.MODULE

    def visit_Assign(self, node: ast.Assign) -> None:
        binding = self._expression_binding(node.value)
        for target in node.targets:
            if isinstance(target, ast.Name):
                self.scopes[-1][target.id] = binding
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name):
            annotation = _python_annotation_name(node.annotation)
            binding = _ClientBinding.CLIENT if self._lookup(annotation) == _ClientBinding.CLIENT_TYPE else _ClientBinding.UNKNOWN
            if node.value is not None:
                value_binding = self._expression_binding(node.value)
                if value_binding != _ClientBinding.UNKNOWN:
                    binding = value_binding
            self.scopes[-1][node.target.id] = binding
        if node.value is not None:
            self.visit(node.value)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.scopes[-1][node.name] = _ClientBinding.UNKNOWN
        scope = {name: _ClientBinding.UNKNOWN for name in _python_bound_names(node.body)}
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg is not None:
            arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
            arguments.append(node.args.kwarg)
        for argument in arguments:
            annotation = _python_annotation_name(argument.annotation)
            scope[argument.arg] = _ClientBinding.UNKNOWN
            if self._lookup(annotation) == _ClientBinding.CLIENT_TYPE:
                scope[argument.arg] = _ClientBinding.CLIENT
        self.scopes.append(scope)
        self.scope_kinds.append(_PythonScopeKind.FUNCTION)
        self._visit_statements(node.body)
        self.scope_kinds.pop()
        self.scopes.pop()

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.scopes[-1][node.name] = _ClientBinding.UNKNOWN
        self.scopes.append({name: _ClientBinding.UNKNOWN for name in _python_bound_names(node.body)})
        self.scope_kinds.append(_PythonScopeKind.CLASS)
        self._visit_statements(node.body)
        self.scope_kinds.pop()
        self.scopes.pop()

    def visit_Lambda(self, node: ast.Lambda) -> None:
        arguments = [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]
        if node.args.vararg is not None:
            arguments.append(node.args.vararg)
        if node.args.kwarg is not None:
            arguments.append(node.args.kwarg)
        self.scopes.append({argument.arg: _ClientBinding.UNKNOWN for argument in arguments})
        self.scope_kinds.append(_PythonScopeKind.FUNCTION)
        self.visit(node.body)
        self.scope_kinds.pop()
        self.scopes.pop()

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node, (node.elt,))

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node, (node.key, node.value))

    def _visit_comprehension(self, node: ast.AST, values: tuple[ast.AST, ...]) -> None:
        generators = list(getattr(node, "generators", []))
        names = {
            name
            for generator in generators
            for name in _python_target_names(generator.target)
        }
        self.scopes.append({name: _ClientBinding.UNKNOWN for name in names})
        self.scope_kinds.append(_PythonScopeKind.COMPREHENSION)
        for generator in generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)
        self.scope_kinds.pop()
        self.scopes.pop()

    def visit_Call(self, node: ast.Call) -> None:
        if (
            isinstance(node.func, ast.Attribute)
            and node.func.attr == "rpc"
            and isinstance(node.func.value, ast.Name)
            and self._lookup(node.func.value.id) == _ClientBinding.CLIENT
            and node.args
            and isinstance(node.args[0], ast.Constant)
            and isinstance(node.args[0].value, str)
        ):
            identity = _sql_identifier_from_rpc(node.args[0].value)
            if identity is not None:
                self.calls.append(_RpcCall(identity, int(getattr(node, "lineno", 0) or 0)))
        self.generic_visit(node)

    def _lookup(self, name: str) -> _ClientBinding:
        function_scope = any(
            kind in {_PythonScopeKind.FUNCTION, _PythonScopeKind.COMPREHENSION}
            for kind in self.scope_kinds
        )
        for scope, kind in zip(reversed(self.scopes), reversed(self.scope_kinds), strict=True):
            if function_scope and kind == _PythonScopeKind.CLASS:
                continue
            if name in scope:
                return scope[name]
        return _ClientBinding.UNKNOWN

    def _expression_binding(self, node: ast.AST) -> _ClientBinding:
        if isinstance(node, ast.Name):
            return self._lookup(node.id)
        if isinstance(node, ast.Call):
            if isinstance(node.func, ast.Name) and self._lookup(node.func.id) == _ClientBinding.FACTORY:
                return _ClientBinding.CLIENT
            if (
                isinstance(node.func, ast.Attribute)
                and node.func.attr == "create_client"
                and isinstance(node.func.value, ast.Name)
                and self._lookup(node.func.value.id) == _ClientBinding.MODULE
            ):
                return _ClientBinding.CLIENT
        return _ClientBinding.UNKNOWN


def _python_target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Starred):
        return _python_target_names(node.value)
    if isinstance(node, ast.Tuple | ast.List):
        return {name for item in node.elts for name in _python_target_names(item)}
    return set()


class _PythonBoundNameCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.names: set[str] = set()
        self.external: set[str] = set()

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, (ast.Store, ast.Del)):
            self.names.add(node.id)

    def visit_FunctionDef(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        self.names.add(node.name)

    visit_AsyncFunctionDef = visit_FunctionDef

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.names.add(node.name)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    visit_SetComp = visit_ListComp
    visit_GeneratorExp = visit_ListComp

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_Import(self, node: ast.Import) -> None:
        self.names.update(alias.asname or alias.name.split(".", 1)[0] for alias in node.names)

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        self.names.update(alias.asname or alias.name for alias in node.names if alias.name != "*")

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_MatchAs(self, node: ast.MatchAs) -> None:
        if node.name:
            self.names.add(node.name)
        self.generic_visit(node)

    def visit_Global(self, node: ast.Global) -> None:
        self.external.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.external.update(node.names)


def _python_bound_names(statements: list[ast.stmt]) -> set[str]:
    collector = _PythonBoundNameCollector()
    for statement in statements:
        collector.visit(statement)
    return collector.names - collector.external


def _python_rpc_calls(text: str) -> _ParseResult[tuple[_RpcCall, ...]]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return _parse_failed("graph_structured_client_parse_failed", exc.msg, int(exc.lineno or 0))
    visitor = _PythonRpcVisitor()
    visitor.visit_module(tree)
    return _parsed(tuple(sorted(set(visitor.calls), key=lambda call: (call.line, call.routine.key))))


def _python_supabase_module(module: str) -> bool:
    return module in {"supabase", "supabase.client", "supabase._sync.client", "supabase._async.client"}


def _python_annotation_name(node: ast.AST | None) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        return node.attr
    if isinstance(node, ast.Subscript):
        return _python_annotation_name(node.value)
    return ""


_CALLABLE_CONTROL_WORDS = {"if", "for", "while", "switch", "with"}
_PARAMETER_MODIFIERS = {
    "abstract", "const", "covariant", "external", "factory", "final", "late", "public",
    "private", "protected", "readonly", "required", "static", "var",
}


def _matching_open_token(tokens: list[_Token], close_index: int, *, opening: str = "(", closing: str = ")") -> int:
    depth = 0
    for index in range(close_index, -1, -1):
        if tokens[index].value == closing:
            depth += 1
        elif tokens[index].value == opening:
            depth -= 1
            if depth == 0:
                return index
    return -1


def _parameter_segments(tokens: list[_Token]) -> list[list[_Token]]:
    values: list[list[_Token]] = []
    current: list[_Token] = []
    depth = 0
    for token in tokens:
        if token.value in {"(", "[", "{"}:
            depth += 1
        elif token.value in {")", "]", "}"}:
            depth = max(0, depth - 1)
        if token.value == "," and depth == 0:
            values.append(current)
            current = []
        else:
            current.append(token)
    values.append(current)
    return values


def _parameter_declaration(tokens: list[_Token]) -> list[_Token]:
    depth = 0
    for index, token in enumerate(tokens):
        if token.value in {"(", "[", "{"}:
            depth += 1
        elif token.value in {")", "]", "}"}:
            depth = max(0, depth - 1)
        elif token.value == "=" and depth == 0:
            return tokens[:index]
    return tokens


def _parameter_bindings(
    tokens: list[_Token],
    *,
    language: str,
    scopes: list[dict[str, _ClientBinding]],
) -> dict[str, _ClientBinding]:
    bindings: dict[str, _ClientBinding] = {}
    for raw_segment in _parameter_segments(tokens):
        segment = _parameter_declaration(raw_segment)
        identifiers = [
            (index, token.value)
            for index, token in enumerate(segment)
            if token.kind == _TokenKind.IDENTIFIER and token.value not in _PARAMETER_MODIFIERS
        ]
        if not identifiers:
            continue
        if language in {"javascript", "typescript"}:
            colon = next((index for index, token in enumerate(segment) if token.value == ":"), len(segment))
            names = [value for index, value in identifiers if index < colon]
            if not names:
                continue
            name = names[-1]
            type_names = [value for index, value in identifiers if index > colon]
        else:
            name = identifiers[-1][1]
            type_names = [value for _index, value in identifiers[:-1]]
        binding = _ClientBinding.CLIENT if any(
            _lookup_binding(scopes, type_name) == _ClientBinding.CLIENT_TYPE
            for type_name in type_names
        ) else _ClientBinding.UNKNOWN
        bindings[name] = binding
    return bindings


def _arrow_parameter_span(tokens: list[_Token], arrow_index: int) -> tuple[int, int]:
    close_index = -1
    for index in range(arrow_index - 1, -1, -1):
        if tokens[index].value in {";", "{", "}"}:
            break
        if tokens[index].value == ")":
            close_index = index
            break
    if close_index >= 0:
        open_index = _matching_open_token(tokens, close_index)
        if open_index >= 0:
            return open_index + 1, close_index
    if arrow_index > 0 and tokens[arrow_index - 1].kind == _TokenKind.IDENTIFIER:
        return arrow_index - 1, arrow_index
    return -1, -1


def _callable_parameter_bindings(
    tokens: list[_Token],
    body_index: int,
    *,
    language: str,
    scopes: list[dict[str, _ClientBinding]],
) -> dict[str, _ClientBinding]:
    boundary = body_index - 1
    while boundary >= 0 and tokens[boundary].value not in {";", "{", "}"}:
        boundary -= 1
    for index in range(body_index - 2, boundary, -1):
        if tokens[index].value == "=" and tokens[index + 1].value == ">":
            start, end = _arrow_parameter_span(tokens, index)
            return _parameter_bindings(tokens[start:end], language=language, scopes=scopes) if start >= 0 else {}
    close_index = next(
        (index for index in range(body_index - 1, boundary, -1) if tokens[index].value == ")"),
        -1,
    )
    if close_index < 0:
        return {}
    open_index = _matching_open_token(tokens, close_index)
    if open_index < 0:
        return {}
    predecessor = tokens[open_index - 1].value if open_index > 0 else ""
    if predecessor in _CALLABLE_CONTROL_WORDS:
        return {}
    if predecessor:
        return _parameter_bindings(tokens[open_index + 1 : close_index], language=language, scopes=scopes)
    return {}


def _receiver_binding(
    tokens: list[_Token],
    call_index: int,
    *,
    language: str,
    scopes: list[dict[str, _ClientBinding]],
) -> _ClientBinding:
    receiver = tokens[call_index].value
    boundary = call_index - 1
    while boundary >= 0 and tokens[boundary].value not in {";", "{", "}"}:
        boundary -= 1
    for index in range(call_index - 2, boundary, -1):
        if tokens[index].value == "=" and tokens[index + 1].value == ">":
            start, end = _arrow_parameter_span(tokens, index)
            if start >= 0:
                arrow_bindings = _parameter_bindings(tokens[start:end], language=language, scopes=scopes)
                if receiver in arrow_bindings:
                    return arrow_bindings[receiver]
    return _lookup_binding(scopes, receiver)


def _javascript_rpc_calls(text: str) -> _ParseResult[tuple[_RpcCall, ...]]:
    token_result = _code_tokens(text, language="javascript")
    if token_result.status == _ParseStatus.FAILED:
        return token_result
    tokens = list(token_result.value or ())
    root: dict[str, _ClientBinding] = {}
    _javascript_import_bindings(tokens, root)
    scopes: list[dict[str, _ClientBinding]] = [root]
    calls: list[_RpcCall] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.value == "{":
            scopes.append(_callable_parameter_bindings(tokens, index, language="typescript", scopes=scopes))
        elif token.value == "}" and len(scopes) > 1:
            scopes.pop()
        elif token.value in {"const", "let", "var"} and index + 1 < len(tokens) and tokens[index + 1].kind == _TokenKind.IDENTIFIER:
            name = tokens[index + 1].value
            cursor = index + 2
            annotation = ""
            if cursor + 1 < len(tokens) and tokens[cursor].value == ":" and tokens[cursor + 1].kind == _TokenKind.IDENTIFIER:
                annotation = tokens[cursor + 1].value
                cursor += 2
            binding = _ClientBinding.CLIENT if _lookup_binding(scopes, annotation) == _ClientBinding.CLIENT_TYPE else _ClientBinding.UNKNOWN
            if cursor < len(tokens) and tokens[cursor].value == "=":
                expression = _javascript_expression_binding(tokens, cursor + 1, scopes)
                if expression != _ClientBinding.UNKNOWN:
                    binding = expression
            scopes[-1][name] = binding
        elif token.value in {"function", "class"} and index + 1 < len(tokens) and tokens[index + 1].kind == _TokenKind.IDENTIFIER:
            scopes[-1][tokens[index + 1].value] = _ClientBinding.UNKNOWN
        elif (
            token.kind == _TokenKind.IDENTIFIER
            and index + 2 < len(tokens)
            and tokens[index + 1].value == "="
        ):
            _assign_binding(scopes, token.value, _javascript_expression_binding(tokens, index + 2, scopes))
        if (
            token.kind == _TokenKind.IDENTIFIER
            and _receiver_binding(tokens, index, language="typescript", scopes=scopes) == _ClientBinding.CLIENT
            and index + 4 < len(tokens)
            and tokens[index + 1].value == "."
            and tokens[index + 2].value == "rpc"
            and tokens[index + 3].value == "("
            and tokens[index + 4].kind == _TokenKind.STRING
            and tokens[index + 4].static
            and index + 5 < len(tokens)
            and tokens[index + 5].value in {",", ")"}
        ):
            identity = _sql_identifier_from_rpc(tokens[index + 4].value)
            if identity is not None:
                calls.append(_RpcCall(identity, tokens[index + 2].line))
        index += 1
    return _parsed(tuple(sorted(set(calls), key=lambda call: (call.line, call.routine.key))))


def _javascript_import_bindings(tokens: list[_Token], bindings: dict[str, _ClientBinding]) -> None:
    index = 0
    while index < len(tokens):
        if tokens[index].value != "import":
            index += 1
            continue
        cursor = index + 1
        named: list[tuple[str, str]] = []
        if cursor < len(tokens) and tokens[cursor].value == "{":
            cursor += 1
            while cursor < len(tokens) and tokens[cursor].value != "}":
                if tokens[cursor].kind == _TokenKind.IDENTIFIER:
                    imported = tokens[cursor].value
                    bound = imported
                    if cursor + 2 < len(tokens) and tokens[cursor + 1].value == "as" and tokens[cursor + 2].kind == _TokenKind.IDENTIFIER:
                        bound = tokens[cursor + 2].value
                        cursor += 2
                    named.append((imported, bound))
                cursor += 1
        while cursor < len(tokens) and tokens[cursor].value not in {"from", ";"} and tokens[cursor].kind != _TokenKind.STRING:
            cursor += 1
        if cursor < len(tokens) and tokens[cursor].value == "from":
            cursor += 1
        if cursor < len(tokens) and tokens[cursor].kind == _TokenKind.STRING and _javascript_supabase_module(tokens[cursor].value):
            for imported, bound in named:
                if imported == "createClient":
                    bindings[bound] = _ClientBinding.FACTORY
                elif imported == "SupabaseClient":
                    bindings[bound] = _ClientBinding.CLIENT_TYPE
        index = cursor + 1


def _javascript_supabase_module(specifier: str) -> bool:
    if specifier == "@supabase/supabase-js" or specifier.startswith("@supabase/supabase-js/"):
        return True
    parsed = urlparse(specifier)
    if parsed.scheme not in {"http", "https"} or parsed.netloc != "esm.sh":
        return False
    path = parsed.path.lstrip("/")
    if not path.startswith("@supabase/supabase-js"):
        return False
    suffix = path[len("@supabase/supabase-js") :]
    return not suffix or suffix.startswith("@") or suffix.startswith("/")


def _javascript_expression_binding(tokens: list[_Token], start: int, scopes: list[dict[str, _ClientBinding]]) -> _ClientBinding:
    if start >= len(tokens) or tokens[start].kind != _TokenKind.IDENTIFIER:
        return _ClientBinding.UNKNOWN
    binding = _lookup_binding(scopes, tokens[start].value)
    if binding == _ClientBinding.FACTORY and start + 1 < len(tokens) and tokens[start + 1].value == "(":
        return _ClientBinding.CLIENT
    if binding == _ClientBinding.FACTORY and (start + 1 >= len(tokens) or tokens[start + 1].value in {";", ",", ")", "}"}):
        return _ClientBinding.FACTORY
    return _ClientBinding.UNKNOWN


def _dart_rpc_calls(text: str) -> _ParseResult[tuple[_RpcCall, ...]]:
    token_result = _code_tokens(text, language="dart")
    if token_result.status == _ParseStatus.FAILED:
        return token_result
    tokens = list(token_result.value or ())
    if not _dart_has_supabase_import(tokens):
        return _parsed(())
    scopes: list[dict[str, _ClientBinding]] = [{"SupabaseClient": _ClientBinding.CLIENT_TYPE}]
    calls: list[_RpcCall] = []
    index = 0
    while index < len(tokens):
        token = tokens[index]
        if token.value == "{":
            scopes.append(_callable_parameter_bindings(tokens, index, language="dart", scopes=scopes))
        elif token.value == "}" and len(scopes) > 1:
            scopes.pop()
        elif token.value in {"final", "var", "late"}:
            cursor = index + 1
            declared_type = ""
            if (
                cursor + 1 < len(tokens)
                and _lookup_binding(scopes, tokens[cursor].value) == _ClientBinding.CLIENT_TYPE
                and tokens[cursor + 1].kind == _TokenKind.IDENTIFIER
            ):
                declared_type = tokens[cursor].value
                cursor += 1
            if cursor < len(tokens) and tokens[cursor].kind == _TokenKind.IDENTIFIER:
                name = tokens[cursor].value
                binding = _ClientBinding.CLIENT if declared_type else _ClientBinding.UNKNOWN
                if cursor + 1 < len(tokens) and tokens[cursor + 1].value == "=":
                    expression = _dart_expression_binding(tokens, cursor + 2, scopes)
                    if expression != _ClientBinding.UNKNOWN:
                        binding = expression
                scopes[-1][name] = binding
        elif token.value == "class" and index + 1 < len(tokens) and tokens[index + 1].kind == _TokenKind.IDENTIFIER:
            scopes[-1][tokens[index + 1].value] = _ClientBinding.UNKNOWN
        elif (
            _lookup_binding(scopes, token.value) == _ClientBinding.CLIENT_TYPE
            and index + 2 < len(tokens)
            and tokens[index + 1].kind == _TokenKind.IDENTIFIER
            and tokens[index + 2].value in {"=", ";"}
        ):
            scopes[-1][tokens[index + 1].value] = _ClientBinding.CLIENT
        if _dart_direct_supabase_rpc(tokens, index):
            string_token = tokens[index + 8]
            identity = _sql_identifier_from_rpc(string_token.value)
            if identity is not None:
                calls.append(_RpcCall(identity, tokens[index + 6].line))
        elif (
            token.kind == _TokenKind.IDENTIFIER
            and _receiver_binding(tokens, index, language="dart", scopes=scopes) == _ClientBinding.CLIENT
            and index + 4 < len(tokens)
            and tokens[index + 1].value == "."
            and tokens[index + 2].value == "rpc"
            and tokens[index + 3].value == "("
            and tokens[index + 4].kind == _TokenKind.STRING
            and tokens[index + 4].static
            and index + 5 < len(tokens)
            and tokens[index + 5].value in {",", ")"}
        ):
            identity = _sql_identifier_from_rpc(tokens[index + 4].value)
            if identity is not None:
                calls.append(_RpcCall(identity, tokens[index + 2].line))
        index += 1
    return _parsed(tuple(sorted(set(calls), key=lambda call: (call.line, call.routine.key))))


def _dart_has_supabase_import(tokens: list[_Token]) -> bool:
    for index, token in enumerate(tokens[:-1]):
        if token.value == "import" and tokens[index + 1].kind == _TokenKind.STRING:
            if tokens[index + 1].value in _DART_SUPABASE_MODULES:
                return True
    return False


def _dart_expression_binding(tokens: list[_Token], start: int, scopes: list[dict[str, _ClientBinding]]) -> _ClientBinding:
    if start >= len(tokens):
        return _ClientBinding.UNKNOWN
    if _lookup_binding(scopes, tokens[start].value) == _ClientBinding.CLIENT_TYPE and start + 1 < len(tokens) and tokens[start + 1].value == "(":
        return _ClientBinding.CLIENT
    if _token_values(tokens, start, 5) == ("Supabase", ".", "instance", ".", "client"):
        return _ClientBinding.CLIENT
    if tokens[start].kind == _TokenKind.IDENTIFIER:
        return _lookup_binding(scopes, tokens[start].value)
    return _ClientBinding.UNKNOWN


def _dart_direct_supabase_rpc(tokens: list[_Token], start: int) -> bool:
    return (
        _token_values(tokens, start, 8) == ("Supabase", ".", "instance", ".", "client", ".", "rpc", "(")
        and start + 9 < len(tokens)
        and tokens[start + 8].kind == _TokenKind.STRING
        and tokens[start + 8].static
        and tokens[start + 9].value in {",", ")"}
    )


def _lookup_binding(scopes: list[dict[str, _ClientBinding]], name: str) -> _ClientBinding:
    if not name:
        return _ClientBinding.UNKNOWN
    for scope in reversed(scopes):
        if name in scope:
            return scope[name]
    return _ClientBinding.UNKNOWN


def _assign_binding(scopes: list[dict[str, _ClientBinding]], name: str, binding: _ClientBinding) -> None:
    for scope in reversed(scopes):
        if name in scope:
            scope[name] = binding
            return
    scopes[-1][name] = binding


def _code_tokens(text: str, *, language: str) -> _ParseResult[tuple[_Token, ...]]:
    tokens: list[_Token] = []
    index = 0
    line = 1
    while index < len(text):
        char = text[index]
        if char.isspace():
            line += 1 if char == "\n" else 0
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index + 2)
            index = len(text) if end < 0 else end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                return _parse_failed("graph_structured_client_parse_failed", "code block comment is unterminated", line)
            fragment = text[index : end + 2]
            line += fragment.count("\n")
            index = end + 2
            continue
        if language == "javascript" and char == "/" and _javascript_regex_can_start(tokens):
            start_line = line
            index, line, complete = _read_javascript_regex(text, index, line)
            if not complete:
                return _parse_failed("graph_structured_client_parse_failed", "code regular expression literal is unterminated", start_line)
            tokens.append(_Token(_TokenKind.SYMBOL, "<regex>", start_line))
            continue
        if language == "javascript" and char == "`":
            start_line = line
            value, index, line, complete, static = _read_javascript_template(text, index, line)
            if not complete:
                return _parse_failed("graph_structured_client_parse_failed", "code template literal is unterminated", start_line)
            tokens.append(_Token(_TokenKind.STRING, value, start_line, static=static))
            continue
        if char in {'"', "'", "`"}:
            start_line = line
            value, index, line, complete, static = _read_code_string(
                text,
                index,
                line,
                quote=char,
                dart_interpolation=language == "dart",
            )
            if not complete:
                return _parse_failed("graph_structured_client_parse_failed", "code string literal is unterminated", start_line)
            tokens.append(_Token(_TokenKind.STRING, value, start_line, static=static))
            continue
        if char.isalpha() or char in {"_", "$"}:
            start = index
            while index < len(text) and (text[index].isalnum() or text[index] in {"_", "$"}):
                index += 1
            tokens.append(_Token(_TokenKind.IDENTIFIER, text[start:index], line))
            continue
        if char in {".", "(", ")", "{", "}", "[", "]", ",", ";", "=", ":", "?", "+", "-", "*", "/", "%", "!", "~", "&", "|", "^", "<", ">"}:
            tokens.append(_Token(_TokenKind.SYMBOL, char, line))
        index += 1
    return _parsed(tuple(tokens))


_JAVASCRIPT_REGEX_PREFIX_SYMBOLS = frozenset(
    {"(", "[", "{", ",", ";", "=", ":", "?", "+", "-", "*", "/", "%", "!", "~", "&", "|", "^", "<", ">"}
)
_JAVASCRIPT_REGEX_PREFIX_KEYWORDS = frozenset(
    {"await", "case", "delete", "do", "else", "in", "instanceof", "new", "of", "return", "throw", "typeof", "void", "yield"}
)
_JAVASCRIPT_STATEMENT_PAREN_KEYWORDS = frozenset({"if", "for", "while", "with"})


def _javascript_regex_can_start(tokens: list[_Token]) -> bool:
    if not tokens:
        return True
    previous = tokens[-1]
    if previous.kind == _TokenKind.SYMBOL:
        if previous.value in _JAVASCRIPT_REGEX_PREFIX_SYMBOLS:
            return True
        if previous.value == ")":
            opening = _matching_open_token(tokens, len(tokens) - 1)
            if opening > 0 and tokens[opening - 1].kind == _TokenKind.IDENTIFIER:
                prefix = tokens[opening - 1].value
                if prefix in _JAVASCRIPT_STATEMENT_PAREN_KEYWORDS:
                    return True
                if prefix == "await" and opening > 1 and tokens[opening - 2].value == "for":
                    return True
        return False
    return previous.kind == _TokenKind.IDENTIFIER and previous.value in _JAVASCRIPT_REGEX_PREFIX_KEYWORDS


def _read_javascript_regex(text: str, index: int, line: int) -> tuple[int, int, bool]:
    index += 1
    escaped = False
    in_character_class = False
    while index < len(text):
        char = text[index]
        if char == "\n" and not escaped:
            return index, line, False
        if char == "\n":
            line += 1
        if escaped:
            escaped = False
        elif char == "\\":
            escaped = True
        elif char == "[":
            in_character_class = True
        elif char == "]" and in_character_class:
            in_character_class = False
        elif char == "/" and not in_character_class:
            index += 1
            while index < len(text) and text[index].isalpha():
                index += 1
            return index, line, True
        index += 1
    return index, line, False


def _read_javascript_template(text: str, index: int, line: int) -> tuple[str, int, int, bool, bool]:
    value: list[str] = []
    index += 1
    escaped = False
    static = True
    while index < len(text):
        char = text[index]
        if char == "\n":
            line += 1
        if escaped:
            value.append(char)
            escaped = False
            index += 1
            continue
        if char == "\\":
            escaped = True
            index += 1
            continue
        if char == "`":
            return "".join(value), index + 1, line, True, static
        if text.startswith("${", index):
            static = False
            value.append("${}")
            index, line, complete = _skip_javascript_template_expression(text, index + 2, line)
            if not complete:
                return "".join(value), index, line, False, static
            continue
        value.append(char)
        index += 1
    return "".join(value), index, line, False, static


def _skip_javascript_template_expression(text: str, index: int, line: int) -> tuple[int, int, bool]:
    depth = 1
    tokens: list[_Token] = []
    while index < len(text):
        char = text[index]
        if char.isspace():
            line += 1 if char == "\n" else 0
            index += 1
            continue
        if text.startswith("//", index):
            end = text.find("\n", index + 2)
            if end < 0:
                return len(text), line, False
            index = end
            continue
        if text.startswith("/*", index):
            end = text.find("*/", index + 2)
            if end < 0:
                return len(text), line, False
            fragment = text[index : end + 2]
            line += fragment.count("\n")
            index = end + 2
            continue
        if char == "/" and _javascript_regex_can_start(tokens):
            start_line = line
            index, line, complete = _read_javascript_regex(text, index, line)
            if not complete:
                return index, line, False
            tokens.append(_Token(_TokenKind.SYMBOL, "<regex>", start_line))
            continue
        if char == "`":
            start_line = line
            _value, index, line, complete, static = _read_javascript_template(text, index, line)
            if not complete:
                return index, line, False
            tokens.append(_Token(_TokenKind.STRING, "", start_line, static=static))
            continue
        if char in {'"', "'"}:
            start_line = line
            value, index, line, complete, static = _read_code_string(
                text,
                index,
                line,
                quote=char,
                dart_interpolation=False,
            )
            if not complete:
                return index, line, False
            tokens.append(_Token(_TokenKind.STRING, value, start_line, static=static))
            continue
        if char.isalpha() or char in {"_", "$"}:
            start = index
            while index < len(text) and (text[index].isalnum() or text[index] in {"_", "$"}):
                index += 1
            tokens.append(_Token(_TokenKind.IDENTIFIER, text[start:index], line))
            continue
        if char == "{":
            depth += 1
            tokens.append(_Token(_TokenKind.SYMBOL, char, line))
            index += 1
            continue
        if char == "}":
            depth -= 1
            index += 1
            if depth == 0:
                return index, line, True
            tokens.append(_Token(_TokenKind.SYMBOL, char, line))
            continue
        if char in {".", "(", ")", "[", "]", ",", ";", "=", ":", "?", "+", "-", "*", "/", "%", "!", "~", "&", "|", "^", "<", ">"}:
            tokens.append(_Token(_TokenKind.SYMBOL, char, line))
        index += 1
    return index, line, False


def _read_code_string(
    text: str,
    index: int,
    line: int,
    *,
    quote: str,
    dart_interpolation: bool,
) -> tuple[str, int, int, bool, bool]:
    value: list[str] = []
    index += 1
    escaped = False
    static = True
    while index < len(text):
        char = text[index]
        if char == "\n":
            line += 1
        if escaped:
            value.append(char)
            escaped = False
        elif char == "\\":
            escaped = True
        elif quote == "`" and text.startswith("${", index):
            static = False
            value.append("${")
            index += 2
            continue
        elif (
            dart_interpolation
            and quote in {'"', "'"}
            and char == "$"
            and index + 1 < len(text)
            and (text[index + 1] == "{" or text[index + 1].isalpha() or text[index + 1] == "_")
        ):
            static = False
            value.append(char)
        elif char == quote:
            return "".join(value), index + 1, line, True, static
        else:
            value.append(char)
        index += 1
    return "".join(value), index, line, False, static


def _sql_identifier_from_rpc(value: str) -> _SqlIdentifier | None:
    raw_parts = value.strip().split(".")
    if not raw_parts or any(not part for part in raw_parts):
        return None
    parts: list[_SqlIdentifierPart] = []
    for part in raw_parts:
        if not (part[0].isalpha() or part[0] == "_") or not all(char.isalnum() or char in {"_", "$"} for char in part):
            return None
        canonical = part.casefold()
        parts.append(_SqlIdentifierPart(canonical, canonical))
    return _SqlIdentifier(tuple(parts))


def _resolve_sql_relations(
    definitions: list[_SqlFact],
    references: list[_SqlFact],
) -> Iterable[tuple[str, str, StructuredRelationEvidence]]:
    groups: dict[tuple[object, ...], list[_SqlFact]] = {}
    names: dict[tuple[_SqlObjectKind, str], set[tuple[str, ...]]] = {}
    for definition in definitions:
        signature_key: object = definition.routine_signature.parameter_types if definition.routine_signature is not None else ()
        key = (definition.object_kind, definition.identity.key, signature_key)
        groups.setdefault(key, []).append(definition)
        names.setdefault((definition.object_kind, definition.identity.short_key), set()).add(definition.identity.key)
    for values in groups.values():
        values.sort(key=lambda item: (item.path, item.line))

    for reference in references:
        identity_keys: set[tuple[str, ...]]
        if len(reference.identity.parts) > 1:
            identity_keys = {reference.identity.key}
            confidence = "high"
        else:
            identity_keys = names.get((reference.object_kind, reference.identity.short_key), set())
            confidence = "medium"
        if len(identity_keys) != 1:
            continue
        identity_key = next(iter(identity_keys))
        compatible: list[list[_SqlFact]] = []
        for (kind, name_key, signature_key), values in groups.items():
            if kind != reference.object_kind or name_key != identity_key:
                continue
            if reference.object_kind == _SqlObjectKind.ROUTINE and reference.argument_count is not None:
                if len(signature_key) != reference.argument_count:
                    continue
            compatible.append(values)
        owners: list[_SqlFact] = []
        for values in compatible:
            active: list[_SqlFact] = []
            for definition in values:
                if definition.path == reference.path:
                    continue
                if reference.operation != _SqlOperation.CLIENT_RPC and definition.path > reference.path:
                    continue
                if definition.replace:
                    active = [definition]
                else:
                    active.append(definition)
            if len(active) == 1:
                owners.extend(active)
            elif len(active) > 1:
                owners.extend(active)
        if len(owners) != 1:
            continue
        owner = owners[0]
        if reference.object_kind == _SqlObjectKind.ROUTINE:
            relation = StructuredRelationType.SQL_RPC_DEPENDENCY
        elif PurePosixPath(reference.path).name.casefold() == "seed.sql" or "seeds" in {
            part.casefold() for part in PurePosixPath(reference.path).parts[:-1]
        }:
            relation = StructuredRelationType.SQL_SEED_DEPENDENCY
        else:
            relation = StructuredRelationType.SQL_SCHEMA_DEPENDENCY
        yield (
            reference.path,
            owner.path,
            StructuredRelationEvidence(
                relation,
                reference.identity.display,
                reference.line,
                confidence=confidence,
                operation=reference.operation.value,
            ),
        )

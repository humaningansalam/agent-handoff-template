from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any
from urllib.parse import quote


def encode_component(value: str) -> str:
    return quote(value, safe="")


def repository_id(repo_id: str) -> str:
    return f"repo:{encode_component(repo_id)}"


def file_id(repo_id: str, path: str) -> str:
    return f"repo:{encode_component(repo_id)}:file:{encode_component(path)}"


def import_ref_id(
    repo_id: str,
    importer_path: str,
    language: str,
    raw_import: str,
    *,
    form: str = "raw",
    level: int = 0,
    module: str = "",
    imported_name: str = "",
) -> str:
    resolved_module = raw_import if form == "raw" and not module else module
    parts = (repo_id, importer_path, language, form, str(level), resolved_module, imported_name, raw_import)
    return "repo:" + encode_component(parts[0]) + ":import-ref:" + ":".join(encode_component(part) for part in parts[1:])


def topic_id(repo_id: str, topic: str) -> str:
    return f"repo:{encode_component(repo_id)}:topic:{encode_component(topic)}"


def task_id(value: str) -> str:
    return f"task:{encode_component(value)}"


def change_event_id(value: str, index: int) -> str:
    return f"task:{encode_component(value)}:change:{index}"


def artifact_id(value: str, artifact_path: str) -> str:
    return f"task:{encode_component(value)}:artifact:{encode_component(artifact_path)}"


def document_id(path: str) -> str:
    return f"document:{encode_component(path)}"


def knowledge_id(value: str) -> str:
    return f"knowledge:{encode_component(value)}"


def symbol_id(repo_id: str, provider: str, provider_symbol_id: str) -> str:
    return f"repo:{encode_component(repo_id)}:symbol:{encode_component(provider)}:{encode_component(provider_symbol_id)}"


def anchor_id(repo_id: str, provider: str, path: str, start_line: int, start_col: int, end_line: int, end_col: int) -> str:
    span = f"{start_line}:{start_col}:{end_line}:{end_col}"
    return f"repo:{encode_component(repo_id)}:anchor:{encode_component(provider)}:{encode_component(path)}:{encode_component(span)}"


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_data(data: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


class GraphContextAnchorKind(StrEnum):
    FILE = "file"
    SYMBOL = "symbol"


class GraphQuerySelectorKind(StrEnum):
    FILE = "file"
    TOPIC = "topic"
    IMPORT = "import"
    SYMBOL = "symbol"
    CALLERS_OF = "callers_of"
    CALLEES_OF = "callees_of"
    IMPACT_FILE = "impact_file"
    IMPACT_SYMBOL = "impact_symbol"
    TASK = "task"
    ARTIFACT = "artifact"


@dataclass(frozen=True)
class GraphQuerySelectorSchema:
    value_field: str
    required_fields: frozenset[str] = frozenset()
    optional_fields: frozenset[str] = frozenset()


GRAPH_QUERY_SELECTOR_SCHEMAS: dict[GraphQuerySelectorKind, GraphQuerySelectorSchema] = {
    GraphQuerySelectorKind.FILE: GraphQuerySelectorSchema("path"),
    GraphQuerySelectorKind.TOPIC: GraphQuerySelectorSchema("topic"),
    GraphQuerySelectorKind.IMPORT: GraphQuerySelectorSchema("raw_import"),
    GraphQuerySelectorKind.SYMBOL: GraphQuerySelectorSchema("symbol", optional_fields=frozenset({"in_file"})),
    GraphQuerySelectorKind.CALLERS_OF: GraphQuerySelectorSchema("symbol", optional_fields=frozenset({"in_file"})),
    GraphQuerySelectorKind.CALLEES_OF: GraphQuerySelectorSchema("symbol", optional_fields=frozenset({"in_file"})),
    GraphQuerySelectorKind.IMPACT_FILE: GraphQuerySelectorSchema("path", required_fields=frozenset({"depth"})),
    GraphQuerySelectorKind.IMPACT_SYMBOL: GraphQuerySelectorSchema(
        "symbol",
        required_fields=frozenset({"depth"}),
        optional_fields=frozenset({"in_file"}),
    ),
    GraphQuerySelectorKind.TASK: GraphQuerySelectorSchema("task_id"),
    GraphQuerySelectorKind.ARTIFACT: GraphQuerySelectorSchema("path"),
}


def canonical_graph_query_selector(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        raise ValueError("graph query selector must be an object")
    raw_kind = value.get("type")
    if not isinstance(raw_kind, str):
        raise ValueError("graph query selector type must be a string")
    kind = GraphQuerySelectorKind(raw_kind)
    schema = GRAPH_QUERY_SELECTOR_SCHEMAS[kind]
    expected_fields = {"type", schema.value_field, *schema.required_fields, *schema.optional_fields}
    present_fields = set(value)
    if not {"type", schema.value_field, *schema.required_fields}.issubset(present_fields) or not present_fields.issubset(expected_fields):
        raise ValueError("graph query selector fields do not match its type")
    primary_value = value.get(schema.value_field)
    if not isinstance(primary_value, str) or not primary_value.strip() or primary_value != primary_value.strip():
        raise ValueError("graph query selector value must be canonical")
    result: dict[str, Any] = {"type": kind.value, schema.value_field: primary_value}
    if "in_file" in present_fields:
        in_file = value.get("in_file")
        if not isinstance(in_file, str) or not in_file.strip() or in_file != in_file.strip():
            raise ValueError("graph query selector in_file must be canonical")
        result["in_file"] = in_file
    if "depth" in present_fields:
        depth = value.get("depth")
        if not isinstance(depth, int) or isinstance(depth, bool) or depth < 1:
            raise ValueError("graph query selector depth must be a positive integer")
        result["depth"] = depth
    return result


@dataclass(frozen=True)
class GraphContextAnchor:
    kind: GraphContextAnchorKind
    path: str
    symbol: str = ""
    line_start: int = 0
    line_end: int = 0
    provider: str = ""
    provider_symbol_id: str = ""

    def key(self) -> tuple[str, str, str, int, int, str, str]:
        return (
            self.kind.value,
            self.path,
            self.symbol,
            self.line_start,
            self.line_end,
            self.provider,
            self.provider_symbol_id,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {"kind": self.kind.value, "path": self.path}
        if self.symbol:
            data["symbol"] = self.symbol
        if self.line_start:
            data["line_start"] = self.line_start
        if self.line_end:
            data["line_end"] = self.line_end
        if self.provider:
            data["provider"] = self.provider
        if self.provider_symbol_id:
            data["provider_symbol_id"] = self.provider_symbol_id
        return data


@dataclass(frozen=True)
class GraphContinuation:
    kind: GraphContextAnchorKind
    value: str
    in_file: str = ""
    query_types: tuple[GraphQuerySelectorKind, ...] = ()
    actions: tuple[str, ...] = ()

    def selector_dict(self) -> dict[str, str]:
        data = {"kind": self.kind.value, "value": self.value}
        if self.in_file:
            data["in_file"] = self.in_file
        return data

    def to_dict(self) -> dict[str, Any]:
        return {
            "selector": self.selector_dict(),
            "query_types": [query_type.value for query_type in self.query_types],
            "actions": list(self.actions),
        }


@dataclass(frozen=True)
class GraphNode:
    id: str
    kind: str
    identity: dict[str, Any]
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "id": self.id,
            "kind": self.kind,
            "identity": self.identity,
        }
        if self.facts:
            data["facts"] = self.facts
        return data


@dataclass(frozen=True)
class GraphEdge:
    kind: str
    from_id: str
    to_id: str
    assertion: str
    source: str
    facts: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind,
            "from": self.from_id,
            "to": self.to_id,
            "assertion": self.assertion,
            "source": self.source,
        }
        if self.facts:
            data["facts"] = self.facts
        return data


@dataclass(frozen=True)
class ProviderCoverage:
    capability: str
    eligible_paths: tuple[str, ...]
    analyzed_paths: tuple[str, ...]
    unsupported_paths: tuple[str, ...]
    failed_paths: tuple[str, ...]
    evidence_level: str
    coverage_gaps: tuple[str, ...] = ()

    @property
    def status(self) -> str:
        if not self.eligible_paths:
            return "complete"
        if self.failed_paths and not self.analyzed_paths and not self.unsupported_paths:
            return "unavailable"
        if self.unsupported_paths and not self.analyzed_paths and not self.failed_paths:
            return "unsupported"
        if self.failed_paths or self.unsupported_paths or self.coverage_gaps:
            return "partial"
        return "complete"

    def to_dict(self) -> dict[str, object]:
        return {
            "capability": self.capability,
            "status": self.status,
            "eligible_paths": list(self.eligible_paths),
            "analyzed_paths": list(self.analyzed_paths),
            "unsupported_paths": list(self.unsupported_paths),
            "failed_paths": list(self.failed_paths),
            "evidence_level": self.evidence_level,
            "coverage_gaps": list(self.coverage_gaps),
        }


@dataclass(frozen=True)
class GraphSnapshot:
    repository: dict[str, str]
    sources: list[dict[str, str]]
    completeness: dict[str, Any]
    nodes: list[GraphNode]
    edges: list[GraphEdge]
    schema: str = "repoctl.graph.snapshot"
    schema_version: int = 1
    authoritative: bool = False
    capabilities: list[str] = field(default_factory=lambda: ["repository", "file", "import_ref", "topic"])
    snapshot_digest: str = ""

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "authoritative": self.authoritative,
            "repository": self.repository,
            "capabilities": sorted(self.capabilities),
            "sources": sorted(self.sources, key=lambda source: (source.get("kind", ""), source.get("assertion", ""), source.get("digest", ""))),
            "completeness": self.completeness,
            "nodes": [node.to_dict() for node in sorted(self.nodes, key=lambda node: node.id)],
            "edges": [edge.to_dict() for edge in sorted(self.edges, key=lambda edge: (edge.kind, edge.from_id, edge.to_id, edge.assertion, edge.source))],
        }
        if include_digest:
            data["snapshot_digest"] = self.snapshot_digest or digest_data(data)
        return data

    def with_digest(self) -> GraphSnapshot:
        return GraphSnapshot(
            repository=self.repository,
            sources=self.sources,
            completeness=self.completeness,
            nodes=self.nodes,
            edges=self.edges,
            schema=self.schema,
            schema_version=self.schema_version,
            authoritative=self.authoritative,
            capabilities=self.capabilities,
            snapshot_digest=digest_data(self.to_dict(include_digest=False)),
        )

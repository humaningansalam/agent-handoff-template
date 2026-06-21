from __future__ import annotations

import hashlib
import json
from dataclasses import dataclass, field
from typing import Any
from urllib.parse import quote


def encode_component(value: str) -> str:
    return quote(value, safe="")


def repository_id(repo_id: str) -> str:
    return f"repo:{encode_component(repo_id)}"


def file_id(repo_id: str, path: str) -> str:
    return f"repo:{encode_component(repo_id)}:file:{encode_component(path)}"


def import_ref_id(repo_id: str, language: str, raw_import: str) -> str:
    return f"repo:{encode_component(repo_id)}:import-ref:{encode_component(language)}:{encode_component(raw_import)}"


def topic_id(repo_id: str, topic: str) -> str:
    return f"repo:{encode_component(repo_id)}:topic:{encode_component(topic)}"


def task_id(value: str) -> str:
    return f"task:{encode_component(value)}"


def change_event_id(value: str, index: int) -> str:
    return f"task:{encode_component(value)}:change:{index}"


def artifact_id(value: str, artifact_path: str) -> str:
    return f"task:{encode_component(value)}:artifact:{encode_component(artifact_path)}"


def symbol_id(repo_id: str, provider: str, provider_symbol_id: str) -> str:
    return f"repo:{encode_component(repo_id)}:symbol:{encode_component(provider)}:{encode_component(provider_symbol_id)}"


def anchor_id(repo_id: str, provider: str, path: str, start_line: int, start_col: int, end_line: int, end_col: int) -> str:
    span = f"{start_line}:{start_col}:{end_line}:{end_col}"
    return f"repo:{encode_component(repo_id)}:anchor:{encode_component(provider)}:{encode_component(path)}:{encode_component(span)}"


def canonical_json(data: Any) -> str:
    return json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":"))


def digest_data(data: Any) -> str:
    return "sha256:" + hashlib.sha256(canonical_json(data).encode("utf-8")).hexdigest()


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

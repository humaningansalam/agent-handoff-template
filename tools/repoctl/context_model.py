from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .document_roles import DocumentRole
from .graph_model import GraphContextAnchor, digest_data


class ContextSectionKind(StrEnum):
    UNSPECIFIED = "unspecified"
    FILE = "file"
    PROVIDER_SYMBOL = "provider_symbol"
    PROVIDER_RELATIONSHIP = "provider_relationship"
    DOCUMENT = "document"
    CONFIG = "config"
    TASK = "task"
    VERIFICATION = "verification"


class ContextEvidenceKind(StrEnum):
    EXACT_PATH = "exact_path"
    EXACT_FILENAME = "exact_filename"
    EXACT_SYMBOL = "exact_symbol"
    EXACT_RELATIONSHIP = "exact_relationship"
    PATH_TERMS = "path_terms"
    SECTION_TERMS = "section_terms"
    BODY_TERMS = "body_terms"
    FTS = "fts"
    STARTUP_READING = "startup_reading"
    GRAPH_RELATION = "graph_relation"
    REVIEWED_KNOWLEDGE_PATH = "reviewed_knowledge_path"


class ContextAnchorStrength(StrEnum):
    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"
    EXACT = "exact"
    EXPLICIT = "explicit"


CONTEXT_ANCHOR_STRENGTH_PRIORITY = {
    ContextAnchorStrength.NONE: 0,
    ContextAnchorStrength.WEAK: 1,
    ContextAnchorStrength.STRONG: 2,
    ContextAnchorStrength.EXPLICIT: 3,
    ContextAnchorStrength.EXACT: 4,
}


class ContextAnchorStatus(StrEnum):
    RESOLVED = "resolved"
    AMBIGUOUS = "ambiguous"
    UNRESOLVED = "unresolved"


class ContextAnchorResolutionCode(StrEnum):
    RESOLVED = "context_graph_anchor_resolved"
    AMBIGUOUS = "context_graph_anchor_ambiguous"
    UNRESOLVED = "context_graph_anchor_unresolved"


@dataclass(frozen=True)
class ContextSourceRef:
    kind: str
    path: str
    section: str = ""
    section_kind: ContextSectionKind = ContextSectionKind.UNSPECIFIED
    line_start: int = 0
    line_end: int = 0
    source_fact_id: str = ""
    content_sha256: str = ""

    def key(self) -> tuple[str, str, str, str, int, int, str]:
        return (
            self.kind,
            self.path,
            self.section,
            self.section_kind.value,
            self.line_start,
            self.line_end,
            self.source_fact_id,
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind,
            "path": self.path,
            "content_sha256": self.content_sha256,
        }
        if self.section:
            data["section"] = self.section
        if self.section_kind != ContextSectionKind.UNSPECIFIED:
            data["section_kind"] = self.section_kind.value
        if self.line_start:
            data["line_start"] = self.line_start
        if self.line_end:
            data["line_end"] = self.line_end
        if self.source_fact_id:
            data["source_fact_id"] = self.source_fact_id
        return data


@dataclass(frozen=True)
class ContextGraphAnchorCandidate:
    anchor: GraphContextAnchor
    source_ref: ContextSourceRef
    evidence_kinds: tuple[ContextEvidenceKind, ...]
    anchor_strength: ContextAnchorStrength
    related_record_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "anchor": self.anchor.to_dict(),
            "source_ref": self.source_ref.to_dict(),
            "evidence_kinds": sorted(kind.value for kind in set(self.evidence_kinds)),
            "anchor_strength": self.anchor_strength.value,
            "related_record_ids": sorted(set(self.related_record_ids)),
        }


@dataclass(frozen=True)
class ContextAnchorResolution:
    status: ContextAnchorStatus
    code: ContextAnchorResolutionCode
    anchors: tuple[ContextGraphAnchorCandidate, ...] = ()
    candidates: tuple[ContextGraphAnchorCandidate, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "status": self.status.value,
            "code": self.code.value,
            "anchors": [candidate.to_dict() for candidate in self.anchors],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }


@dataclass(frozen=True)
class ContextCandidate:
    source_ref: ContextSourceRef
    text: str
    score: float
    score_breakdown: dict[str, float]
    selection_reasons: list[str] = field(default_factory=list)
    graph_path: list[dict[str, Any]] = field(default_factory=list)
    evidence_kinds: tuple[ContextEvidenceKind, ...] = ()
    anchor_strength: ContextAnchorStrength = ContextAnchorStrength.NONE
    related_record_ids: tuple[str, ...] = ()
    document_role: DocumentRole = DocumentRole.UNSPECIFIED

    def to_dict(self) -> dict[str, Any]:
        data = {
            "source_ref": self.source_ref.to_dict(),
            "excerpt": self.text,
            "score": round(self.score, 6),
            "score_breakdown": {key: round(value, 6) for key, value in sorted(self.score_breakdown.items())},
            "selection_reasons": sorted(set(self.selection_reasons)),
            "graph_path": self.graph_path,
            "evidence_kinds": sorted(kind.value for kind in set(self.evidence_kinds)),
            "anchor_strength": self.anchor_strength.value,
            "related_record_ids": sorted(set(self.related_record_ids)),
        }
        if self.document_role != DocumentRole.UNSPECIFIED:
            data["document_role"] = self.document_role.value
        return data


@dataclass(frozen=True)
class ContextBundle:
    repository: dict[str, str]
    query: dict[str, Any]
    source_snapshots: dict[str, str]
    completeness: dict[str, Any]
    evidence: list[ContextCandidate]
    selection: dict[str, Any]
    knowledge_results: list[dict[str, Any]] = field(default_factory=list)
    groups: dict[str, list[dict[str, Any]]] = field(default_factory=dict)
    relationship_candidates: list[dict[str, Any]] = field(default_factory=list)
    schema: str = "repoctl.context.bundle"
    schema_version: int = 11
    authoritative: bool = False
    bundle_digest: str = ""

    def to_dict(self, *, include_digest: bool = True) -> dict[str, Any]:
        data: dict[str, Any] = {
            "schema": self.schema,
            "schema_version": self.schema_version,
            "authoritative": self.authoritative,
            "repository": self.repository,
            "query": self.query,
            "source_snapshots": dict(sorted(self.source_snapshots.items())),
            "completeness": self.completeness,
            "evidence": [candidate.to_dict() for candidate in self.evidence],
            "knowledge_results": sorted(self.knowledge_results, key=_knowledge_sort_key),
            "groups": {key: value for key, value in sorted(self.groups.items())},
            "relationship_candidates": self.relationship_candidates,
            "relationship_candidate_count": len(self.relationship_candidates),
            "relationship_candidates_truncated": False,
            "selection": self.selection,
        }
        if include_digest:
            data["bundle_digest"] = self.bundle_digest or digest_data(data)
        return data

    def with_digest(self) -> ContextBundle:
        return ContextBundle(
            repository=self.repository,
            query=self.query,
            source_snapshots=self.source_snapshots,
            completeness=self.completeness,
            evidence=self.evidence,
            selection=self.selection,
            knowledge_results=self.knowledge_results,
            groups=self.groups,
            relationship_candidates=self.relationship_candidates,
            schema=self.schema,
            schema_version=self.schema_version,
            authoritative=self.authoritative,
            bundle_digest=digest_data(self.to_dict(include_digest=False)),
        )

def _knowledge_sort_key(item: dict[str, Any]) -> tuple[float, str]:
    record = item.get("record") if isinstance(item.get("record"), dict) else {}
    return (-float(item.get("score") or 0.0), str(record.get("id") or ""))

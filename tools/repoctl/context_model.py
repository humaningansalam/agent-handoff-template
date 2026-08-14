from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from typing import Any

from .document_roles import DocumentRole
from .graph_model import GraphContextAnchor, GraphContinuation, digest_data


class ContextResultMode(StrEnum):
    AUTO = "auto"
    STARTUP_READING = "startup_reading"
    CODE_LOCATION = "code_location"
    CALL_IMPACT = "call_impact"
    FILE_IMPACT = "file_impact"
    AUTHORITY_OR_CONTRACT = "authority_or_contract"
    PAST_DECISION = "past_decision"
    INVARIANT = "invariant"
    FAILURE_MODE = "failure_mode"


class ContextSourceKind(StrEnum):
    CURRENT_SOURCE = "current_source"
    CONFIG = "config"
    STRUCTURED_DATA = "structured_data"


CONTEXT_SOURCE_KIND_VALUES = frozenset(kind.value for kind in ContextSourceKind)
LEXICAL_CONTEXT_SOURCE_KIND_VALUES = frozenset(
    {
        ContextSourceKind.CURRENT_SOURCE.value,
        ContextSourceKind.CONFIG.value,
    }
)


class ContextSectionKind(StrEnum):
    UNSPECIFIED = "unspecified"
    FILE = "file"
    PROVIDER_SYMBOL = "provider_symbol"
    PROVIDER_RELATIONSHIP = "provider_relationship"
    DOCUMENT = "document"
    CONFIG = "config"
    STRUCTURED_DATA = "structured_data"
    TASK = "task"
    VERIFICATION = "verification"


class ContextEvidenceKind(StrEnum):
    EXACT_PATH = "exact_path"
    EXACT_FILENAME = "exact_filename"
    EXACT_SYMBOL = "exact_symbol"
    EXACT_RELATIONSHIP = "exact_relationship"
    EXACT_TASK = "exact_task"
    NAMED_FILE_IDENTITY = "named_file_identity"
    NAMED_SYMBOL_IDENTITY = "named_symbol_identity"
    PATH_TERMS = "path_terms"
    SECTION_TERMS = "section_terms"
    BODY_TERMS = "body_terms"
    FTS = "fts"
    STARTUP_READING = "startup_reading"
    GRAPH_SEED = "graph_seed"
    GRAPH_RELATION = "graph_relation"
    REVIEWED_KNOWLEDGE_PATH = "reviewed_knowledge_path"
    HISTORY_CORROBORATION = "history_corroboration"


class ContextHistoryMatchStrength(StrEnum):
    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"
    EXACT = "exact"


class ContextAnchorStrength(StrEnum):
    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"
    EXACT = "exact"
    EXPLICIT = "explicit"


class ContextGraphAnchorProvenance(StrEnum):
    EXACT_IDENTITY = "exact_identity"
    PROVIDER_SYMBOL = "provider_symbol"
    REVIEWED_KNOWLEDGE = "reviewed_knowledge"
    LEXICAL_FILE = "lexical_file"


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
    provider: str = ""
    provider_symbol_id: str = ""
    content_sha256: str = ""

    def key(self) -> tuple[str, str, str, str, int, int, str, str, str]:
        return (
            self.kind,
            self.path,
            self.section,
            self.section_kind.value,
            self.line_start,
            self.line_end,
            self.source_fact_id,
            self.provider,
            self.provider_symbol_id,
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
        if self.provider:
            data["provider"] = self.provider
        if self.provider_symbol_id:
            data["provider_symbol_id"] = self.provider_symbol_id
        return data


@dataclass(frozen=True)
class ContextGraphAnchorCandidate:
    anchor: GraphContextAnchor
    source_ref: ContextSourceRef
    evidence_kinds: tuple[ContextEvidenceKind, ...]
    anchor_strength: ContextAnchorStrength
    anchor_provenance: ContextGraphAnchorProvenance
    retrieval_lane: str = ""
    lexical_rank: int = 0
    retrieval_score: float = 0.0
    score_breakdown: dict[str, float] = field(default_factory=dict)
    query_term_matches: dict[str, tuple[str, ...]] = field(default_factory=dict)
    graph_support: dict[str, Any] = field(default_factory=dict)
    component_ids: tuple[str, ...] = ()
    related_record_ids: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        data = {
            "anchor": self.anchor.to_dict(),
            "source_ref": self.source_ref.to_dict(),
            "evidence_kinds": sorted(kind.value for kind in set(self.evidence_kinds)),
            "anchor_strength": self.anchor_strength.value,
            "anchor_provenance": self.anchor_provenance.value,
            "related_record_ids": sorted(set(self.related_record_ids)),
        }
        if self.retrieval_lane:
            data["retrieval_lane"] = self.retrieval_lane
        if self.lexical_rank:
            data["lexical_rank"] = self.lexical_rank
        if self.retrieval_score:
            data["retrieval_score"] = round(self.retrieval_score, 6)
        if self.score_breakdown:
            data["score_breakdown"] = {
                key: round(value, 6) for key, value in sorted(self.score_breakdown.items())
            }
        if self.query_term_matches:
            data["query_term_matches"] = {
                field_name: list(terms)
                for field_name, terms in sorted(self.query_term_matches.items())
                if terms
            }
        if self.graph_support:
            data["graph_support"] = self.graph_support
        if self.component_ids:
            data["component_ids"] = list(self.component_ids)
        return data


@dataclass(frozen=True)
class ContextAnchorResolution:
    status: ContextAnchorStatus
    code: ContextAnchorResolutionCode
    anchors: tuple[ContextGraphAnchorCandidate, ...] = ()
    candidates: tuple[ContextGraphAnchorCandidate, ...] = ()
    selection_coverage: dict[str, Any] = field(default_factory=dict)

    def to_dict(self) -> dict[str, Any]:
        data = {
            "status": self.status.value,
            "code": self.code.value,
            "anchors": [candidate.to_dict() for candidate in self.anchors],
            "candidates": [candidate.to_dict() for candidate in self.candidates],
        }
        if self.selection_coverage:
            data["selection_coverage"] = self.selection_coverage
        return data


@dataclass(frozen=True)
class ContextEvidenceProjection:
    """Evidence ranking plus bounded compact visibility, never edit authority."""

    ranked_source_paths: tuple[str, ...] = ()
    visible_source_paths: tuple[str, ...] = ()
    ranked_test_paths: tuple[str, ...] = ()
    visible_test_paths: tuple[str, ...] = ()
    prior_outcome_paths: tuple[str, ...] = ()

    def to_dict(self) -> dict[str, Any]:
        return {
            "ranked_source_paths": list(self.ranked_source_paths),
            "visible_source_paths": list(self.visible_source_paths),
            "ranked_test_paths": list(self.ranked_test_paths),
            "visible_test_paths": list(self.visible_test_paths),
            "prior_outcome_paths": list(self.prior_outcome_paths),
        }


@dataclass(frozen=True)
class ContextGraphSeedRef:
    anchor: GraphContextAnchor
    source_ref: ContextSourceRef
    provenance: ContextGraphAnchorProvenance
    anchor_strength: ContextAnchorStrength
    continuation: GraphContinuation

    @property
    def identity_digest(self) -> str:
        return digest_data(
            {
                "anchor": self.anchor.to_dict(),
                "source_ref": self.source_ref.to_dict(),
            }
        )

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.anchor.path,
            "source_ref": self.source_ref.to_dict(),
            "anchor": self.anchor.to_dict(),
            "provenance": self.provenance.value,
            "anchor_strength": self.anchor_strength.value,
            "continuation": self.continuation.to_dict(),
            "identity_digest": self.identity_digest,
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
    query_term_matches: dict[str, tuple[str, ...]] = field(default_factory=dict)
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
            "query_term_matches": {
                field_name: list(terms)
                for field_name, terms in sorted(self.query_term_matches.items())
                if terms
            },
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
    component_crossings: list[dict[str, Any]] = field(default_factory=list)
    graph_seed_refs: list[ContextGraphSeedRef] = field(default_factory=list)
    preselection_graph_support_by_path: dict[str, dict[str, Any]] = field(
        default_factory=dict,
        repr=False,
    )
    evidence_projection: ContextEvidenceProjection | None = field(
        default=None,
        repr=False,
    )
    schema: str = "repoctl.context.bundle"
    schema_version: int = 15
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
            "component_crossings": self.component_crossings,
            "component_crossing_count": len(self.component_crossings),
            "component_crossings_truncated": False,
            "graph_seed_refs": [seed.to_dict() for seed in self.graph_seed_refs],
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
            component_crossings=self.component_crossings,
            graph_seed_refs=self.graph_seed_refs,
            preselection_graph_support_by_path=self.preselection_graph_support_by_path,
            evidence_projection=self.evidence_projection,
            schema=self.schema,
            schema_version=self.schema_version,
            authoritative=self.authoritative,
            bundle_digest=digest_data(self.to_dict(include_digest=False)),
        )

def _knowledge_sort_key(item: dict[str, Any]) -> tuple[float, str]:
    record = item.get("record") if isinstance(item.get("record"), dict) else {}
    return (-float(item.get("score") or 0.0), str(record.get("id") or ""))

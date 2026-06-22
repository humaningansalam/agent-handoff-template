from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any

from .graph_model import digest_data


@dataclass(frozen=True)
class ContextSourceRef:
    kind: str
    path: str
    section: str = ""
    line_start: int = 0
    line_end: int = 0
    content_sha256: str = ""

    def key(self) -> tuple[str, str, str, int, int]:
        return (self.kind, self.path, self.section, self.line_start, self.line_end)

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "kind": self.kind,
            "path": self.path,
            "content_sha256": self.content_sha256,
        }
        if self.section:
            data["section"] = self.section
        if self.line_start:
            data["line_start"] = self.line_start
        if self.line_end:
            data["line_end"] = self.line_end
        return data


@dataclass(frozen=True)
class ContextCandidate:
    source_ref: ContextSourceRef
    text: str
    score: float
    score_breakdown: dict[str, float]
    selection_reasons: list[str] = field(default_factory=list)
    graph_path: list[dict[str, str]] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "source_ref": self.source_ref.to_dict(),
            "excerpt": self.text,
            "score": round(self.score, 6),
            "score_breakdown": {key: round(value, 6) for key, value in sorted(self.score_breakdown.items())},
            "selection_reasons": sorted(set(self.selection_reasons)),
            "graph_path": self.graph_path,
        }


@dataclass(frozen=True)
class ContextBundle:
    repository: dict[str, str]
    query: dict[str, Any]
    source_snapshots: dict[str, str]
    completeness: dict[str, Any]
    candidates: list[ContextCandidate]
    packed_context: list[ContextCandidate]
    budget: dict[str, int]
    schema: str = "repoctl.context.bundle"
    schema_version: int = 1
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
            "candidates": [candidate.to_dict() for candidate in sorted(self.candidates, key=_candidate_sort_key)],
            "packed_context": [candidate.to_dict() for candidate in sorted(self.packed_context, key=_candidate_sort_key)],
            "budget": self.budget,
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
            candidates=self.candidates,
            packed_context=self.packed_context,
            budget=self.budget,
            schema=self.schema,
            schema_version=self.schema_version,
            authoritative=self.authoritative,
            bundle_digest=digest_data(self.to_dict(include_digest=False)),
        )


def _candidate_sort_key(candidate: ContextCandidate) -> tuple[float, str, str, int]:
    ref = candidate.source_ref
    return (-candidate.score, ref.path, ref.section, ref.line_start)

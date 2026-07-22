from __future__ import annotations

import re
import sqlite3
from enum import StrEnum

from .context_chunks import DocumentChunk
from .context_model import (
    CONTEXT_ANCHOR_STRENGTH_PRIORITY,
    ContextAnchorStrength,
    ContextCandidate,
    ContextEvidenceKind,
    ContextSectionKind,
)
from .document_roles import (
    ORDINARY_RECALL_EXCLUDED_DOCUMENT_ROLES,
    SOURCE_EXCLUDED_DOCUMENT_ROLES,
    DocumentRole,
    source_document_role,
)
from .path_roles import is_test_path


TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+|[가-힣]+")
IDENTIFIER_PART_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+")
IDENTIFIER_SEGMENT_RE = re.compile(r"[A-Za-z0-9]+|[가-힣]+")
FTS_FIELD_WEIGHTS = (4.0, 3.0, 1.0)
STOPWORDS = {
    "a",
    "an",
    "and",
    "after",
    "are",
    "does",
    "file",
    "files",
    "for",
    "from",
    "function",
    "functions",
    "how",
    "if",
    "impact",
    "impacted",
    "is",
    "of",
    "the",
    "to",
    "what",
    "why",
    "change",
    "changed",
    "changes",
}


class ContextRetrievalLane(StrEnum):
    PRODUCT_SOURCE = "product_source"
    PRODUCT_TEST = "product_test"
    PRODUCT_DOCUMENT = "product_document"
    PROJECT_CANONICAL = "project_canonical"
    PROJECT_GOVERNANCE = "project_governance"
    PROJECT_PROCEDURE = "project_procedure"
    PROJECT_DOCUMENT = "project_document"
    TASK_HISTORY = "task_history"
    SUPPORTING = "supporting"


AUTO_RETRIEVAL_LANE_LIMITS = {
    ContextRetrievalLane.PRODUCT_SOURCE: 12,
    ContextRetrievalLane.PRODUCT_TEST: 3,
    ContextRetrievalLane.PRODUCT_DOCUMENT: 2,
    ContextRetrievalLane.PROJECT_CANONICAL: 2,
    ContextRetrievalLane.PROJECT_GOVERNANCE: 2,
    ContextRetrievalLane.PROJECT_PROCEDURE: 2,
    ContextRetrievalLane.PROJECT_DOCUMENT: 2,
    ContextRetrievalLane.TASK_HISTORY: 2,
    ContextRetrievalLane.SUPPORTING: 1,
}


def retrieve_context(
    query: str,
    chunks: list[DocumentChunk],
    *,
    limit: int = 20,
    repository_path: str = "",
) -> list[ContextCandidate]:
    return rank_context_chunks(query, chunks, limit=limit, repository_path=repository_path)


def retrieve_context_balanced(
    query: str,
    chunks: list[DocumentChunk],
    *,
    mode: str,
    repository_path: str,
    limit: int = 20,
) -> list[ContextCandidate]:
    ranked = rank_context_chunks(
        query,
        chunks,
        limit=-1,
        repository_path=repository_path,
    )
    if mode != "auto" or limit < 0:
        return ranked if limit < 0 else ranked[:limit]

    by_lane: dict[ContextRetrievalLane, list[ContextCandidate]] = {
        lane: [] for lane in ContextRetrievalLane
    }
    for candidate in ranked:
        lane = context_retrieval_lane(
            kind=candidate.source_ref.kind,
            path=candidate.source_ref.path,
            repository_path=repository_path,
            document_role=candidate.document_role,
        )
        by_lane[lane].append(candidate)

    selected: dict[tuple[str, str, str, str, int, int], ContextCandidate] = {}

    def add(candidate: ContextCandidate) -> None:
        if len(selected) < limit:
            selected.setdefault(candidate.source_ref.key(), candidate)

    lane_heads = [candidates[0] for candidates in by_lane.values() if candidates]
    for candidate in sorted(lane_heads, key=_retrieval_sort_key):
        add(candidate)
    if len(selected) >= limit:
        return sorted(selected.values(), key=_retrieval_sort_key)[:limit]

    for candidate in ranked:
        if candidate.anchor_strength == ContextAnchorStrength.EXACT:
            add(candidate)
    if len(selected) >= limit:
        return sorted(selected.values(), key=_retrieval_sort_key)[:limit]

    selected_per_lane = {
        lane: sum(
            1
            for candidate in selected.values()
            if context_retrieval_lane(
                kind=candidate.source_ref.kind,
                path=candidate.source_ref.path,
                repository_path=repository_path,
                document_role=candidate.document_role,
            )
            == lane
        )
        for lane in ContextRetrievalLane
    }
    for candidate in ranked:
        lane = context_retrieval_lane(
            kind=candidate.source_ref.kind,
            path=candidate.source_ref.path,
            repository_path=repository_path,
            document_role=candidate.document_role,
        )
        if selected_per_lane[lane] >= AUTO_RETRIEVAL_LANE_LIMITS[lane]:
            continue
        before = len(selected)
        add(candidate)
        if len(selected) > before:
            selected_per_lane[lane] += 1
        if len(selected) >= limit:
            break

    for candidate in ranked:
        if len(selected) >= limit:
            break
        add(candidate)
    return sorted(selected.values(), key=_retrieval_sort_key)


def context_retrieval_lane(
    *,
    kind: str,
    path: str,
    repository_path: str,
    document_role: DocumentRole = DocumentRole.UNSPECIFIED,
) -> ContextRetrievalLane:
    if kind in {"completion_receipt", "task_artifact"}:
        return ContextRetrievalLane.TASK_HISTORY

    normalized_repository = repository_path.replace("\\", "/").strip("/")
    normalized_path = path.replace("\\", "/").strip("/")
    role = source_document_role(
        kind=kind,
        path=normalized_path,
        repository_path=normalized_repository,
        assigned=document_role,
    )
    product_prefix = f"{normalized_repository}/" if normalized_repository else ""
    is_product_path = bool(product_prefix and normalized_path.startswith(product_prefix))
    if is_product_path:
        if kind == "verification_hint" or (
            kind in {"current_source", "config"}
            and is_test_path(normalized_path, repository_path=normalized_repository)
        ):
            return ContextRetrievalLane.PRODUCT_TEST
        if kind in {"current_source", "config"}:
            return ContextRetrievalLane.PRODUCT_SOURCE
        if kind == "product_manifest":
            return ContextRetrievalLane.PRODUCT_DOCUMENT

    if kind in {"document", "product_manifest", "verification_hint"}:
        if role in {DocumentRole.OPERATING_AUTHORITY, DocumentRole.PRODUCT_AUTHORITY}:
            return ContextRetrievalLane.PROJECT_CANONICAL
        if role == DocumentRole.GOVERNANCE_AUTHORITY:
            return ContextRetrievalLane.PROJECT_GOVERNANCE
        if role == DocumentRole.PROCEDURE:
            return ContextRetrievalLane.PROJECT_PROCEDURE
        if is_product_path and kind == "document" and role == DocumentRole.UNSPECIFIED:
            return ContextRetrievalLane.PRODUCT_DOCUMENT
        return ContextRetrievalLane.PROJECT_DOCUMENT
    return ContextRetrievalLane.SUPPORTING


def rank_context_chunks(
    query: str,
    chunks: list[DocumentChunk],
    *,
    limit: int = 20,
    repository_path: str = "",
) -> list[ContextCandidate]:
    terms = context_query_terms(query)
    ordered_terms = _ordered_query_terms(query)
    selectors = context_identity_selectors(query)
    fts_scores = _fts_scores(query, chunks)
    candidates: list[ContextCandidate] = []

    for chunk in chunks:
        document_role = source_document_role(
            kind=chunk.source_ref.kind,
            path=chunk.source_ref.path,
            repository_path=repository_path,
            assigned=chunk.document_role,
        )
        path_terms = set(canonical_identifier_sequence(chunk.source_ref.path))
        section_terms = set(canonical_identifier_sequence(chunk.source_ref.section))
        body_terms = set(canonical_identifier_sequence(chunk.text))
        path_coverage = _term_coverage(ordered_terms, path_terms)
        section_coverage = _term_coverage(ordered_terms, section_terms)
        body_coverage = _term_coverage(ordered_terms, body_terms)
        identity_kinds = context_identity_evidence(
            selectors,
            path=chunk.source_ref.path,
            section=chunk.source_ref.section,
            section_kind=chunk.source_ref.section_kind,
        )
        if document_role in SOURCE_EXCLUDED_DOCUMENT_ROLES:
            continue
        if (
            document_role in ORDINARY_RECALL_EXCLUDED_DOCUMENT_ROLES
            and not identity_kinds
        ):
            continue
        identity_score, _ = _identity_score_from_kinds(identity_kinds)
        fts_score = fts_scores.get(chunk.source_ref.key(), 0.0)
        authority_score = _authority_score(chunk, document_role=document_role)
        evidence_kinds = set(identity_kinds)
        reasons: list[str] = []

        if ContextEvidenceKind.EXACT_PATH in identity_kinds:
            reasons.append("exact path match")
        if ContextEvidenceKind.EXACT_FILENAME in identity_kinds:
            reasons.append("exact filename match")
        if ContextEvidenceKind.EXACT_SYMBOL in identity_kinds:
            reasons.append("exact provider symbol/section match")
        if path_coverage > 0:
            evidence_kinds.add(ContextEvidenceKind.PATH_TERMS)
            reasons.append("path term coverage")
        if section_coverage > 0:
            evidence_kinds.add(ContextEvidenceKind.SECTION_TERMS)
            reasons.append("section term coverage")
        if body_coverage > 0:
            evidence_kinds.add(ContextEvidenceKind.BODY_TERMS)
            reasons.append("body term coverage")
        if fts_score > 0:
            evidence_kinds.add(ContextEvidenceKind.FTS)
            reasons.append("SQLite FTS match")

        if not evidence_kinds:
            continue

        exact_coverage = max(path_coverage, section_coverage, body_coverage)
        score = (
            identity_score * 40.0
            + path_coverage * 8.0
            + section_coverage * 12.0
            + body_coverage * 2.0
            + fts_score
            + authority_score
            + _current_source_priority(chunk)
        )
        candidates.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=_excerpt(chunk.text, terms=terms),
                score=score,
                score_breakdown={
                    "identity": identity_score,
                    "path": path_coverage,
                    "section": section_coverage,
                    "body": body_coverage,
                    "exact": exact_coverage,
                    "fts": fts_score,
                    "authority": authority_score,
                },
                selection_reasons=sorted(set(reasons)),
                evidence_kinds=tuple(sorted(evidence_kinds, key=lambda kind: kind.value)),
                anchor_strength=_anchor_strength(
                    identity_kinds=identity_kinds,
                    section_kind=chunk.source_ref.section_kind,
                    section_coverage=section_coverage,
                ),
                document_role=document_role,
            )
        )

    ranked = sorted(candidates, key=_retrieval_sort_key)
    diverse = _distinct_paths_first(ranked)
    return diverse if limit < 0 else diverse[:limit]


def canonical_identifier_sequence(value: str) -> tuple[str, ...]:
    parts: list[str] = []
    for segment in IDENTIFIER_SEGMENT_RE.findall(value.replace("\\", "/")):
        if segment.isascii():
            identifier_parts = IDENTIFIER_PART_RE.findall(segment)
            parts.extend(part.casefold() for part in identifier_parts or [segment])
        else:
            parts.append(segment.casefold())
    return tuple(part for part in parts if part)


def context_identity_selectors(query: str) -> tuple[tuple[str, ...], ...]:
    selectors: list[tuple[str, ...]] = []
    stripped = query.strip().strip("`'\"")
    whole = tuple(part for part in canonical_identifier_sequence(stripped) if part not in STOPWORDS)
    if whole:
        selectors.append(whole)
    for token in TOKEN_RE.findall(query):
        selector = canonical_identifier_sequence(token.strip("`'\""))
        if selector:
            selectors.append(selector)
    return tuple(dict.fromkeys(sorted(selectors, key=lambda value: (-len(value), value))))


def context_identity_evidence(
    selectors: tuple[tuple[str, ...], ...],
    *,
    path: str,
    section: str,
    section_kind: ContextSectionKind,
) -> tuple[ContextEvidenceKind, ...]:
    path_identity = canonical_identifier_sequence(path.removeprefix("./"))
    filename_identity = canonical_identifier_sequence(path.replace("\\", "/").rsplit("/", 1)[-1])
    section_identity = canonical_identifier_sequence(section.strip())
    kinds: set[ContextEvidenceKind] = set()
    for selector in selectors:
        if selector == filename_identity:
            kinds.add(ContextEvidenceKind.EXACT_FILENAME)
        elif len(selector) > len(filename_identity) and _is_sequence_suffix(path_identity, selector):
            kinds.add(ContextEvidenceKind.EXACT_PATH)
        elif selector == path_identity:
            kinds.add(ContextEvidenceKind.EXACT_PATH)
        if (
            section_kind == ContextSectionKind.PROVIDER_SYMBOL
            and section_identity
            and selector == section_identity
        ):
            kinds.add(ContextEvidenceKind.EXACT_SYMBOL)
    return tuple(sorted(kinds, key=lambda kind: kind.value))


def _identity_score(query: str, chunk: DocumentChunk) -> tuple[float, str]:
    kinds = context_identity_evidence(
        context_identity_selectors(query),
        path=chunk.source_ref.path,
        section=chunk.source_ref.section,
        section_kind=chunk.source_ref.section_kind,
    )
    return _identity_score_from_kinds(kinds)


def _identity_score_from_kinds(kinds: tuple[ContextEvidenceKind, ...]) -> tuple[float, str]:
    if ContextEvidenceKind.EXACT_PATH in kinds:
        return 1.5, "exact path match"
    if ContextEvidenceKind.EXACT_SYMBOL in kinds:
        return 1.35, "exact symbol/section match"
    if ContextEvidenceKind.EXACT_FILENAME in kinds:
        return 1.2, "exact filename match"
    return 0.0, ""


def context_query_terms(query: str) -> set[str]:
    terms: set[str] = set()
    for token in TOKEN_RE.findall(query):
        raw = token.casefold().strip("`'\"")
        if len(raw) >= 2 and raw not in STOPWORDS:
            terms.add(raw)
        for part in canonical_identifier_sequence(token):
            if len(part) >= 2 and part not in STOPWORDS:
                terms.add(part)
    return terms


def _ordered_query_terms(query: str) -> tuple[str, ...]:
    ordered: list[str] = []
    for token in TOKEN_RE.findall(query):
        for part in canonical_identifier_sequence(token):
            if len(part) < 2 or part in STOPWORDS or part in ordered:
                continue
            ordered.append(part)
    return tuple(ordered)


def _term_coverage(query_terms: tuple[str, ...], field_terms: set[str]) -> float:
    if not query_terms:
        return 0.0
    return sum(1 for term in query_terms if term in field_terms) / len(query_terms)


def _is_sequence_suffix(value: tuple[str, ...], suffix: tuple[str, ...]) -> bool:
    return len(suffix) <= len(value) and value[-len(suffix) :] == suffix


def _anchor_strength(
    *,
    identity_kinds: tuple[ContextEvidenceKind, ...],
    section_kind: ContextSectionKind,
    section_coverage: float,
) -> ContextAnchorStrength:
    if identity_kinds:
        return ContextAnchorStrength.EXACT
    if section_kind == ContextSectionKind.PROVIDER_SYMBOL and section_coverage == 1.0:
        return ContextAnchorStrength.STRONG
    return ContextAnchorStrength.WEAK


def _distinct_paths_first(candidates: list[ContextCandidate]) -> list[ContextCandidate]:
    first_by_path: list[ContextCandidate] = []
    remaining: list[ContextCandidate] = []
    seen_paths: set[str] = set()
    for candidate in candidates:
        if candidate.source_ref.path in seen_paths:
            remaining.append(candidate)
            continue
        first_by_path.append(candidate)
        seen_paths.add(candidate.source_ref.path)
    return [*first_by_path, *remaining]


def _retrieval_sort_key(candidate: ContextCandidate) -> tuple[int, float, str, int]:
    return (
        -CONTEXT_ANCHOR_STRENGTH_PRIORITY[candidate.anchor_strength],
        -candidate.score,
        candidate.source_ref.path,
        candidate.source_ref.line_start,
    )


def _authority_score(
    chunk: DocumentChunk,
    *,
    document_role: DocumentRole,
) -> float:
    path = chunk.source_ref.path
    kind = chunk.source_ref.kind
    section = chunk.source_ref.section.lower()
    role = document_role
    if role == DocumentRole.PRODUCT_AUTHORITY:
        return 0.75
    if role == DocumentRole.OPERATING_AUTHORITY:
        return 0.7
    if role == DocumentRole.GOVERNANCE_AUTHORITY:
        if path.lower().startswith("docs/adr/") and section == "decision":
            return 0.5
        return 0.45
    if role == DocumentRole.PROCEDURE:
        return 0.35
    if role in ORDINARY_RECALL_EXCLUDED_DOCUMENT_ROLES | SOURCE_EXCLUDED_DOCUMENT_ROLES:
        return 0.0
    if kind == "completion_receipt":
        return 0.35
    if kind == "task_artifact":
        return 0.3
    if kind in {"current_source", "config"}:
        return 0.25
    if _is_product_doc_path(path) or kind == "product_manifest":
        return 0.3
    if role == DocumentRole.REFERENCE:
        return 0.2
    if kind.startswith("graph_"):
        return 0.05
    return 0.1


def _fts_scores(query: str, chunks: list[DocumentChunk]) -> dict[tuple[str, str, str, str, int, int], float]:
    if not query.strip() or not chunks:
        return {}
    connection = sqlite3.connect(":memory:")
    try:
        connection.execute("CREATE VIRTUAL TABLE chunks USING fts5(path, section, body)")
        connection.executemany(
            "INSERT INTO chunks(path, section, body) VALUES (?, ?, ?)",
            [(chunk.source_ref.path, chunk.source_ref.section, chunk.text) for chunk in chunks],
        )
        phrase = " OR ".join(_escape_fts(token) for token in sorted(context_query_terms(query)))
        if not phrase:
            return {}
        result: dict[tuple[str, str, str, str, int, int], float] = {}
        cursor = connection.execute(
            "SELECT rowid, bm25(chunks, ?, ?, ?) AS rank FROM chunks WHERE chunks MATCH ? ORDER BY rank, rowid",
            (*FTS_FIELD_WEIGHTS, phrase),
        )
        for rowid, rank in cursor.fetchall():
            chunk = chunks[int(rowid) - 1]
            result[chunk.source_ref.key()] = -float(rank)
        return result
    except sqlite3.Error:
        return {}
    finally:
        connection.close()


def _escape_fts(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def _current_source_priority(chunk: DocumentChunk) -> float:
    if chunk.source_ref.kind not in {"current_source", "config"}:
        return 0.0
    path = chunk.source_ref.path.lower()
    name = path.rsplit("/", 1)[-1]
    if name == "__init__.py":
        return -0.6
    if path.endswith(("_state.json", "-state.json")) or "/data/" in path and path.endswith(".json"):
        return -1.5
    if chunk.source_ref.kind == "config":
        return 0.25
    if name.startswith(".") or path.endswith((".lock", ".log")):
        return -0.6
    if is_test_path(path):
        return 0.35
    if path.endswith((".py", ".js", ".jsx", ".mjs", ".cjs", ".ts", ".tsx", ".mts", ".cts", ".dart", ".cs", ".java", ".kt", ".go", ".rs", ".sql", ".sh")):
        return 0.3
    return 0.0


def _is_product_doc_path(path: str) -> bool:
    lowered = path.lower()
    return lowered.startswith("repos/docs/") or lowered.startswith("repos/") and "/docs/" in lowered


def _excerpt(text: str, *, terms: set[str], limit: int = 900) -> str:
    lines = text.strip().splitlines()
    match_index = next(
        (
            index
            for index, line in enumerate(lines)
            if any(term.casefold() in line.casefold() for term in terms)
        ),
        0,
    )
    start = max(0, match_index - 2)
    end = min(len(lines), match_index + 5)
    compact = "\n".join(line.rstrip() for line in lines[start:end])
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."


def excerpt_for_query(text: str, query: str, *, limit: int = 900) -> str:
    return _excerpt(text, terms=context_query_terms(query), limit=limit)

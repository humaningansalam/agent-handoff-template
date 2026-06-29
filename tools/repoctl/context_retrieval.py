from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

from .context_chunks import DocumentChunk
from .context_graph import GraphIndex
from .context_model import ContextCandidate
from .graph_model import GraphSnapshot


TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+|[가-힣]+")
QUERY_SYNONYMS = {
    "authority": {"authoritative", "source"},
    "authorities": {"authority", "authoritative", "source"},
    "evidence": {"source", "provenance"},
    "non-authoritative": {"authority", "authoritative", "read-only", "derived"},
    "권위": {"authority", "authoritative", "source"},
    "비권위": {"non-authoritative", "authority", "authoritative"},
    "파생": {"derived", "snapshot"},
    "근거": {"evidence", "source"},
    "계약": {"contract", "invariant"},
    "결정": {"decision", "adr"},
    "검증": {"verification", "receipt"},
}
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


def retrieve_context(query: str, chunks: list[DocumentChunk], *, snapshot: GraphSnapshot | None = None, limit: int = 20) -> list[ContextCandidate]:
    terms = _terms(query)
    code_query = _is_code_query(terms)
    query_lower = query.lower()
    scores: dict[tuple[str, str, str, int, int], dict[str, float]] = defaultdict(lambda: {"exact": 0.0, "fts": 0.0, "authority": 0.0, "graph": 0.0})
    reasons: dict[tuple[str, str, str, int, int], set[str]] = defaultdict(set)
    by_key = {chunk.source_ref.key(): chunk for chunk in chunks}

    for chunk in chunks:
        key = chunk.source_ref.key()
        haystack = f"{chunk.source_ref.path} {chunk.source_ref.section} {chunk.text}".lower()
        exact_hits = sum(1 for term in terms if term.lower() in haystack)
        if exact_hits:
            exact_score = min(1.0, exact_hits / max(1, len(terms)))
            if chunk.source_ref.kind == "graph_node" and not code_query:
                exact_score *= 0.3
            if chunk.source_ref.kind != "graph_node" and code_query:
                exact_score *= 0.3
            scores[key]["exact"] = exact_score
            reasons[key].add("exact term/path/heading match")
        scores[key]["authority"] = _authority_score(chunk)

    for key, fts_score in _fts_scores(query, chunks).items():
        if by_key[key].source_ref.kind == "graph_node" and not code_query:
            fts_score *= 0.3
        if by_key[key].source_ref.kind != "graph_node" and code_query:
            fts_score *= 0.3
        scores[key]["fts"] = max(scores[key]["fts"], fts_score)
        reasons[key].add("SQLite FTS match")

    if snapshot is not None:
        graph_scores = _graph_scores(terms, chunks, snapshot, code_query=code_query, query_lower=query_lower)
        for key, graph_score in graph_scores.items():
            scores[key]["graph"] = max(scores[key]["graph"], graph_score)
            reasons[key].add("Graph evidence match")

    candidates: list[ContextCandidate] = []
    for key, breakdown in scores.items():
        graph_weight = 2.0 if code_query else 0.15
        chunk = by_key[key]
        product_boost = _product_evidence_boost(chunk, query_lower)
        if breakdown["exact"] <= 0 and breakdown["fts"] <= 0 and breakdown["graph"] <= 0 and product_boost <= 0:
            continue
        if product_boost > 0:
            reasons[key].add("product/recent evidence priority")
        score = (
            breakdown["exact"] * 2.0
            + breakdown["fts"] * 1.2
            + breakdown["authority"]
            + breakdown["graph"] * graph_weight
            + _code_node_kind_boost(chunk, query_lower, code_query=code_query)
            + product_boost
        )
        if score <= 0:
            continue
        candidates.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=_excerpt(chunk.text),
                score=score,
                score_breakdown=breakdown,
                selection_reasons=sorted(reasons[key]),
            )
        )
    return sorted(candidates, key=lambda item: (-item.score, item.source_ref.path, item.source_ref.line_start))[:limit]


def _terms(query: str) -> set[str]:
    terms = {token for token in TOKEN_RE.findall(query) if len(token) >= 2 and token.lower() not in STOPWORDS}
    expanded = set(terms)
    for term in terms:
        for needle, synonyms in QUERY_SYNONYMS.items():
            if needle in term:
                expanded.update(synonyms)
    return expanded


def _is_code_query(terms: set[str]) -> bool:
    code_action_terms = {"call", "calls", "called", "define", "defined", "defines", "import", "imports", "imported", "method", "symbol", "where"}
    for term in terms:
        if term.lower() in code_action_terms:
            return True
        if any(separator in term for separator in ("_", "/", "::")):
            return True
        if "." in term and not term.startswith("."):
            return True
        if _looks_like_code_identifier(term):
            return True
    return False


def _looks_like_code_identifier(term: str) -> bool:
    if not any(char.islower() for char in term) or not any(char.isupper() for char in term):
        return False
    return not (term[:1].isupper() and term[1:].islower())


def _authority_score(chunk: DocumentChunk) -> float:
    path = chunk.source_ref.path
    kind = chunk.source_ref.kind
    section = chunk.source_ref.section.lower()
    if path.startswith("docs/contracts/"):
        return 0.45
    if path.startswith("docs/adr/"):
        return 0.5 if section == "decision" else 0.4
    if kind == "completion_receipt":
        return 0.35
    if kind == "task_artifact":
        return 0.3
    if path == "AGENTS.md":
        return 0.45
    if path == "README.md":
        return 0.25
    if path.startswith("docs/workflows/"):
        return 0.2
    if kind.startswith("graph_"):
        return 0.05
    return 0.1


def _fts_scores(query: str, chunks: list[DocumentChunk]) -> dict[tuple[str, str, str, int, int], float]:
    if not query.strip() or not chunks:
        return {}
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE chunks USING fts5(path, section, body)")
        rows = [(chunk.source_ref.path, chunk.source_ref.section, chunk.text) for chunk in chunks]
        conn.executemany("INSERT INTO chunks(path, section, body) VALUES (?, ?, ?)", rows)
        phrase = " OR ".join(_escape_fts(token) for token in _terms(query))
        if not phrase:
            return {}
        result: dict[tuple[str, str, str, int, int], float] = {}
        cursor = conn.execute("SELECT rowid, bm25(chunks) AS rank FROM chunks WHERE chunks MATCH ? ORDER BY rank LIMIT 50", (phrase,))
        for rowid, rank in cursor.fetchall():
            chunk = chunks[int(rowid) - 1]
            score = 1.0 / (1.0 + abs(float(rank)))
            if len(TOKEN_RE.findall(chunk.text)) < 5:
                score *= 0.3
            result[chunk.source_ref.key()] = score
        return result
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _escape_fts(token: str) -> str:
    return '"' + token.replace('"', '""') + '"'


def _graph_scores(terms: set[str], chunks: list[DocumentChunk], snapshot: GraphSnapshot, *, code_query: bool, query_lower: str) -> dict[tuple[str, str, str, int, int], float]:
    index = GraphIndex(snapshot)
    matches = index.exact_matches(terms)
    if not matches:
        return {}
    seed_ids = {match.node_id for match in matches[:20]}
    neighbor_ids, _edges = index.neighborhood(seed_ids, depth=1)
    impact_scores: dict[str, float] = {}
    if code_query:
        impact_scores = _caller_impact_scores(index, seed_ids, include_symbols=any(term in query_lower for term in ("call", "function", "impact")))
    result: dict[tuple[str, str, str, int, int], float] = {}
    graph_chunks = [chunk for chunk in chunks if chunk.source_ref.kind == "graph_node"]
    for chunk in graph_chunks:
        path = chunk.source_ref.path
        for node_id in neighbor_ids:
            if path == f"<graph:{node_id}>":
                if code_query:
                    result[chunk.source_ref.key()] = max(impact_scores.get(node_id, 0.0), 1.0 if node_id in seed_ids else 0.45)
                else:
                    result[chunk.source_ref.key()] = 0.25 if node_id in seed_ids else 0.1
                break
    return result


def _caller_impact_scores(index: GraphIndex, seed_ids: set[str], *, include_symbols: bool) -> dict[str, float]:
    scores: dict[str, float] = {}
    for edge in index.snapshot.edges:
        if edge.kind != "CALLS" or edge.to_id not in seed_ids:
            continue
        if include_symbols:
            scores[edge.from_id] = max(scores.get(edge.from_id, 0.0), 2.2)
        for defining_edge in index.incoming.get(edge.from_id, []):
            if defining_edge.kind == "DEFINES":
                scores[defining_edge.from_id] = max(scores.get(defining_edge.from_id, 0.0), 2.0)
    return scores


def _code_node_kind_boost(chunk: DocumentChunk, query_lower: str, *, code_query: bool) -> float:
    if not code_query or chunk.source_ref.kind != "graph_node":
        return 0.0
    section = chunk.source_ref.section
    if "function" in query_lower and section.startswith("symbol "):
        return 0.4
    if "file" in query_lower and section.startswith("file "):
        return 0.4
    return 0.0


def _product_evidence_boost(chunk: DocumentChunk, query_lower: str) -> float:
    path = chunk.source_ref.path
    kind = chunk.source_ref.kind
    product_query = any(term in query_lower for term in ("project", "product", "architecture", "current", "recent", "최근", "현재", "프로젝트", "구조", "결정"))
    decision_query = any(term in query_lower for term in ("decision", "decisions", "why", "invariant", "failure", "receipt", "결정", "불변식", "장애", "실패", "검증"))
    if not product_query and not decision_query:
        return 0.0
    if path == "docs/PRD.md":
        return 0.9
    if path in {"README.md", "docs/README.md"}:
        return 0.55
    if kind == "completion_receipt":
        return 0.8 if decision_query else 0.45
    if kind == "task_artifact":
        return 0.65 if decision_query else 0.35
    if path.startswith("docs/contracts/") and product_query and not decision_query:
        return -0.2
    return 0.0


def _excerpt(text: str, *, limit: int = 900) -> str:
    compact = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."

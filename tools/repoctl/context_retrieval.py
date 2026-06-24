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
            scores[key]["exact"] = exact_score
            reasons[key].add("exact term/path/heading match")
        scores[key]["authority"] = _authority_score(chunk)

    for key, fts_score in _fts_scores(query, chunks).items():
        if by_key[key].source_ref.kind == "graph_node" and not code_query:
            fts_score *= 0.3
        scores[key]["fts"] = max(scores[key]["fts"], fts_score)
        reasons[key].add("SQLite FTS match")

    if snapshot is not None:
        graph_scores = _graph_scores(terms, chunks, snapshot, code_query=code_query)
        for key, graph_score in graph_scores.items():
            scores[key]["graph"] = max(scores[key]["graph"], graph_score)
            reasons[key].add("Graph evidence match")

    candidates: list[ContextCandidate] = []
    for key, breakdown in scores.items():
        if breakdown["exact"] <= 0 and breakdown["fts"] <= 0 and breakdown["graph"] <= 0:
            continue
        graph_weight = 2.0 if code_query else 0.15
        score = breakdown["exact"] * 2.0 + breakdown["fts"] * 1.2 + breakdown["authority"] + breakdown["graph"] * graph_weight
        if score <= 0:
            continue
        chunk = by_key[key]
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


def _graph_scores(terms: set[str], chunks: list[DocumentChunk], snapshot: GraphSnapshot, *, code_query: bool) -> dict[tuple[str, str, str, int, int], float]:
    index = GraphIndex(snapshot)
    matches = index.exact_matches(terms)
    if not matches:
        return {}
    seed_ids = {match.node_id for match in matches[:20]}
    neighbor_ids, _edges = index.neighborhood(seed_ids, depth=1)
    result: dict[tuple[str, str, str, int, int], float] = {}
    graph_chunks = [chunk for chunk in chunks if chunk.source_ref.kind == "graph_node"]
    for chunk in graph_chunks:
        path = chunk.source_ref.path
        for node_id in neighbor_ids:
            if path == f"<graph:{node_id}>":
                if code_query:
                    result[chunk.source_ref.key()] = 1.0 if node_id in seed_ids else 0.45
                else:
                    result[chunk.source_ref.key()] = 0.25 if node_id in seed_ids else 0.1
                break
    return result


def _excerpt(text: str, *, limit: int = 900) -> str:
    compact = "\n".join(line.rstrip() for line in text.strip().splitlines())
    if len(compact) <= limit:
        return compact
    return compact[: limit - 3].rstrip() + "..."

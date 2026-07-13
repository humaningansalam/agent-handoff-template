from __future__ import annotations

import re
import sqlite3
from collections import defaultdict

from .context_chunks import DocumentChunk
from .context_model import ContextCandidate


TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+|[가-힣]+")
IDENTIFIER_PART_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+")
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


def retrieve_context(query: str, chunks: list[DocumentChunk], *, limit: int = 20) -> list[ContextCandidate]:
    terms = _terms(query)
    scores: dict[tuple[str, str, str, int, int], dict[str, float]] = defaultdict(lambda: {"exact": 0.0, "fts": 0.0, "authority": 0.0})
    reasons: dict[tuple[str, str, str, int, int], set[str]] = defaultdict(set)
    by_key = {chunk.source_ref.key(): chunk for chunk in chunks}

    for chunk in chunks:
        key = chunk.source_ref.key()
        haystack = f"{chunk.source_ref.path} {chunk.source_ref.section} {chunk.text}".lower()
        exact_hits = sum(1 for term in terms if term.lower() in haystack)
        if exact_hits:
            scores[key]["exact"] = min(1.0, exact_hits / max(1, len(terms)))
            reasons[key].add("exact term/path/heading match")
        scores[key]["authority"] = _authority_score(chunk)

    for key, fts_score in _fts_scores(query, chunks).items():
        scores[key]["fts"] = max(scores[key]["fts"], fts_score)
        reasons[key].add("SQLite FTS match")

    candidates: list[ContextCandidate] = []
    for key, breakdown in scores.items():
        chunk = by_key[key]
        if breakdown["exact"] <= 0 and breakdown["fts"] <= 0:
            continue
        score = (
            breakdown["exact"] * 2.0
            + breakdown["fts"] * 1.2
            + breakdown["authority"]
            + _current_source_priority(chunk)
        )
        if score <= 0:
            continue
        candidates.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=_excerpt(chunk.text, terms=terms),
                score=score,
                score_breakdown=breakdown,
                selection_reasons=sorted(reasons[key]),
            )
        )
    return sorted(candidates, key=lambda item: (-item.score, item.source_ref.path, item.source_ref.line_start))[:limit]


def _terms(query: str) -> set[str]:
    terms: set[str] = set()
    for token in TOKEN_RE.findall(query):
        for part in _identifier_terms(token):
            if len(part) >= 2 and part.lower() not in STOPWORDS:
                terms.add(part)
    return terms


def _identifier_terms(token: str) -> set[str]:
    values = {token}
    for segment in re.split(r"[./:_-]+", token):
        if not segment:
            continue
        values.add(segment)
        values.update(IDENTIFIER_PART_RE.findall(segment))
    return values


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
    if kind == "current_source":
        return 0.25
    if path in {"AGENTS.md", "docs/PRD.md"}:
        return 0.45
    if path == "README.md":
        return 0.25
    if path.startswith("docs/workflows/"):
        return 0.2
    if _is_product_doc_path(path) or kind == "product_manifest":
        return 0.3
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
        for position, (rowid, _rank) in enumerate(cursor.fetchall(), start=1):
            chunk = chunks[int(rowid) - 1]
            score = 1.0 / position
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


def _current_source_priority(chunk: DocumentChunk) -> float:
    if chunk.source_ref.kind != "current_source":
        return 0.0
    path = chunk.source_ref.path.lower()
    name = path.rsplit("/", 1)[-1]
    if name == "__init__.py":
        return -0.6
    if path.endswith(("_state.json", "-state.json")) or "/data/" in path and path.endswith(".json"):
        return -1.5
    if name.startswith(".") or path.endswith((".lock", ".log")):
        return -0.6
    if _looks_like_test_path(path):
        return 0.35
    if path.endswith((".py", ".js", ".jsx", ".ts", ".tsx", ".dart", ".cs", ".java", ".kt", ".go", ".rs", ".sql", ".sh")):
        return 0.3
    return 0.0


def _looks_like_test_path(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        "/tests/" in path
        or "/test/" in path
        or path.startswith("tests/")
        or path.startswith("test/")
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".test.ts", "_test.dart"))
    )


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
    return _excerpt(text, terms=_terms(query), limit=limit)

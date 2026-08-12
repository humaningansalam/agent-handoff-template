from __future__ import annotations

import json
import sqlite3
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any

from .code_index import CodeIndexEntry
from .context_chunks import DocumentChunk, chunk_markdown_file, chunk_markdown_text, chunk_text_source, sha256_text
from .context_model import CONTEXT_SOURCE_KIND_VALUES, LEXICAL_CONTEXT_SOURCE_KIND_VALUES, ContextSectionKind, ContextSourceKind, ContextSourceRef
from .context_retrieval import (
    AUTO_RETRIEVAL_LANE_LIMITS,
    FTS_FIELD_WEIGHTS,
    ContextIdentityQuery,
    ContextRetrievalLane,
    context_identity_evidence,
    context_identity_selectors,
    context_query_terms,
    context_retrieval_lane,
)
from .context_sources import MAX_CONTEXT_SOURCE_BYTES, context_document_paths, context_product_manifest_paths, context_source_kind
from .document_roles import (
    ORDINARY_RECALL_EXCLUDED_DOCUMENT_ROLES,
    SOURCE_EXCLUDED_DOCUMENT_ROLES,
    source_document_role,
)
from .graph_model import GraphSnapshot, digest_data
from .language_profiles import collect_verification_hints
from .repositories import RepoTarget
from .tasks import CompletionReceiptCollection, Problem


EVIDENCE_INDEX_SCHEMA = "repoctl.evidence.index"
EVIDENCE_INDEX_SCHEMA_VERSION = 7
STATIC_KINDS = {"document", "product_manifest", "verification_hint", "completion_receipt", "task_artifact"}


class EvidenceRetrievalChannel(StrEnum):
    FTS = "fts"
    EXACT_IDENTITY = "exact_identity"


@dataclass(frozen=True)
class _SourceChunkRange:
    section: str
    line_start: int
    line_end: int
    section_kind: ContextSectionKind
    source_fact_id: str = ""
    provider: str = ""
    provider_symbol_id: str = ""


def _evidence_index_schema_is_current(metadata: dict[str, Any]) -> bool:
    return (
        metadata.get("schema") == EVIDENCE_INDEX_SCHEMA
        and type(metadata.get("schema_version")) is int
        and metadata["schema_version"] == EVIDENCE_INDEX_SCHEMA_VERSION
    )


def _database_path(root: Path, target: RepoTarget, database_path: Path | None = None) -> Path:
    return database_path or root / ".repoctl-state/graph" / target.id / "evidence.sqlite3"


def _path_label(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _connect(path: Path, *, read_only: bool = False) -> sqlite3.Connection:
    if read_only:
        connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    else:
        path.parent.mkdir(parents=True, exist_ok=True)
        connection = sqlite3.connect(path)
    connection.row_factory = sqlite3.Row
    return connection


def _initialize(connection: sqlite3.Connection) -> None:
    connection.executescript(
        """
        PRAGMA journal_mode=DELETE;
        PRAGMA synchronous=FULL;
        CREATE TABLE IF NOT EXISTS metadata (
            key TEXT PRIMARY KEY,
            value TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS chunks (
            id INTEGER PRIMARY KEY,
            source_key TEXT NOT NULL UNIQUE,
            kind TEXT NOT NULL,
            path TEXT NOT NULL,
            section TEXT NOT NULL,
            section_kind TEXT NOT NULL,
            line_start INTEGER NOT NULL,
            line_end INTEGER NOT NULL,
            source_fact_id TEXT NOT NULL,
            provider TEXT NOT NULL,
            provider_symbol_id TEXT NOT NULL,
            content_sha256 TEXT NOT NULL,
            body TEXT NOT NULL,
            title TEXT NOT NULL,
            file_fingerprint TEXT NOT NULL
        );
        CREATE INDEX IF NOT EXISTS chunks_path_idx ON chunks(path, kind);
        CREATE INDEX IF NOT EXISTS chunks_kind_idx ON chunks(kind, path);
        CREATE VIRTUAL TABLE IF NOT EXISTS chunks_fts USING fts5(
            path,
            section,
            body,
            content='chunks',
            content_rowid='id'
        );
        CREATE TRIGGER IF NOT EXISTS chunks_ai AFTER INSERT ON chunks BEGIN
            INSERT INTO chunks_fts(rowid, path, section, body)
            VALUES (new.id, new.path, new.section, new.body);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_ad AFTER DELETE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, path, section, body)
            VALUES ('delete', old.id, old.path, old.section, old.body);
        END;
        CREATE TRIGGER IF NOT EXISTS chunks_au AFTER UPDATE ON chunks BEGIN
            INSERT INTO chunks_fts(chunks_fts, rowid, path, section, body)
            VALUES ('delete', old.id, old.path, old.section, old.body);
            INSERT INTO chunks_fts(rowid, path, section, body)
            VALUES (new.id, new.path, new.section, new.body);
        END;
        """
    )


def _metadata(connection: sqlite3.Connection) -> dict[str, Any]:
    values: dict[str, Any] = {}
    try:
        rows = connection.execute("SELECT key, value FROM metadata").fetchall()
    except sqlite3.Error:
        return values
    for row in rows:
        try:
            values[str(row["key"])] = json.loads(str(row["value"]))
        except json.JSONDecodeError:
            values[str(row["key"])] = str(row["value"])
    return values


def _write_metadata(connection: sqlite3.Connection, values: dict[str, Any]) -> None:
    connection.execute("DELETE FROM metadata")
    connection.executemany(
        "INSERT INTO metadata(key, value) VALUES (?, ?)",
        [
            (key, json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":")))
            for key, value in sorted(values.items())
        ],
    )


def _source_key(chunk: DocumentChunk) -> str:
    return digest_data(
        {
            "kind": chunk.source_ref.kind,
            "path": chunk.source_ref.path,
            "section": chunk.source_ref.section,
            "section_kind": chunk.source_ref.section_kind.value,
            "line_start": chunk.source_ref.line_start,
            "line_end": chunk.source_ref.line_end,
            "source_fact_id": chunk.source_ref.source_fact_id,
            "provider": chunk.source_ref.provider,
            "provider_symbol_id": chunk.source_ref.provider_symbol_id,
        }
    )


def _insert_chunks(connection: sqlite3.Connection, chunks: list[DocumentChunk], *, file_fingerprint: str = "") -> None:
    connection.executemany(
        """
        INSERT INTO chunks(
            source_key, kind, path, section, section_kind, line_start, line_end,
            source_fact_id, provider, provider_symbol_id, content_sha256, body,
            title, file_fingerprint
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        ON CONFLICT(source_key) DO UPDATE SET
            kind=excluded.kind,
            path=excluded.path,
            section=excluded.section,
            section_kind=excluded.section_kind,
            line_start=excluded.line_start,
            line_end=excluded.line_end,
            source_fact_id=excluded.source_fact_id,
            provider=excluded.provider,
            provider_symbol_id=excluded.provider_symbol_id,
            content_sha256=excluded.content_sha256,
            body=excluded.body,
            title=excluded.title,
            file_fingerprint=excluded.file_fingerprint
        """,
        [
            (
                _source_key(chunk),
                chunk.source_ref.kind,
                chunk.source_ref.path,
                chunk.source_ref.section,
                chunk.source_ref.section_kind.value,
                chunk.source_ref.line_start,
                chunk.source_ref.line_end,
                chunk.source_ref.source_fact_id,
                chunk.source_ref.provider,
                chunk.source_ref.provider_symbol_id,
                chunk.source_ref.content_sha256,
                chunk.text,
                chunk.title,
                file_fingerprint,
            )
            for chunk in chunks
        ],
    )


def _static_chunks(
    root: Path,
    *,
    target: RepoTarget,
    receipt_collection: CompletionReceiptCollection,
) -> tuple[list[DocumentChunk], set[str], list[Problem]]:
    chunks: list[DocumentChunk] = []
    static_paths: set[str] = set()
    problems: list[Problem] = []
    for path in context_document_paths(root, target=target):
        rel = path.relative_to(root).as_posix()
        static_paths.add(rel)
        try:
            chunks.extend(chunk_markdown_file(root, path))
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem("warning", "context_source_unreadable", str(exc), rel))
    for path in context_product_manifest_paths(root, target=target):
        rel = path.relative_to(root).as_posix()
        static_paths.add(rel)
        try:
            chunks.append(
                chunk_text_source(
                    root,
                    rel,
                    path.read_text(encoding="utf-8"),
                    kind="product_manifest",
                    section=path.name,
                    section_kind=ContextSectionKind.CONFIG,
                )
            )
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem("warning", "context_manifest_unreadable", str(exc), rel))
    for hint in collect_verification_hints(target.root_path):
        path = target.root_path / hint.source_path
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        text = f"Verification command: {hint.command}\nSource: {rel}\nReason: {hint.reason}\nProvider: {hint.provider}"
        chunks.append(
            chunk_text_source(
                root,
                rel,
                text,
                kind="verification_hint",
                section=f"verification: {hint.command}",
                section_kind=ContextSectionKind.VERIFICATION,
            )
        )
    receipt_artifacts = receipt_collection.artifacts
    receipt_problems = receipt_collection.problems
    problems.extend(Problem("warning", problem.code, problem.message, problem.path) for problem in receipt_problems)
    for receipt_artifact in receipt_artifacts:
        receipt = receipt_artifact.receipt
        task_id = str(receipt.get("task_id") or "")
        chunks.append(
            chunk_text_source(
                root,
                receipt_artifact.receipt_path,
                receipt_artifact.receipt_text,
                kind="completion_receipt",
                section=task_id or "completion receipt",
                section_kind=ContextSectionKind.TASK,
            )
        )
        chunks.extend(
            chunk_markdown_text(
                root,
                receipt_artifact.resolved_path,
                receipt_artifact.artifact_text,
                kind="task_artifact",
            )
        )
    return chunks, static_paths, problems


def _source_ranges(snapshot: GraphSnapshot, path: str) -> list[_SourceChunkRange]:
    nodes = {node.id: node for node in snapshot.nodes}
    symbol_ids = {
        edge.to_id
        for edge in snapshot.edges
        if edge.kind == "DEFINES"
        and str(nodes.get(edge.from_id).identity.get("path") if nodes.get(edge.from_id) is not None else "") == path
    }
    anchor_ids = {
        edge.from_id: edge.to_id
        for edge in snapshot.edges
        if edge.kind == "ANCHORS" and edge.from_id in symbol_ids
    }
    ranges: list[_SourceChunkRange] = []
    for symbol_id in sorted(symbol_ids):
        symbol = nodes.get(symbol_id)
        anchor = nodes.get(anchor_ids.get(symbol_id, ""))
        if symbol is None or anchor is None:
            continue
        provider_facts = symbol.facts.get("provider") if isinstance(symbol.facts.get("provider"), dict) else {}
        if provider_facts.get("kind") == "module":
            continue
        section = str(provider_facts.get("qualified_name") or provider_facts.get("name") or "symbol")
        provider = str(symbol.identity.get("provider") or "")
        provider_symbol_id = str(symbol.identity.get("provider_symbol_id") or "")
        try:
            start = int(anchor.identity.get("start_line") or 0)
            end = int(anchor.identity.get("end_line") or 0)
        except (TypeError, ValueError):
            continue
        if start > 0 and end >= start and provider and provider_symbol_id:
            ranges.append(
                _SourceChunkRange(
                    section,
                    start,
                    end,
                    ContextSectionKind.PROVIDER_SYMBOL,
                    provider=provider,
                    provider_symbol_id=provider_symbol_id,
                )
            )
    file_node = next(
        (
            node
            for node in snapshot.nodes
            if node.kind == "file" and str(node.identity.get("path") or "") == path
        ),
        None,
    )
    rpc = file_node.facts.get("rpc") if file_node is not None and isinstance(file_node.facts.get("rpc"), dict) else {}
    source_facts = rpc.get("source_facts") if isinstance(rpc.get("source_facts"), list) else []
    for fact in source_facts:
        if not isinstance(fact, dict):
            continue
        routine = fact.get("routine") if isinstance(fact.get("routine"), dict) else {}
        anchor = fact.get("anchor") if isinstance(fact.get("anchor"), dict) else {}
        source_fact_id = str(fact.get("fact_id") or "")
        section = str(routine.get("value") or "") if routine.get("status") == "known" else ""
        try:
            start = int(anchor.get("start_line") or 0)
            end = int(anchor.get("end_line") or 0)
        except (TypeError, ValueError):
            continue
        if source_fact_id and section and start > 0 and end >= start:
            ranges.append(
                _SourceChunkRange(
                    section,
                    start,
                    end,
                    ContextSectionKind.PROVIDER_RELATIONSHIP,
                    source_fact_id,
                )
            )
    return sorted(
        set(ranges),
        key=lambda item: (
            item.line_start,
            item.line_end,
            item.section,
            item.section_kind.value,
            item.source_fact_id,
        ),
    )


def _source_chunks(root: Path, *, entry: CodeIndexEntry, snapshot: GraphSnapshot) -> tuple[list[DocumentChunk], Problem | None]:
    kind = context_source_kind(entry.path, entry.classification)
    if not kind:
        return [], None
    path = root / entry.workspace_path
    try:
        if path.stat().st_size > MAX_CONTEXT_SOURCE_BYTES:
            return [], Problem(
                "warning",
                "context_source_too_large",
                f"{kind.value} exceeds {MAX_CONTEXT_SOURCE_BYTES} byte indexing limit",
                entry.workspace_path,
            )
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return [], None
    except OSError as exc:
        return [], Problem("warning", "context_source_unreadable", str(exc), entry.workspace_path)
    if not text.strip():
        return [], None
    if kind == ContextSourceKind.CONFIG:
        return [
            chunk_text_source(
                root,
                entry.workspace_path,
                text,
                kind="config",
                section=entry.path,
                section_kind=ContextSectionKind.CONFIG,
            )
        ], None
    if kind == ContextSourceKind.STRUCTURED_DATA:
        return [
            chunk_text_source(
                root,
                entry.workspace_path,
                text,
                kind=ContextSourceKind.STRUCTURED_DATA.value,
                section=entry.path,
                section_kind=ContextSectionKind.STRUCTURED_DATA,
            )
        ], None
    lines = text.splitlines()
    digest = sha256_text(text)
    ranges = _source_ranges(snapshot, entry.path)
    if not ranges:
        return [
            chunk_text_source(
                root,
                entry.workspace_path,
                text,
                kind="current_source",
                section=entry.path,
                section_kind=ContextSectionKind.FILE,
            )
        ], None

    chunks: list[DocumentChunk] = []
    covered = [False] * len(lines)
    for source_range in ranges:
        bounded_start = max(1, source_range.line_start)
        bounded_end = min(len(lines), source_range.line_end)
        if bounded_end < bounded_start:
            continue
        for index in range(bounded_start - 1, bounded_end):
            covered[index] = True
        body = "\n".join(lines[bounded_start - 1 : bounded_end]).strip()
        if not body:
            continue
        chunks.append(
            DocumentChunk(
                source_ref=ContextSourceRef(
                    kind="current_source",
                    path=entry.workspace_path,
                    section=source_range.section,
                    section_kind=source_range.section_kind,
                    line_start=bounded_start,
                    line_end=bounded_end,
                    source_fact_id=source_range.source_fact_id,
                    provider=source_range.provider,
                    provider_symbol_id=source_range.provider_symbol_id,
                    content_sha256=digest,
                ),
                text=body,
                title=source_range.section,
            )
        )
    module_body = "\n".join(line for index, line in enumerate(lines) if not covered[index]).strip()
    if module_body:
        chunks.append(
            DocumentChunk(
                source_ref=ContextSourceRef(
                    kind="current_source",
                    path=entry.workspace_path,
                    section=f"{entry.path} module",
                    section_kind=ContextSectionKind.FILE,
                    line_start=1,
                    line_end=max(1, len(lines)),
                    content_sha256=digest,
                ),
                text=module_body,
                title=f"{entry.path} module",
            )
        )
    return chunks, None


def _source_manifest(connection: sqlite3.Connection, kinds: set[str]) -> str:
    placeholders = ",".join("?" for _ in kinds)
    rows = connection.execute(
        f"SELECT kind, path, section, section_kind, line_start, line_end, source_fact_id, provider, provider_symbol_id, content_sha256 FROM chunks WHERE kind IN ({placeholders}) ORDER BY kind, path, section, section_kind, line_start, line_end, source_fact_id, provider, provider_symbol_id",
        tuple(sorted(kinds)),
    ).fetchall()
    return digest_data([dict(row) for row in rows])


def _source_path_counts(connection: sqlite3.Connection) -> dict[str, int]:
    return {
        str(row["kind"]): int(row["count"])
        for row in connection.execute(
            "SELECT kind, COUNT(DISTINCT path) AS count FROM chunks GROUP BY kind ORDER BY kind"
        )
    }


def materialize_evidence_index(
    root: Path,
    *,
    target: RepoTarget,
    snapshot: GraphSnapshot,
    entries: list[CodeIndexEntry],
    file_fingerprints: dict[str, str],
    changed_paths: set[str],
    graph_input_digest: str,
    receipt_collection: CompletionReceiptCollection,
    rebuild: bool = False,
    allow_reset: bool = False,
    database_path: Path | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    path = _database_path(root, target, database_path)
    if path.is_file():
        try:
            connection = _connect(path)
            current_meta = _metadata(connection)
            connection.close()
        except sqlite3.Error as exc:
            if not allow_reset:
                return {}, [Problem("error", "evidence_index_unavailable", str(exc), _path_label(root, path))]
            current_meta = {}
        if not _evidence_index_schema_is_current(current_meta):
            if not allow_reset:
                return {}, [
                    Problem(
                        "error",
                        "evidence_index_schema_invalid",
                        "materialized evidence index schema is invalid; run repoctl graph build --rebuild",
                        _path_label(root, path),
                    )
                ]
            path.unlink(missing_ok=True)
            rebuild = True
    else:
        rebuild = True

    connection = _connect(path)
    problems: list[Problem] = []
    try:
        _initialize(connection)
        with connection:
            if rebuild:
                connection.execute("DELETE FROM chunks")
            static_chunks, static_paths, static_problems = _static_chunks(
                root,
                target=target,
                receipt_collection=receipt_collection,
            )
            problems.extend(static_problems)
            placeholders = ",".join("?" for _ in STATIC_KINDS)
            connection.execute(f"DELETE FROM chunks WHERE kind IN ({placeholders})", tuple(sorted(STATIC_KINDS)))
            _insert_chunks(connection, static_chunks)

            entries_by_path = {entry.path: entry for entry in entries}
            update_paths = set(entries_by_path) if rebuild else set(changed_paths)
            source_kind_values = tuple(sorted(CONTEXT_SOURCE_KIND_VALUES))
            source_kind_placeholders = ",".join("?" for _ in source_kind_values)
            for repo_path in sorted(update_paths):
                workspace_path = f"{target.display_path.rstrip('/')}/{repo_path}"
                connection.execute(
                    f"DELETE FROM chunks WHERE path = ? AND kind IN ({source_kind_placeholders})",
                    (workspace_path, *source_kind_values),
                )
                entry = entries_by_path.get(repo_path)
                if (
                    entry is None
                    or not context_source_kind(entry.path, entry.classification)
                    or workspace_path in static_paths
                ):
                    continue
                chunks, problem = _source_chunks(root, entry=entry, snapshot=snapshot)
                if problem is not None:
                    problems.append(problem)
                _insert_chunks(connection, chunks, file_fingerprint=file_fingerprints.get(repo_path, ""))

            counts = {
                str(row["kind"]): int(row["count"])
                for row in connection.execute("SELECT kind, COUNT(*) AS count FROM chunks GROUP BY kind ORDER BY kind")
            }
            source_path_counts = _source_path_counts(connection)
            metadata = {
                "schema": EVIDENCE_INDEX_SCHEMA,
                "schema_version": EVIDENCE_INDEX_SCHEMA_VERSION,
                "repository": target.to_dict(),
                "graph_input_digest": graph_input_digest,
                "snapshot_digest": snapshot.snapshot_digest,
                "chunk_counts": counts,
                "source_path_counts": source_path_counts,
                "document_manifest_digest": _source_manifest(connection, {"document", "product_manifest", "verification_hint"}),
                "receipt_manifest_digest": _source_manifest(connection, {"completion_receipt", "task_artifact"}),
                "current_source_manifest_digest": _source_manifest(connection, CONTEXT_SOURCE_KIND_VALUES),
                "problems": [
                    problem.to_dict()
                    for problem in problems
                    if problem.code != "invalid_completion_receipt"
                ],
            }
            _write_metadata(connection, metadata)
        return {
            "path": _path_label(root, path),
            "status": "rebuilt" if rebuild else "updated",
            "updated_paths": sorted(changed_paths),
            **metadata,
        }, problems
    except sqlite3.Error as exc:
        return {}, [*problems, Problem("error", "evidence_index_materialization_failed", str(exc), _path_label(root, path))]
    finally:
        connection.close()


def load_evidence_index_metadata(
    root: Path,
    *,
    target: RepoTarget,
    database_path: Path | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    path = _database_path(root, target, database_path)
    if not path.is_file():
        return {}, [Problem("error", "evidence_index_missing", "materialized evidence index is missing; run repoctl graph build --rebuild", _path_label(root, path))]
    try:
        connection = _connect(path, read_only=True)
        metadata = _metadata(connection)
        connection.close()
    except sqlite3.Error as exc:
        return {}, [Problem("error", "evidence_index_unavailable", str(exc), _path_label(root, path))]
    if not _evidence_index_schema_is_current(metadata):
        return {}, [Problem("error", "evidence_index_schema_invalid", "materialized evidence index schema is invalid; run repoctl graph build --rebuild", _path_label(root, path))]
    return metadata, []


def evidence_index_binding_problems(
    root: Path,
    *,
    target: RepoTarget,
    metadata: dict[str, Any],
    snapshot_digest: str,
    graph_input_digest: str,
    database_path: Path | None = None,
) -> list[Problem]:
    path = _database_path(root, target, database_path)
    if str(metadata.get("snapshot_digest") or "") != snapshot_digest:
        return [
            Problem(
                "error",
                "evidence_index_snapshot_mismatch",
                "materialized evidence index and Graph snapshot do not match; run repoctl graph build --rebuild",
                _path_label(root, path),
            )
        ]
    if str(metadata.get("graph_input_digest") or "") != graph_input_digest:
        return [
            Problem(
                "error",
                "evidence_index_input_mismatch",
                "materialized evidence index and Graph input state do not match; run repoctl graph build --rebuild",
                _path_label(root, path),
            )
        ]
    return []


def _row_chunk(
    row: sqlite3.Row,
    *,
    repository_path: str,
    corpus_fts_rank: int = 0,
) -> DocumentChunk:
    kind = str(row["kind"])
    path = str(row["path"])
    row_keys = set(row.keys())
    corpus_fts_score = (
        -float(row["rank"])
        if "rank" in row_keys and row["rank"] is not None
        else None
    )
    return DocumentChunk(
        source_ref=ContextSourceRef(
            kind=kind,
            path=path,
            section=str(row["section"]),
            section_kind=ContextSectionKind(str(row["section_kind"])),
            line_start=int(row["line_start"]),
            line_end=int(row["line_end"]),
            source_fact_id=str(row["source_fact_id"]),
            provider=str(row["provider"]),
            provider_symbol_id=str(row["provider_symbol_id"]),
            content_sha256=str(row["content_sha256"]),
        ),
        text=str(row["body"]),
        title=str(row["title"]),
        document_role=source_document_role(
            kind=kind,
            path=path,
            repository_path=repository_path,
        ),
        corpus_fts_score=corpus_fts_score,
        corpus_fts_rank=corpus_fts_rank if corpus_fts_score is not None else 0,
    )


def _retrieval_filter(
    mode: str,
    target: RepoTarget,
    *,
    channel: EvidenceRetrievalChannel,
    include_history: bool,
) -> tuple[str, list[Any]]:
    prefix = f"{target.display_path.rstrip('/')}/%"
    source_kinds = (
        LEXICAL_CONTEXT_SOURCE_KIND_VALUES
        if channel is EvidenceRetrievalChannel.FTS
        else CONTEXT_SOURCE_KIND_VALUES
    )
    if mode in {"code_location", "call_impact", "file_impact"}:
        allowed_kinds = (*sorted(source_kinds), "product_manifest", "verification_hint")
        placeholders = ",".join("?" for _ in allowed_kinds)
        return f"c.path LIKE ? AND c.kind IN ({placeholders})", [prefix, *allowed_kinds]
    if include_history and mode in {"auto", "past_decision", "failure_mode"}:
        base_sql, base_params = "1 = 1", []
    else:
        base_sql, base_params = "c.kind NOT IN ('completion_receipt', 'task_artifact')", []
    if channel is EvidenceRetrievalChannel.EXACT_IDENTITY:
        return base_sql, base_params
    exact_only_kinds = sorted(CONTEXT_SOURCE_KIND_VALUES - LEXICAL_CONTEXT_SOURCE_KIND_VALUES)
    placeholders = ",".join("?" for _ in exact_only_kinds)
    return f"({base_sql}) AND c.kind NOT IN ({placeholders})", [*base_params, *exact_only_kinds]


def query_evidence_index(
    root: Path,
    *,
    target: RepoTarget,
    query: str,
    mode: str,
    snapshot_digest: str = "",
    graph_input_digest: str = "",
    limit: int = 24,
    database_path: Path | None = None,
    overlay_chunks: list[DocumentChunk] | None = None,
    replaced_paths: set[str] | None = None,
    include_history: bool = True,
) -> tuple[list[DocumentChunk], dict[str, Any], list[Problem]]:
    metadata, problems = load_evidence_index_metadata(root, target=target, database_path=database_path)
    if problems:
        return [], metadata, problems
    if snapshot_digest or graph_input_digest:
        problems = evidence_index_binding_problems(
            root,
            target=target,
            metadata=metadata,
            snapshot_digest=snapshot_digest,
            graph_input_digest=graph_input_digest,
            database_path=database_path,
        )
        if problems:
            return [], metadata, problems
    terms = sorted(context_query_terms(query))
    selectors = context_identity_selectors(query)
    if not terms and not selectors:
        return [], metadata, []
    path = _database_path(root, target, database_path)
    connection = _connect(path, read_only=True)
    effective_connection: sqlite3.Connection | None = None
    try:
        query_connection = connection
        replacement_paths = {str(value) for value in (replaced_paths or set()) if str(value)}
        current_overlays = list(overlay_chunks or [])
        if replacement_paths or current_overlays:
            effective_connection = sqlite3.connect(":memory:")
            effective_connection.row_factory = sqlite3.Row
            connection.backup(effective_connection)
            with effective_connection:
                if replacement_paths:
                    placeholders = ",".join("?" for _value in replacement_paths)
                    effective_connection.execute(
                        f"DELETE FROM chunks WHERE path IN ({placeholders})",
                        tuple(sorted(replacement_paths)),
                    )
                _insert_chunks(effective_connection, current_overlays)
            query_connection = effective_connection
        if not isinstance(metadata.get("source_path_counts"), dict) or effective_connection is not None:
            metadata = {**metadata, "source_path_counts": _source_path_counts(query_connection)}
        fts_filter_sql, fts_filter_params = _retrieval_filter(
            mode,
            target,
            channel=EvidenceRetrievalChannel.FTS,
            include_history=include_history,
        )
        exact_filter_sql, exact_filter_params = _retrieval_filter(
            mode,
            target,
            channel=EvidenceRetrievalChannel.EXACT_IDENTITY,
            include_history=include_history,
        )
        fts_rows = _path_diverse_fts_rows(
            query_connection,
            terms=terms,
            filter_sql=fts_filter_sql,
            filter_params=fts_filter_params,
            limit=limit,
            mode=mode,
            repository_path=target.display_path,
        )
        identity_rows = _identity_rows(
            query_connection,
            selectors=selectors,
            filter_sql=exact_filter_sql,
            filter_params=exact_filter_params,
            limit=limit,
        )
        rows: dict[int, sqlite3.Row] = {int(row["id"]): row for row in identity_rows}
        fts_ranks: dict[int, int] = {}
        for rank, row in enumerate(fts_rows, start=1):
            row_id = int(row["id"])
            rows[row_id] = row
            fts_ranks[row_id] = rank
        return [
            _row_chunk(
                row,
                repository_path=target.display_path,
                corpus_fts_rank=fts_ranks.get(row_id, 0),
            )
            for row_id, row in sorted(rows.items())
        ], metadata, []
    except (sqlite3.Error, ValueError) as exc:
        return [], metadata, [Problem("error", "evidence_index_query_failed", str(exc), _path_label(root, path))]
    finally:
        if effective_connection is not None:
            effective_connection.close()
        connection.close()


def _path_diverse_fts_rows(
    connection: sqlite3.Connection,
    *,
    terms: list[str],
    filter_sql: str,
    filter_params: list[Any],
    limit: int,
    mode: str,
    repository_path: str,
) -> list[sqlite3.Row]:
    if not terms:
        return []
    phrase = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
    weights = ", ".join(str(weight) for weight in FTS_FIELD_WEIGHTS)
    cursor = connection.execute(
        f"""
        SELECT c.*, bm25(chunks_fts, {weights}) AS rank
        FROM chunks_fts
        JOIN chunks AS c ON c.id = chunks_fts.rowid
        WHERE chunks_fts MATCH ? AND {filter_sql}
        ORDER BY rank, c.path, c.line_start, c.id
        """,
        [phrase, *filter_params],
    )
    if mode == "auto":
        return _lane_balanced_fts_rows(cursor, repository_path=repository_path)

    path_limit = max(24, max(1, limit) * 2)
    chunks_per_path = 4
    path_counts: dict[str, int] = {}
    rows: list[sqlite3.Row] = []
    for row in cursor:
        document_role = source_document_role(
            kind=str(row["kind"]),
            path=str(row["path"]),
            repository_path=repository_path,
        )
        if document_role in ORDINARY_RECALL_EXCLUDED_DOCUMENT_ROLES | SOURCE_EXCLUDED_DOCUMENT_ROLES:
            continue
        row_path = str(row["path"])
        path_counts.setdefault(row_path, 0)
        if path_counts[row_path] < chunks_per_path:
            rows.append(row)
            path_counts[row_path] += 1
        if len(path_counts) >= path_limit:
            break
    return rows


def _lane_balanced_fts_rows(
    cursor: sqlite3.Cursor,
    *,
    repository_path: str,
) -> list[sqlite3.Row]:
    lane_path_limits = {
        lane: max(2, lane_limit * 2)
        for lane, lane_limit in AUTO_RETRIEVAL_LANE_LIMITS.items()
    }
    lane_paths: dict[ContextRetrievalLane, set[str]] = {
        lane: set() for lane in ContextRetrievalLane
    }
    chunks_per_path = 4
    path_counts: dict[tuple[ContextRetrievalLane, str], int] = {}
    rows: list[sqlite3.Row] = []
    for row in cursor:
        document_role = source_document_role(
            kind=str(row["kind"]),
            path=str(row["path"]),
            repository_path=repository_path,
        )
        if document_role in ORDINARY_RECALL_EXCLUDED_DOCUMENT_ROLES | SOURCE_EXCLUDED_DOCUMENT_ROLES:
            continue
        lane = context_retrieval_lane(
            kind=str(row["kind"]),
            path=str(row["path"]),
            repository_path=repository_path,
            document_role=document_role,
        )
        row_path = str(row["path"])
        known_paths = lane_paths[lane]
        if row_path not in known_paths and len(known_paths) >= lane_path_limits[lane]:
            continue
        known_paths.add(row_path)
        key = (lane, row_path)
        path_counts.setdefault(key, 0)
        if path_counts[key] < chunks_per_path:
            rows.append(row)
            path_counts[key] += 1
    return rows


def _identity_rows(
    connection: sqlite3.Connection,
    *,
    selectors: ContextIdentityQuery,
    filter_sql: str,
    filter_params: list[Any],
    limit: int,
) -> list[sqlite3.Row]:
    if not selectors.explicit_selectors and not selectors.ordered_natural_terms:
        return []
    identity_limit = max(64, max(1, limit) * 4)
    matching_ids: list[int] = []
    cursor = connection.execute(
        f"SELECT c.id, c.path, c.section, c.section_kind FROM chunks AS c WHERE {filter_sql} ORDER BY c.path, c.line_start, c.id",
        filter_params,
    )
    for row in cursor:
        section_kind = ContextSectionKind(str(row["section_kind"]))
        if context_identity_evidence(
            selectors,
            path=str(row["path"]),
            section=str(row["section"]),
            section_kind=section_kind,
        ):
            matching_ids.append(int(row["id"]))
            if len(matching_ids) >= identity_limit:
                break
    if not matching_ids:
        return []
    placeholders = ",".join("?" for _ in matching_ids)
    return connection.execute(
        f"SELECT c.* FROM chunks AS c WHERE c.id IN ({placeholders}) ORDER BY c.path, c.line_start, c.id",
        matching_ids,
    ).fetchall()


def evidence_chunks_for_paths(
    root: Path,
    *,
    target: RepoTarget,
    workspace_paths: set[str],
    kinds: set[str] | None = None,
    database_path: Path | None = None,
) -> tuple[list[DocumentChunk], list[Problem]]:
    if not workspace_paths:
        return [], []
    metadata, problems = load_evidence_index_metadata(root, target=target, database_path=database_path)
    if problems:
        return [], problems
    del metadata
    path = _database_path(root, target, database_path)
    connection = _connect(path, read_only=True)
    try:
        path_placeholders = ",".join("?" for _ in workspace_paths)
        params: list[Any] = [*sorted(workspace_paths)]
        kind_sql = ""
        if kinds:
            kind_placeholders = ",".join("?" for _ in kinds)
            kind_sql = f" AND kind IN ({kind_placeholders})"
            params.extend(sorted(kinds))
        rows = connection.execute(
            f"SELECT * FROM chunks WHERE path IN ({path_placeholders}){kind_sql} ORDER BY path, line_start, section",
            params,
        ).fetchall()
        return [
            _row_chunk(row, repository_path=target.display_path)
            for row in rows
        ], []
    except sqlite3.Error as exc:
        return [], [Problem("error", "evidence_index_query_failed", str(exc), _path_label(root, path))]
    finally:
        connection.close()

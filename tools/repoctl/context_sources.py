from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context_chunks import DocumentChunk, chunk_markdown_file, chunk_text_source
from .graph_model import GraphSnapshot, digest_data
from .repositories import RepoTarget
from .tasks import Problem, collect_completion_receipts


DOCUMENT_PATTERNS = (
    "AGENTS.md",
    "README.md",
    "docs/PRD.md",
    "docs/adr/*.md",
    "docs/contracts/*.md",
    "docs/workflows/*.md",
)

EXCLUDED_PARTS = {".repoctl-state", "generated"}


def collect_context_sources(
    root: Path,
    *,
    target: RepoTarget,
    snapshot: GraphSnapshot | None,
    graph_problems: list[Problem],
    graph_meta: dict[str, Any],
) -> tuple[list[DocumentChunk], dict[str, str], dict[str, Any], list[Problem]]:
    chunks: list[DocumentChunk] = []
    problems: list[Problem] = []
    document_paths = _document_paths(root)
    for path in document_paths:
        try:
            chunks.extend(chunk_markdown_file(root, path))
        except OSError as exc:
            problems.append(Problem("error", "context_source_unreadable", str(exc), path.relative_to(root).as_posix()))

    receipts, receipt_problems = collect_completion_receipts(root, repo_id=target.id)
    problems.extend(receipt_problems)
    for receipt in receipts:
        rel = f"docs/tasks/.repoctl-state/completions/{receipt.get('task_id', '')}.json"
        text = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)
        chunks.append(chunk_text_source(root, rel, text, kind="completion_receipt", section=str(receipt.get("task_id") or "completion receipt")))
        for artifact in _receipt_artifacts(receipt):
            artifact_path = root / artifact
            if artifact_path.is_file():
                chunks.extend(chunk_markdown_file(root, artifact_path, kind="task_artifact"))

    problems.extend(graph_problems)
    if snapshot is None:
        completeness = {
            "documents_checked": len(document_paths),
            "receipts_checked": len(receipts),
            "graph_available": False,
            "graph_meta": graph_meta,
        }
        return chunks, {"document_manifest_digest": _manifest_digest(chunks), "receipt_manifest_digest": digest_data(receipts)}, completeness, problems

    graph_chunks = _graph_chunks(root, snapshot.to_dict())
    chunks.extend(graph_chunks)
    completeness = {
        "documents_checked": len(document_paths),
        "receipts_checked": len(receipts),
        "graph_available": True,
        "graph_completeness": snapshot.completeness,
    }
    source_snapshots = {
        "document_manifest_digest": _manifest_digest([chunk for chunk in chunks if chunk.source_ref.kind == "document"]),
        "receipt_manifest_digest": digest_data(receipts),
        "graph_digest": snapshot.snapshot_digest,
    }
    return chunks, source_snapshots, completeness, problems


def _document_paths(root: Path) -> list[Path]:
    paths: set[Path] = set()
    for pattern in DOCUMENT_PATTERNS:
        paths.update(path for path in root.glob(pattern) if path.is_file())
    return sorted(path for path in paths if not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts))


def _receipt_artifacts(receipt: dict[str, Any]) -> list[str]:
    values: list[str] = []
    for key in ("task_path", "archive_path"):
        value = str(receipt.get(key) or "")
        if value:
            values.append(value)
    verification = receipt.get("verification")
    if isinstance(verification, dict):
        for key in ("task_path", "archive_path"):
            value = str(verification.get(key) or "")
            if value:
                values.append(value)
    return sorted(set(values))


def _graph_chunks(root: Path, snapshot: dict[str, Any]) -> list[DocumentChunk]:
    chunks: list[DocumentChunk] = []
    for node in snapshot.get("nodes", []):
        if not isinstance(node, dict):
            continue
        identity = node.get("identity") if isinstance(node.get("identity"), dict) else {}
        label = str(identity.get("path") or identity.get("topic") or identity.get("task_id") or identity.get("provider_symbol_id") or node.get("id") or "")
        text = json.dumps(node, ensure_ascii=False, sort_keys=True)
        chunks.append(chunk_text_source(root, f"<graph:{node.get('id', '')}>", text, kind="graph_node", section=f"{node.get('kind', 'node')} {label}"))
    return chunks


def _manifest_digest(chunks: list[DocumentChunk]) -> str:
    return digest_data([chunk.source_ref.to_dict() for chunk in sorted(chunks, key=lambda item: item.source_ref.key())])

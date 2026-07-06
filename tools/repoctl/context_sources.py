from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context_chunks import DocumentChunk, chunk_markdown_file, chunk_text_source
from .graph_model import GraphSnapshot, digest_data
from .language_profiles import collect_verification_hints, product_manifest_patterns
from .repositories import RepoTarget
from .tasks import Problem, collect_completion_receipts


DOCUMENT_PATTERNS = (
    "AGENTS.md",
    "README.md",
    "docs/BOARD.md",
    "docs/PRD.md",
    "docs/adr/*.md",
    "docs/contracts/*.md",
    "docs/workflows/*.md",
)
PRODUCT_DOCUMENT_PATTERNS = (
    "README.md",
    "README.*.md",
    "docs/README.md",
    "docs/PRD.md",
    "docs/*.md",
)
EXCLUDED_PARTS = {".repoctl-state", "generated", ".next", ".nuxt", ".svelte-kit", ".turbo", ".firebase", ".dart_tool", "Library", "Temp", "Obj", "obj", "Build", "Builds", "Logs", "UserSettings", "node_modules", "dist", "build", "target"}


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
    document_paths = _document_paths(root, target=target)
    for path in document_paths:
        try:
            chunks.extend(chunk_markdown_file(root, path))
        except OSError as exc:
            problems.append(Problem("error", "context_source_unreadable", str(exc), path.relative_to(root).as_posix()))
    manifest_paths = _product_manifest_paths(root, target=target)
    for path in manifest_paths:
        try:
            rel = path.relative_to(root).as_posix()
            chunks.append(chunk_text_source(root, rel, path.read_text(encoding="utf-8"), kind="product_manifest", section=path.name))
        except UnicodeDecodeError as exc:
            problems.append(Problem("warning", "context_manifest_non_utf8", str(exc), path.relative_to(root).as_posix()))
        except OSError as exc:
            problems.append(Problem("error", "context_manifest_unreadable", str(exc), path.relative_to(root).as_posix()))

    for hint in collect_verification_hints(target.root_path):
        source_path = target.root_path / hint.source_path
        if not source_path.exists():
            continue
        rel = source_path.relative_to(root).as_posix()
        text = f"Verification command: {hint.command}\nSource: {rel}\nReason: {hint.reason}\nProvider: {hint.provider}"
        chunks.append(chunk_text_source(root, rel, text, kind="verification_hint", section=f"verification: {hint.command}"))

    receipts, receipt_problems = collect_completion_receipts(root, repo_id=target.id)
    receipt_warnings = [
        Problem(
            "warning",
            "context_completion_receipt_invalid",
            f"{problem.message}; receipt excluded from this Context bundle",
            problem.path,
        )
        for problem in receipt_problems
    ]
    problems.extend(receipt_warnings)
    for receipt in receipts:
        rel = f"docs/tasks/.repoctl-state/completions/{receipt.get('task_id', '')}.json"
        text = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)
        chunks.append(chunk_text_source(root, rel, text, kind="completion_receipt", section=str(receipt.get("task_id") or "completion receipt")))
        for artifact in _receipt_artifacts(receipt):
            artifact_path = root / artifact
            if artifact_path.is_file():
                chunks.extend(chunk_markdown_file(root, artifact_path, kind="task_artifact"))

    graph_context_problems = _context_graph_problems(graph_problems)
    problems.extend(graph_context_problems)
    if snapshot is None:
        completeness = {
            "documents_checked": len(document_paths),
            "manifests_checked": len(manifest_paths),
            "receipts_checked": len(receipts),
            "receipt_problem_count": len(receipt_warnings),
            "receipt_problem_paths": sorted(problem.path or "" for problem in receipt_warnings if problem.path),
            "graph_available": False,
            "graph_meta": graph_meta,
        }
        return chunks, {
            "document_manifest_digest": _manifest_digest([chunk for chunk in chunks if chunk.source_ref.kind in {"document", "product_manifest", "verification_hint"}]),
            "receipt_manifest_digest": digest_data(receipts),
        }, completeness, problems

    graph_chunks = _graph_chunks(root, snapshot.to_dict())
    chunks.extend(graph_chunks)
    completeness = {
        "documents_checked": len(document_paths),
        "manifests_checked": len(manifest_paths),
        "receipts_checked": len(receipts),
        "receipt_problem_count": len(receipt_warnings),
        "receipt_problem_paths": sorted(problem.path or "" for problem in receipt_warnings if problem.path),
        "graph_available": True,
        "graph_meta": graph_meta,
        "graph_completeness": snapshot.completeness,
    }
    source_snapshots = {
        "document_manifest_digest": _manifest_digest([chunk for chunk in chunks if chunk.source_ref.kind in {"document", "product_manifest", "verification_hint"}]),
        "receipt_manifest_digest": digest_data(receipts),
        "graph_digest": snapshot.snapshot_digest,
    }
    return chunks, source_snapshots, completeness, problems


def _document_paths(root: Path, *, target: RepoTarget) -> list[Path]:
    paths: set[Path] = set()
    for pattern in DOCUMENT_PATTERNS:
        paths.update(path for path in root.glob(pattern) if path.is_file())
    for pattern in PRODUCT_DOCUMENT_PATTERNS:
        paths.update(path for path in target.root_path.glob(pattern) if path.is_file())
    return sorted(path for path in paths if not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts))


def _product_manifest_paths(root: Path, *, target: RepoTarget) -> list[Path]:
    paths: set[Path] = set()
    for pattern in product_manifest_patterns():
        paths.update(path for path in target.root_path.glob(pattern) if path.is_file())
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


def _context_graph_problems(graph_problems: list[Problem]) -> list[Problem]:
    mapped: list[Problem] = []
    for problem in graph_problems:
        if problem.code == "invalid_completion_receipt":
            mapped.append(
                Problem(
                    "warning",
                    "context_graph_completion_receipt_invalid",
                    f"{problem.message}; graph task receipt evidence is incomplete for this Context bundle",
                    problem.path,
                )
            )
        else:
            mapped.append(problem)
    return mapped


def _manifest_digest(chunks: list[DocumentChunk]) -> str:
    return digest_data([chunk.source_ref.to_dict() for chunk in sorted(chunks, key=lambda item: item.source_ref.key())])

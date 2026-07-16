from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context_chunks import DocumentChunk, chunk_markdown_file, chunk_text_source
from .git import normalize_repo_path
from .graph_model import GraphSnapshot, digest_data
from .language_profiles import collect_verification_hints, is_semantic_source_language, language_for_path, product_manifest_patterns
from .meta import meta_inventory
from .repositories import RepoTarget
from .tasks import Problem, collect_completion_receipts, completion_receipt_artifact_path


DOCUMENT_PATTERNS = (
    "AGENTS.md",
    "README.md",
    "docs/BOARD.md",
    "docs/PRD.md",
    "docs/prd/**/*.md",
    "docs/adr/**/*.md",
    "docs/contracts/**/*.md",
    "docs/workflows/**/*.md",
)
PRODUCT_DOCUMENT_PATTERNS = (
    "README.md",
    "README.*.md",
    "docs/**/*.md",
)
EXCLUDED_PARTS = {".repoctl-state", "generated", ".next", ".nuxt", ".svelte-kit", ".turbo", ".firebase", ".dart_tool", "Library", "Temp", "Obj", "obj", "Build", "Builds", "Logs", "UserSettings", "node_modules", "dist", "build", "target"}
MAX_CONTEXT_SOURCE_BYTES = 1024 * 1024


def current_source_eligible(repo_path: str, classification: str) -> bool:
    return (
        bool(repo_path)
        and "\\" not in repo_path
        and normalize_repo_path(repo_path) == repo_path
        and classification != "excluded"
        and is_semantic_source_language(language_for_path(repo_path))
    )


def collect_context_sources(
    root: Path,
    *,
    target: RepoTarget,
    snapshot: GraphSnapshot | None,
    graph_problems: list[Problem],
    graph_meta: dict[str, Any],
    include_history: bool = False,
) -> tuple[list[DocumentChunk], dict[str, str], dict[str, Any], list[Problem]]:
    chunks: list[DocumentChunk] = []
    problems: list[Problem] = []
    document_paths = context_document_paths(root, target=target)
    for path in document_paths:
        try:
            chunks.extend(chunk_markdown_file(root, path))
        except OSError as exc:
            problems.append(Problem("error", "context_source_unreadable", str(exc), path.relative_to(root).as_posix()))
    manifest_paths = context_product_manifest_paths(root, target=target)
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

    receipts: list[dict[str, Any]] = []
    receipt_warnings: list[Problem] = []
    if include_history:
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
        for receipt in receipts:
            rel = f"docs/tasks/.repoctl-state/completions/{receipt.get('task_id', '')}.json"
            text = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)
            chunks.append(chunk_text_source(root, rel, text, kind="completion_receipt", section=str(receipt.get("task_id") or "completion receipt")))
            artifact = completion_receipt_artifact_path(root, receipt)
            if artifact:
                artifact_path = root / artifact
                if artifact_path.is_file():
                    chunks.extend(chunk_markdown_file(root, artifact_path, kind="task_artifact"))

    invalid_receipt_problems = [problem for problem in graph_problems if problem.code == "invalid_completion_receipt"]
    if snapshot is not None:
        invalid_receipt_problems.extend(
            Problem(
                str(raw.get("severity") or "warning"),
                str(raw.get("code") or "invalid_completion_receipt"),
                str(raw.get("message") or "completion receipt is invalid"),
                str(raw.get("path") or ""),
            )
            for raw in snapshot.completeness.get("receipt_problems", [])
            if isinstance(raw, dict)
        )
    reported_receipt_problems = invalid_receipt_problems or receipt_warnings
    receipt_problem_paths = sorted(problem.path or "" for problem in reported_receipt_problems if problem.path)

    graph_context_problems = context_graph_problems(
        [problem for problem in graph_problems if problem.code != "invalid_completion_receipt"]
        + invalid_receipt_problems
    )
    problems.extend(graph_context_problems)
    if snapshot is None:
        inventory, inventory_problems, _inventory_meta = meta_inventory(root, changed=False, target=target)
        current_source_chunks, current_source_problems = _current_source_chunks_from_records(
            root,
            records=[
                (item.workspace_path, item.path, item.classification)
                for item in inventory
            ],
            existing_paths={chunk.source_ref.path for chunk in chunks},
        )
        chunks.extend(current_source_chunks)
        problems.extend(inventory_problems)
        problems.extend(current_source_problems)
        completeness = {
            "documents_checked": len(document_paths),
            "manifests_checked": len(manifest_paths),
            "receipts_checked": len(receipts),
            "history_loaded": include_history,
            "receipt_problem_count": len(reported_receipt_problems),
            "receipt_problem_paths": receipt_problem_paths,
            "graph_available": False,
            "graph_meta": graph_meta,
            "current_sources_checked": len(current_source_chunks),
        }
        return chunks, {
            "document_manifest_digest": _manifest_digest([chunk for chunk in chunks if chunk.source_ref.kind in {"document", "product_manifest", "verification_hint"}]),
            "receipt_manifest_digest": digest_data(receipts),
            "current_source_manifest_digest": _manifest_digest(current_source_chunks),
        }, completeness, problems

    current_source_chunks, current_source_problems = _current_source_chunks(root, snapshot=snapshot, existing_paths={chunk.source_ref.path for chunk in chunks})
    chunks.extend(current_source_chunks)
    problems.extend(current_source_problems)
    completeness = {
        "documents_checked": len(document_paths),
        "manifests_checked": len(manifest_paths),
        "receipts_checked": len(receipts),
        "current_sources_checked": len(current_source_chunks),
        "history_loaded": include_history,
        "receipt_problem_count": len(reported_receipt_problems),
        "receipt_problem_paths": receipt_problem_paths,
        "graph_available": True,
        "graph_meta": graph_meta,
        "graph_completeness": snapshot.completeness,
    }
    source_snapshots = {
        "document_manifest_digest": _manifest_digest([chunk for chunk in chunks if chunk.source_ref.kind in {"document", "product_manifest", "verification_hint"}]),
        "receipt_manifest_digest": digest_data(receipts),
        "current_source_manifest_digest": _manifest_digest(current_source_chunks),
        "graph_digest": snapshot.snapshot_digest,
    }
    return chunks, source_snapshots, completeness, problems


def _current_source_chunks(
    root: Path,
    *,
    snapshot: GraphSnapshot,
    existing_paths: set[str],
) -> tuple[list[DocumentChunk], list[Problem]]:
    records: list[tuple[str, str, str]] = []
    for node in snapshot.nodes:
        if node.kind != "file":
            continue
        identity = node.identity if isinstance(node.identity, dict) else {}
        facts = node.facts if isinstance(node.facts, dict) else {}
        index = facts.get("index") if isinstance(facts.get("index"), dict) else {}
        workspace_path = str(identity.get("workspace_path") or "")
        repo_path = str(identity.get("path") or "")
        records.append((workspace_path, repo_path, str(index.get("classification") or "")))
    return _current_source_chunks_from_records(root, records=records, existing_paths=existing_paths)


def _current_source_chunks_from_records(
    root: Path,
    *,
    records: list[tuple[str, str, str]],
    existing_paths: set[str],
) -> tuple[list[DocumentChunk], list[Problem]]:
    chunks: list[DocumentChunk] = []
    problems: list[Problem] = []
    for workspace_path, repo_path, classification in records:
        if not current_source_eligible(repo_path, classification):
            continue
        if not workspace_path or workspace_path in existing_paths:
            continue
        path = root / workspace_path
        try:
            if not path.is_file():
                continue
            if path.stat().st_size > MAX_CONTEXT_SOURCE_BYTES:
                problems.append(
                    Problem(
                        "warning",
                        "context_current_source_too_large",
                        f"current source exceeds {MAX_CONTEXT_SOURCE_BYTES} byte indexing limit",
                        workspace_path,
                    )
                )
                continue
            text = path.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            continue
        except OSError as exc:
            problems.append(Problem("warning", "context_current_source_unreadable", str(exc), workspace_path))
            continue
        if not text.strip():
            continue
        chunks.append(chunk_text_source(root, workspace_path, text, kind="current_source", section=repo_path or path.name))
    return chunks, problems


def current_source_chunks_for_paths(
    root: Path,
    *,
    target: RepoTarget,
    repo_paths: set[str],
) -> tuple[list[DocumentChunk], list[Problem]]:
    records: list[tuple[str, str, str]] = []
    problems: list[Problem] = []
    for raw_path in sorted(repo_paths):
        repo_path = normalize_repo_path(raw_path)
        if not repo_path or repo_path != raw_path:
            problems.append(
                Problem(
                    "warning",
                    "context_changed_path_invalid",
                    "changed source path is not a normalized repo-relative path",
                    raw_path,
                )
            )
            continue
        if not current_source_eligible(repo_path, ""):
            continue
        workspace_path = f"{target.display_path.rstrip('/')}/{repo_path}"
        records.append((workspace_path, repo_path, ""))
    chunks, read_problems = _current_source_chunks_from_records(
        root,
        records=records,
        existing_paths=set(),
    )
    return chunks, [*problems, *read_problems]


def context_overlay_chunks(
    root: Path,
    *,
    target: RepoTarget,
    workspace_paths: set[str],
    include_history: bool,
) -> tuple[list[DocumentChunk], list[Problem]]:
    selected = {str(path) for path in workspace_paths if str(path)}
    chunks: list[DocumentChunk] = []
    problems: list[Problem] = []
    document_paths = {
        path.relative_to(root).as_posix(): path
        for path in context_document_paths(root, target=target)
    }
    manifest_paths = {
        path.relative_to(root).as_posix(): path
        for path in context_product_manifest_paths(root, target=target)
    }
    for rel in sorted(selected & set(document_paths)):
        try:
            chunks.extend(chunk_markdown_file(root, document_paths[rel]))
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem("warning", "context_source_unreadable", str(exc), rel))
    for rel in sorted(selected & set(manifest_paths)):
        try:
            chunks.append(
                chunk_text_source(
                    root,
                    rel,
                    manifest_paths[rel].read_text(encoding="utf-8"),
                    kind="product_manifest",
                    section=manifest_paths[rel].name,
                )
            )
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem("warning", "context_manifest_unreadable", str(exc), rel))
    for hint in collect_verification_hints(target.root_path):
        path = target.root_path / hint.source_path
        if not path.is_file():
            continue
        rel = path.relative_to(root).as_posix()
        if rel not in selected:
            continue
        text = f"Verification command: {hint.command}\nSource: {rel}\nReason: {hint.reason}\nProvider: {hint.provider}"
        chunks.append(chunk_text_source(root, rel, text, kind="verification_hint", section=f"verification: {hint.command}"))
    history_changed = any(
        path.startswith(("docs/tasks/.repoctl-state/completions/", "docs/tasks/", "docs/archive/tasks/"))
        for path in selected
    )
    if include_history and history_changed:
        receipts, receipt_problems = collect_completion_receipts(root, repo_id=target.id)
        problems.extend(Problem("warning", problem.code, problem.message, problem.path) for problem in receipt_problems)
        for receipt in receipts:
            task_id = str(receipt.get("task_id") or "")
            receipt_rel = f"docs/tasks/.repoctl-state/completions/{task_id}.json"
            artifact = completion_receipt_artifact_path(root, receipt)
            artifacts = [artifact] if artifact else []
            if receipt_rel in selected:
                text = json.dumps(receipt, ensure_ascii=False, sort_keys=True, indent=2)
                chunks.append(chunk_text_source(root, receipt_rel, text, kind="completion_receipt", section=task_id or "completion receipt"))
            for artifact in artifacts:
                if receipt_rel not in selected and artifact not in selected:
                    continue
                path = root / artifact
                if not path.is_file():
                    continue
                try:
                    chunks.extend(chunk_markdown_file(root, path, kind="task_artifact"))
                except (OSError, UnicodeDecodeError) as exc:
                    problems.append(Problem("warning", "context_task_artifact_unreadable", str(exc), artifact))
    return chunks, problems


def context_document_paths(root: Path, *, target: RepoTarget) -> list[Path]:
    paths: set[Path] = set()
    for pattern in DOCUMENT_PATTERNS:
        paths.update(path for path in root.glob(pattern) if path.is_file())
    for pattern in PRODUCT_DOCUMENT_PATTERNS:
        paths.update(path for path in target.root_path.glob(pattern) if path.is_file())
    return sorted(path for path in paths if not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts))


def context_product_manifest_paths(root: Path, *, target: RepoTarget) -> list[Path]:
    paths: set[Path] = set()
    for pattern in product_manifest_patterns():
        paths.update(path for path in target.root_path.glob(pattern) if path.is_file())
    return sorted(path for path in paths if not any(part in EXCLUDED_PARTS for part in path.relative_to(root).parts))


def context_graph_problems(graph_problems: list[Problem]) -> list[Problem]:
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
    return mapped


def _manifest_digest(chunks: list[DocumentChunk]) -> str:
    return digest_data([chunk.source_ref.to_dict() for chunk in sorted(chunks, key=lambda item: item.source_ref.key())])

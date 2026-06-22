from __future__ import annotations

import json
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .context_chunks import chunk_markdown_file
from .graph_model import digest_data
from .io import RepoctlError, atomic_write
from .tasks import Problem


ALLOWED_KINDS = {"decision", "invariant", "failure_mode"}
ALLOWED_SOURCE_PREFIXES = ("docs/adr/", "docs/contracts/", "docs/workflows/")
EXCLUDED_SOURCE_PARTS = {".repoctl-state", "generated", "plans"}


def build_knowledge_candidate(root: Path, *, source: Path, repo_id: str, kind: str) -> tuple[dict[str, Any], list[Problem]]:
    if kind not in ALLOWED_KINDS:
        return {}, [Problem("error", "invalid_knowledge_candidate_kind", f"candidate kind must be one of {sorted(ALLOWED_KINDS)}")]
    rel = _source_rel(root, source)
    source_problem = _validate_source(root, rel)
    if source_problem is not None:
        return {}, [source_problem]

    path = root / rel
    chunks = chunk_markdown_file(root, path)
    if not chunks:
        return {}, [Problem("error", "knowledge_candidate_source_empty", "candidate source has no readable content", rel)]
    primary = _primary_chunk(chunks, kind)
    candidate_id = _candidate_id(primary.title)
    candidate = {
        "schema": "repoctl.knowledge.candidate",
        "schema_version": 1,
        "id": candidate_id,
        "repo_id": repo_id,
        "kind": kind,
        "status": "candidate",
        "authoritative": False,
        "title": primary.title,
        "claim": _claim(primary.text),
        "summary": _summary(primary.text),
        "source_refs": [primary.source_ref.to_dict()],
        "review": {
            "required": True,
            "status": "pending",
            "checklist": [
                "source refs resolve to current content digests",
                "claim does not come from generated/context/candidate output",
                "candidate should not replace task, Board, Graph, or .repometa authority",
            ],
        },
        "conflict_detected": False,
    }
    candidate["candidate_digest"] = digest_data(candidate)
    destination = _candidate_dir(root, repo_id) / f"{candidate_id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(destination, json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"candidate": candidate, "path": destination.relative_to(root).as_posix()}, []


def list_knowledge_candidates(root: Path, *, repo_id: str) -> dict[str, Any]:
    directory = _candidate_dir(root, repo_id)
    candidates = [_read_candidate(path) for path in sorted(directory.glob("KC-*.json"))] if directory.exists() else []
    return {
        "schema": "repoctl.knowledge.candidate_list",
        "schema_version": 1,
        "repo_id": repo_id,
        "candidates": [
            {
                "id": candidate.get("id", ""),
                "kind": candidate.get("kind", ""),
                "title": candidate.get("title", ""),
                "source_refs": candidate.get("source_refs", []),
                "authoritative": bool(candidate.get("authoritative", True)),
            }
            for candidate in candidates
        ],
    }


def show_knowledge_candidate(root: Path, *, repo_id: str, candidate_id: str) -> tuple[dict[str, Any], list[Problem]]:
    if not re.fullmatch(r"KC-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*", candidate_id):
        return {}, [Problem("error", "invalid_knowledge_candidate_id", "candidate id must look like KC-YYYYMMDDHHMMSSZ--slug")]
    path = _candidate_dir(root, repo_id) / f"{candidate_id}.json"
    if not path.is_file():
        return {}, [Problem("error", "knowledge_candidate_not_found", f"candidate not found: {candidate_id}", path.relative_to(root).as_posix())]
    return {"candidate": _read_candidate(path), "path": path.relative_to(root).as_posix()}, []


def _source_rel(root: Path, source: Path) -> str:
    path = source if source.is_absolute() else root / source
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RepoctlError("knowledge candidate source must be inside the workspace") from exc


def _validate_source(root: Path, rel: str) -> Problem | None:
    parts = set(Path(rel).parts)
    if parts & EXCLUDED_SOURCE_PARTS:
        return Problem("error", "knowledge_candidate_source_excluded", "candidate source is excluded from knowledge ingestion", rel)
    if not rel.startswith(ALLOWED_SOURCE_PREFIXES):
        return Problem("error", "knowledge_candidate_source_not_allowed", "candidate source must be an ADR, contract, or allowed workflow doc", rel)
    if not (root / rel).is_file():
        return Problem("error", "knowledge_candidate_source_missing", "candidate source file is missing", rel)
    return None


def _primary_chunk(chunks: Any, kind: str) -> Any:
    preferred = {
        "decision": {"Decision", "Authority Rules"},
        "invariant": {"Invariant", "Invariants", "Authority Rules"},
        "failure_mode": {"Failure Mode", "Failure Modes", "Known failure modes"},
    }[kind]
    for chunk in chunks:
        if chunk.title in preferred:
            return chunk
    return chunks[0]


def _claim(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if stripped and not stripped.startswith("```") and not stripped.startswith("#"):
            return stripped[:240]
    return ""


def _summary(text: str) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("```"))
    return compact[:500]


def _candidate_id(title: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "candidate"
    return f"KC-{stamp}--{slug[:60].strip('-')}"


def _candidate_dir(root: Path, repo_id: str) -> Path:
    return root / ".repoctl-state/knowledge/candidates" / repo_id


def _read_candidate(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}

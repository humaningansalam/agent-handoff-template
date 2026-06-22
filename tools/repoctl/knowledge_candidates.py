from __future__ import annotations

import json
import hashlib
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


def approve_knowledge_candidate(root: Path, *, repo_id: str, candidate_id: str) -> tuple[dict[str, Any], list[Problem]]:
    candidate_data, problems = show_knowledge_candidate(root, repo_id=repo_id, candidate_id=candidate_id)
    if problems:
        return {}, problems
    candidate = candidate_data["candidate"]
    digest_problems = _source_digest_problems(root, candidate)
    if digest_problems:
        return {}, digest_problems
    record_id = "K" + candidate_id[2:]
    record = {
        "schema": "repoctl.knowledge.record",
        "schema_version": 1,
        "id": record_id,
        "repo_id": repo_id,
        "kind": candidate.get("kind", ""),
        "status": "reviewed",
        "title": candidate.get("title", ""),
        "claim": candidate.get("claim", ""),
        "summary": candidate.get("summary", ""),
        "source_refs": candidate.get("source_refs", []),
        "supersedes": [],
        "created_from": {"candidate_id": candidate_id, "candidate_digest": candidate.get("candidate_digest", "")},
        "review": {"status": "reviewed", "reviewed_by": "human"},
        "authoritative": True,
    }
    record["record_digest"] = digest_data(record)
    record_path = _record_dir(root) / f"{record_id}.json"
    if record_path.exists():
        return {}, [Problem("error", "knowledge_record_exists", f"knowledge record already exists: {record_id}", record_path.relative_to(root).as_posix())]
    record_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(record_path, json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n")

    event = {
        "schema": "repoctl.knowledge.event",
        "schema_version": 1,
        "id": _event_id("approved"),
        "type": "approved",
        "repo_id": repo_id,
        "record_id": record_id,
        "candidate_id": candidate_id,
        "record_digest": record["record_digest"],
    }
    event["event_digest"] = digest_data(event)
    event_path = _event_dir(root) / f"{event['id']}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(event_path, json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {
        "record": record,
        "record_path": record_path.relative_to(root).as_posix(),
        "event": event,
        "event_path": event_path.relative_to(root).as_posix(),
    }, []


def show_knowledge_record(root: Path, *, record_id: str) -> tuple[dict[str, Any], list[Problem]]:
    if not re.fullmatch(r"K-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*", record_id):
        return {}, [Problem("error", "invalid_knowledge_record_id", "record id must look like K-YYYYMMDDHHMMSSZ--slug")]
    path = _record_dir(root) / f"{record_id}.json"
    if not path.is_file():
        return {}, [Problem("error", "knowledge_record_not_found", f"knowledge record not found: {record_id}", path.relative_to(root).as_posix())]
    return {"record": _read_candidate(path), "path": path.relative_to(root).as_posix()}, []


def check_knowledge_records(root: Path, *, repo_id: str) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    records = [_read_candidate(path) for path in sorted(_record_dir(root).glob("K-*.json"))]
    selected = [record for record in records if str(record.get("repo_id") or "") == repo_id]
    for record in selected:
        problems.extend(_source_digest_problems(root, record, record_id=str(record.get("id") or "")))
    return {
        "schema": "repoctl.knowledge.check",
        "schema_version": 1,
        "repo_id": repo_id,
        "record_count": len(selected),
        "records": [
            {
                "id": record.get("id", ""),
                "kind": record.get("kind", ""),
                "status": "stale" if _source_digest_problems(root, record) else record.get("status", ""),
                "title": record.get("title", ""),
            }
            for record in selected
        ],
    }, problems


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


def _event_id(kind: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%SZ")
    return f"E-{stamp}--{kind}"


def _candidate_dir(root: Path, repo_id: str) -> Path:
    return root / ".repoctl-state/knowledge/candidates" / repo_id


def _record_dir(root: Path) -> Path:
    return root / "docs/knowledge/records"


def _event_dir(root: Path) -> Path:
    return root / "docs/knowledge/events"


def _read_candidate(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _source_digest_problems(root: Path, data: dict[str, Any], *, record_id: str = "") -> list[Problem]:
    problems: list[Problem] = []
    refs = data.get("source_refs", [])
    if not isinstance(refs, list) or not refs:
        problems.append(Problem("error", "knowledge_source_refs_missing", "knowledge item has no source refs", str(record_id)))
        return problems
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        rel = str(ref.get("path") or "")
        expected = str(ref.get("content_sha256") or "")
        path = root / rel
        if not path.is_file():
            problems.append(Problem("error", "knowledge_source_missing", "knowledge source file is missing", rel))
            continue
        actual = "sha256:" + hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        if expected != actual:
            problems.append(Problem("error", "knowledge_source_digest_drift", "knowledge source digest changed", rel))
    return problems

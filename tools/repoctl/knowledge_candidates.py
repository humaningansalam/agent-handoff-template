from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from .context_chunks import chunk_markdown_file
from .graph_model import digest_data
from .io import RepoctlError, atomic_write
from .markdown import find_section, parse_frontmatter
from .tasks import Problem, load_completion_receipts, normalize_task_id


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
    candidate_id = _unique_candidate_id(root, repo_id, primary.title, primary.source_ref.content_sha256)
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


def build_knowledge_candidate_from_receipt(root: Path, *, task_id: str, repo_id: str, kind: str) -> tuple[dict[str, Any], list[Problem]]:
    if kind not in ALLOWED_KINDS:
        return {}, [Problem("error", "invalid_knowledge_candidate_kind", f"candidate kind must be one of {sorted(ALLOWED_KINDS)}")]
    normalized_task_id = normalize_task_id(task_id)
    receipt = _receipt_for_task(root, task_id=normalized_task_id, repo_id=repo_id)
    if receipt is None:
        return {}, [Problem("error", "knowledge_candidate_receipt_missing", f"completion receipt not found for task: {normalized_task_id}")]
    receipt_rel = f"docs/tasks/.repoctl-state/completions/{normalized_task_id}.json"
    artifact_rel = str(receipt.get("archive_path") or receipt.get("task_path") or "")
    artifact_path = root / artifact_rel
    if not artifact_path.is_file():
        return {}, [Problem("error", "knowledge_candidate_receipt_artifact_missing", "completion receipt artifact is missing", artifact_rel)]
    artifact_text = artifact_path.read_text(encoding="utf-8")
    title = _receipt_title(receipt, artifact_text)
    summary = _receipt_summary(receipt, artifact_text)
    receipt_text = (root / receipt_rel).read_text(encoding="utf-8")
    source_refs = [
        {
            "kind": "completion_receipt",
            "path": receipt_rel,
            "section": normalized_task_id,
            "content_sha256": _sha256_text(receipt_text),
        },
        {
            "kind": "task_artifact",
            "path": artifact_rel,
            "section": "Verification",
            "content_sha256": str(receipt.get("content_sha256") or ""),
        },
    ]
    candidate_id = _unique_candidate_id(root, repo_id, title, source_refs[0]["content_sha256"])
    candidate = {
        "schema": "repoctl.knowledge.candidate",
        "schema_version": 1,
        "id": candidate_id,
        "repo_id": repo_id,
        "kind": kind,
        "status": "candidate",
        "authoritative": False,
        "title": title,
        "claim": _claim(summary),
        "summary": summary,
        "source_refs": source_refs,
        "review": {
            "required": True,
            "status": "pending",
            "checklist": [
                "completion receipt and task artifact digests still match",
                "candidate captures a stable reusable fact, not one-off task prose",
                "candidate should not replace task, Board, Graph, or .repometa authority",
            ],
        },
        "conflict_detected": False,
        "derived_from": {"kind": "completion_receipt", "task_id": normalized_task_id},
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


def knowledge_status(root: Path, *, repo_id: str) -> dict[str, Any]:
    candidates = list_knowledge_candidates(root, repo_id=repo_id)["candidates"]
    records = [record for record in _load_records(root) if str(record.get("repo_id") or "") == repo_id]
    superseded_ids = _superseded_ids(records)
    statuses: dict[str, int] = {}
    for record in records:
        status = _derived_status(root, record, superseded_ids=superseded_ids)
        statuses[status] = statuses.get(status, 0) + 1
    events = _load_events(root, repo_id=repo_id)
    event_types: dict[str, int] = {}
    for event in events:
        event_type = str(event.get("type") or "")
        event_types[event_type] = event_types.get(event_type, 0) + 1
    return {
        "schema": "repoctl.knowledge.status",
        "schema_version": 1,
        "repo_id": repo_id,
        "candidate_count": len(candidates),
        "record_count": len(records),
        "record_statuses": dict(sorted(statuses.items())),
        "event_count": len(events),
        "event_types": dict(sorted(event_types.items())),
    }


def show_knowledge_candidate(root: Path, *, repo_id: str, candidate_id: str) -> tuple[dict[str, Any], list[Problem]]:
    if not re.fullmatch(r"KC-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*", candidate_id):
        return {}, [Problem("error", "invalid_knowledge_candidate_id", "candidate id must look like KC-YYYYMMDDHHMMSSZ--slug")]
    path = _candidate_dir(root, repo_id) / f"{candidate_id}.json"
    if not path.is_file():
        return {}, [Problem("error", "knowledge_candidate_not_found", f"candidate not found: {candidate_id}", path.relative_to(root).as_posix())]
    return {"candidate": _read_candidate(path), "path": path.relative_to(root).as_posix()}, []


def approve_knowledge_candidate(root: Path, *, repo_id: str, candidate_id: str, supersedes: list[str] | None = None) -> tuple[dict[str, Any], list[Problem]]:
    candidate_data, problems = show_knowledge_candidate(root, repo_id=repo_id, candidate_id=candidate_id)
    if problems:
        return {}, problems
    candidate = candidate_data["candidate"]
    digest_problems = _source_digest_problems(root, candidate)
    if digest_problems:
        return {}, digest_problems
    supersedes = supersedes or []
    relation_problems = _validate_supersedes(root, repo_id=repo_id, supersedes=supersedes)
    if relation_problems:
        return {}, relation_problems
    record_id = "K" + candidate_id[2:]
    if record_id in supersedes:
        return {}, [Problem("error", "knowledge_supersedes_self", "knowledge record cannot supersede itself", record_id)]
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
        "supersedes": supersedes,
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
        "id": _event_id("approved", record_id),
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
    superseded_events = []
    for superseded_id in supersedes:
        superseded_event = {
            "schema": "repoctl.knowledge.event",
            "schema_version": 1,
            "id": _event_id("superseded", superseded_id),
            "type": "superseded",
            "repo_id": repo_id,
            "record_id": superseded_id,
            "superseded_by": record_id,
            "record_digest": record["record_digest"],
        }
        superseded_event["event_digest"] = digest_data(superseded_event)
        superseded_path = _event_dir(root) / f"{superseded_event['id']}.json"
        atomic_write(superseded_path, json.dumps(superseded_event, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        superseded_events.append({"event": superseded_event, "event_path": superseded_path.relative_to(root).as_posix()})
    return {
        "record": record,
        "record_path": record_path.relative_to(root).as_posix(),
        "event": event,
        "event_path": event_path.relative_to(root).as_posix(),
        "superseded_events": superseded_events,
    }, []


def reject_knowledge_candidate(root: Path, *, repo_id: str, candidate_id: str, reason_file: Path) -> tuple[dict[str, Any], list[Problem]]:
    candidate_data, problems = show_knowledge_candidate(root, repo_id=repo_id, candidate_id=candidate_id)
    if problems:
        return {}, problems
    reason_path = reason_file if reason_file.is_absolute() else root / reason_file
    if not reason_path.is_file():
        return {}, [Problem("error", "knowledge_reject_reason_missing", "reject reason file is missing", reason_path.as_posix())]
    reason = reason_path.read_text(encoding="utf-8").strip()
    if not reason:
        return {}, [Problem("error", "knowledge_reject_reason_empty", "reject reason file is empty", reason_path.as_posix())]
    candidate = candidate_data["candidate"]
    event = {
        "schema": "repoctl.knowledge.event",
        "schema_version": 1,
        "id": _event_id("rejected-candidate", candidate_id),
        "type": "rejected_candidate",
        "repo_id": repo_id,
        "candidate_id": candidate_id,
        "candidate_digest": candidate.get("candidate_digest", ""),
        "reason": reason,
    }
    event["event_digest"] = digest_data(event)
    event_path = _event_dir(root) / f"{event['id']}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(event_path, json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"event": event, "event_path": event_path.relative_to(root).as_posix()}, []


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
    superseded_ids = _superseded_ids(selected)
    for record in selected:
        problems.extend(_source_digest_problems(root, record, record_id=str(record.get("id") or "")))
    problems.extend(_supersession_problems(selected))
    return {
        "schema": "repoctl.knowledge.check",
        "schema_version": 1,
        "repo_id": repo_id,
        "record_count": len(selected),
        "records": [
            {
                "id": record.get("id", ""),
                "kind": record.get("kind", ""),
                "status": _derived_status(root, record, superseded_ids=superseded_ids),
                "title": record.get("title", ""),
            }
            for record in selected
        ],
    }, problems


def query_knowledge_records(root: Path, *, repo_id: str, query: str, include_stale: bool = False, include_superseded: bool = False, limit: int = 10) -> tuple[dict[str, Any], list[Problem], list[Problem]]:
    problems: list[Problem] = []
    warnings: list[Problem] = []
    records = [record for record in _load_records(root) if str(record.get("repo_id") or "") == repo_id]
    superseded_ids = _superseded_ids(records)
    scored: list[dict[str, Any]] = []
    for record in records:
        status = _derived_status(root, record, superseded_ids=superseded_ids)
        if status == "stale" and not include_stale:
            warnings.append(Problem("warning", "knowledge_stale_record_excluded", "stale knowledge record excluded from default query", str(record.get("id") or "")))
            continue
        if status == "superseded" and not include_superseded:
            warnings.append(Problem("warning", "knowledge_superseded_record_excluded", "superseded knowledge record excluded from default query", str(record.get("id") or "")))
            continue
        if status not in {"reviewed", "stale", "superseded"}:
            continue
        score, breakdown, reasons = _record_score(query, record)
        if score <= 0:
            continue
        scored.append(
            {
                "record": _public_record(record, status=status),
                "score": round(score, 6),
                "score_breakdown": {key: round(value, 6) for key, value in sorted(breakdown.items())},
                "selection_reasons": reasons,
            }
        )
    scored.sort(key=lambda item: (-float(item["score"]), str(item["record"].get("id") or "")))
    return {
        "schema": "repoctl.knowledge.query",
        "schema_version": 1,
        "repo_id": repo_id,
        "query": {"text": query, "include_stale": include_stale, "include_superseded": include_superseded},
        "results": scored[:limit],
        "result_count": min(len(scored), limit),
        "available_record_count": len(records),
    }, problems, warnings


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


def _receipt_for_task(root: Path, *, task_id: str, repo_id: str) -> dict[str, Any] | None:
    for receipt in load_completion_receipts(root, repo_id=repo_id):
        if str(receipt.get("task_id") or "") == task_id:
            return receipt
    return None


def _receipt_title(receipt: dict[str, Any], artifact_text: str) -> str:
    task_id = str(receipt.get("task_id") or "task")
    frontmatter, body = _artifact_parts(artifact_text)
    title = str(frontmatter.get("title") or "").strip() or _artifact_heading(body)
    if title:
        return title
    return f"{task_id} completion"


def _receipt_summary(receipt: dict[str, Any], artifact_text: str) -> str:
    _frontmatter, body = _artifact_parts(artifact_text)
    parts = [
        f"Task `{receipt.get('task_id', '')}` completed with status `{receipt.get('status', '')}`.",
        _artifact_section(body, "Goal"),
        _artifact_section(body, "Verification"),
    ]
    changed_entries = receipt.get("changed_entries")
    if isinstance(changed_entries, list) and changed_entries:
        changed = ", ".join(str(item.get("path") or "") for item in changed_entries if isinstance(item, dict) and item.get("path"))
        if changed:
            parts.append(f"Changed files: {changed}")
    return "\n\n".join(part.strip() for part in parts if part.strip())[:1000]


def _artifact_heading(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip()
        if stripped.startswith("# "):
            return stripped[2:].strip()
    return ""


def _artifact_parts(text: str) -> tuple[dict[str, Any], str]:
    try:
        frontmatter, body = parse_frontmatter(text)
    except Exception:
        return {}, text
    return frontmatter, body


def _artifact_section(text: str, heading: str) -> str:
    try:
        section = find_section(text, heading)
    except Exception:
        return ""
    return text[section.body_start : section.end].strip()


def _claim(text: str) -> str:
    for line in text.splitlines():
        stripped = line.strip().lstrip("-").strip()
        if stripped and not stripped.startswith("```") and not stripped.startswith("#"):
            return stripped[:240]
    return ""


def _summary(text: str) -> str:
    compact = " ".join(line.strip() for line in text.splitlines() if line.strip() and not line.strip().startswith("```"))
    return compact[:500]


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _unique_candidate_id(root: Path, repo_id: str, title: str, source_digest: str) -> str:
    base = _candidate_id(title, source_digest)
    directory = _candidate_dir(root, repo_id)
    if not (directory / f"{base}.json").exists():
        return base
    for index in range(2, 100):
        candidate = f"{base}-{index}"
        if not (directory / f"{candidate}.json").exists():
            return candidate
    raise RepoctlError("could not allocate unique knowledge candidate id")


def _candidate_id(title: str, source_digest: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "candidate"
    suffix = re.sub(r"[^a-f0-9]", "", source_digest.lower())[:8] or "candidate"
    return f"KC-{stamp}--{slug[:48].strip('-')}-{suffix}"


def _event_id(kind: str, target_id: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", target_id.lower()).strip("-") or kind
    return f"E-{stamp}--{kind}-{slug[:48].strip('-')}"


def _candidate_dir(root: Path, repo_id: str) -> Path:
    return root / ".repoctl-state/knowledge/candidates" / repo_id


def _record_dir(root: Path) -> Path:
    return root / "docs/knowledge/records"


def _event_dir(root: Path) -> Path:
    return root / "docs/knowledge/events"


def _read_candidate(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    return data if isinstance(data, dict) else {}


def _load_records(root: Path) -> list[dict[str, Any]]:
    directory = _record_dir(root)
    return [_read_candidate(path) for path in sorted(directory.glob("K-*.json"))] if directory.exists() else []


def _load_events(root: Path, *, repo_id: str) -> list[dict[str, Any]]:
    directory = _event_dir(root)
    if not directory.exists():
        return []
    events = [_read_candidate(path) for path in sorted(directory.glob("E-*.json"))]
    return [event for event in events if str(event.get("repo_id") or "") == repo_id]


def _validate_supersedes(root: Path, *, repo_id: str, supersedes: list[str]) -> list[Problem]:
    problems: list[Problem] = []
    records = {str(record.get("id") or ""): record for record in _load_records(root)}
    seen: set[str] = set()
    for record_id in supersedes:
        if record_id in seen:
            problems.append(Problem("error", "knowledge_supersedes_duplicate", "duplicate supersedes record id", record_id))
            continue
        seen.add(record_id)
        record = records.get(record_id)
        if record is None:
            problems.append(Problem("error", "knowledge_supersedes_missing", "superseded record does not exist", record_id))
            continue
        if str(record.get("repo_id") or "") != repo_id:
            problems.append(Problem("error", "knowledge_supersedes_repo_mismatch", "superseded record belongs to a different repo", record_id))
    return problems


def _superseded_ids(records: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for record in records:
        supersedes = record.get("supersedes", [])
        if isinstance(supersedes, list):
            values.update(str(item) for item in supersedes if str(item))
    return values


def _supersession_problems(records: list[dict[str, Any]]) -> list[Problem]:
    problems: list[Problem] = []
    known = {str(record.get("id") or "") for record in records}
    for record in records:
        record_id = str(record.get("id") or "")
        supersedes = record.get("supersedes", [])
        if not isinstance(supersedes, list):
            problems.append(Problem("error", "knowledge_supersedes_invalid", "supersedes must be a list", record_id))
            continue
        for superseded_id in supersedes:
            superseded = str(superseded_id)
            if superseded == record_id:
                problems.append(Problem("error", "knowledge_supersedes_self", "knowledge record cannot supersede itself", record_id))
            if superseded and superseded not in known:
                problems.append(Problem("error", "knowledge_supersedes_missing", "superseded record does not exist", superseded))
    return problems


def _derived_status(root: Path, record: dict[str, Any], *, superseded_ids: set[str]) -> str:
    if _source_digest_problems(root, record):
        return "stale"
    record_id = str(record.get("id") or "")
    if record_id in superseded_ids:
        return "superseded"
    return str(record.get("status") or "")


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


def _public_record(record: dict[str, Any], *, status: str) -> dict[str, Any]:
    return {
        "id": record.get("id", ""),
        "repo_id": record.get("repo_id", ""),
        "kind": record.get("kind", ""),
        "status": status,
        "title": record.get("title", ""),
        "claim": record.get("claim", ""),
        "summary": record.get("summary", ""),
        "source_refs": record.get("source_refs", []),
        "record_digest": record.get("record_digest", ""),
    }


def _record_score(query: str, record: dict[str, Any]) -> tuple[float, dict[str, float], list[str]]:
    fields = [
        str(record.get("id") or ""),
        str(record.get("kind") or ""),
        str(record.get("title") or ""),
        str(record.get("claim") or ""),
        str(record.get("summary") or ""),
        json.dumps(record.get("source_refs", []), ensure_ascii=False, sort_keys=True),
    ]
    body = "\n".join(fields)
    exact = _exact_score(query, body)
    fts = _fts_score(query, body)
    authority = 0.5 if str(record.get("status") or "") == "reviewed" else 0.0
    score = exact * 2.0 + fts * 1.2 + authority
    reasons: list[str] = []
    if exact:
        reasons.append("exact record field match")
    if fts:
        reasons.append("SQLite FTS record match")
    if authority:
        reasons.append("reviewed knowledge record")
    return score, {"exact": exact, "fts": fts, "authority": authority}, reasons


def _exact_score(query: str, body: str) -> float:
    terms = _query_terms(query)
    if not terms:
        return 0.0
    haystack = body.lower()
    hits = sum(1 for term in terms if term.lower() in haystack)
    return min(1.0, hits / len(terms))


def _fts_score(query: str, body: str) -> float:
    terms = _query_terms(query)
    if not terms:
        return 0.0
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE records USING fts5(body)")
        conn.execute("INSERT INTO records(body) VALUES (?)", (body,))
        phrase = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        row = conn.execute("SELECT bm25(records) AS rank FROM records WHERE records MATCH ? LIMIT 1", (phrase,)).fetchone()
        if row is None:
            return 0.0
        return 1.0 / (1.0 + abs(float(row[0])))
    except sqlite3.Error:
        return 0.0
    finally:
        conn.close()


def _query_terms(query: str) -> list[str]:
    stopwords = {"a", "an", "and", "are", "for", "from", "how", "is", "of", "the", "to", "what", "why"}
    tokens = re.findall(r"[A-Za-z0-9_./:-]+|[가-힣]+", query)
    return sorted({token for token in tokens if len(token) >= 2 and token.lower() not in stopwords})

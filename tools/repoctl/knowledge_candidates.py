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
    return _write_candidate_from_chunk(root, repo_id=repo_id, kind=kind, primary=primary)


def build_knowledge_candidate_from_pack(root: Path, *, pack: Path, repo_id: str, kind: str) -> tuple[dict[str, Any], list[Problem]]:
    if kind not in ALLOWED_KINDS:
        return {}, [Problem("error", "invalid_knowledge_candidate_kind", f"candidate kind must be one of {sorted(ALLOWED_KINDS)}")]
    pack_path = pack if pack.is_absolute() else root / pack
    pack_data, pack_problems = _read_context_pack_artifact(root, pack_path)
    if pack_problems:
        return {}, pack_problems
    task = pack_data.get("task") if isinstance(pack_data.get("task"), dict) else {}
    pack_repo_id = str(task.get("repo_id") or "")
    if pack_repo_id and pack_repo_id != repo_id:
        return {}, [Problem("error", "knowledge_candidate_pack_repo_mismatch", "context pack repo_id does not match candidate repo_id", pack_repo_id)]
    source_ref, source_problem = _pack_authority_source_ref(root, pack_data, kind)
    if source_problem is not None:
        return {}, [source_problem]
    rel = str(source_ref.get("path") or "")
    chunks = chunk_markdown_file(root, root / rel)
    if not chunks:
        return {}, [Problem("error", "knowledge_candidate_source_empty", "candidate source has no readable content", rel)]
    primary = _primary_chunk(chunks, kind)
    return _write_candidate_from_chunk(
        root,
        repo_id=repo_id,
        kind=kind,
        primary=primary,
        derived_from={
            "kind": "context_pack",
            "path": pack_path.relative_to(root).as_posix() if pack_path.is_relative_to(root) else pack_path.as_posix(),
            "pack_digest": str(pack_data.get("pack_digest") or ""),
        },
        checklist=[
            "context pack was used only to select authority source refs",
            "source refs resolve to current content digests",
            "claim does not come from generated/context/candidate output",
            "candidate should not replace task, Board, Graph, or .repometa authority",
        ],
    )


def _write_candidate_from_chunk(
    root: Path,
    *,
    repo_id: str,
    kind: str,
    primary: Any,
    derived_from: dict[str, Any] | None = None,
    checklist: list[str] | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
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
            "checklist": checklist
            or [
                "source refs resolve to current content digests",
                "claim does not come from generated/context/candidate output",
                "candidate should not replace task, Board, Graph, or .repometa authority",
            ],
        },
        "conflict_detected": False,
    }
    if derived_from:
        candidate["derived_from"] = derived_from
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
    changed_files = _receipt_changed_files(receipt)
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
        "derived_from": {
            "kind": "completion_receipt",
            "task_id": normalized_task_id,
            "repo_id": repo_id,
            "verification_artifact": artifact_rel,
            "changed_files": changed_files,
            "related_symbols": [],
        },
    }
    candidate["candidate_digest"] = digest_data(candidate)
    destination = _candidate_dir(root, repo_id) / f"{candidate_id}.json"
    destination.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(destination, json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"candidate": candidate, "path": destination.relative_to(root).as_posix()}, []


def list_knowledge_candidates(root: Path, *, repo_id: str, with_checks: bool = False) -> dict[str, Any]:
    directory = _candidate_dir(root, repo_id)
    candidates = [_read_candidate(path) for path in sorted(directory.glob("KC-*.json"))] if directory.exists() else []
    review_states = _candidate_review_states(root, repo_id=repo_id)
    items = [
        {
            "id": candidate.get("id", ""),
            "kind": candidate.get("kind", ""),
            "title": candidate.get("title", ""),
            "source_refs": candidate.get("source_refs", []),
            "authoritative": bool(candidate.get("authoritative", True)),
            "review_state": review_states.get(str(candidate.get("id") or ""), "pending"),
        }
        for candidate in candidates
    ]
    if with_checks:
        checks = _candidate_checks(root, repo_id=repo_id, candidates=candidates)
        by_id = {item["candidate_id"]: item for item in checks["results"]}
        for item in items:
            check = by_id.get(str(item.get("id") or ""))
            if check:
                item["check"] = check
    return {
        "schema": "repoctl.knowledge.candidate_list",
        "schema_version": 1,
        "repo_id": repo_id,
        "with_checks": with_checks,
        "candidates": items,
    }


def knowledge_status(root: Path, *, repo_id: str) -> dict[str, Any]:
    directory = _candidate_dir(root, repo_id)
    candidate_records = [_read_candidate(path) for path in sorted(directory.glob("KC-*.json"))] if directory.exists() else []
    candidates = list_knowledge_candidates(root, repo_id=repo_id)["candidates"]
    candidate_review_states: dict[str, int] = {}
    for item in candidates:
        state = str(item.get("review_state") or "pending")
        candidate_review_states[state] = candidate_review_states.get(state, 0) + 1
    candidate_checks = _candidate_checks(root, repo_id=repo_id, candidates=candidate_records)
    candidate_problem_codes: dict[str, int] = {}
    candidate_warning_codes: dict[str, int] = {}
    for result in candidate_checks["results"]:
        for problem in result["problems"]:
            code = str(problem.get("code") or "")
            if code:
                candidate_problem_codes[code] = candidate_problem_codes.get(code, 0) + 1
        for warning in result["warnings"]:
            code = str(warning.get("code") or "")
            if code:
                candidate_warning_codes[code] = candidate_warning_codes.get(code, 0) + 1
    records = [record for record in _load_records(root) if str(record.get("repo_id") or "") == repo_id]
    superseded_ids = _superseded_ids(records)
    deprecated_ids = _deprecated_ids(root, repo_id=repo_id)
    statuses: dict[str, int] = {}
    for record in records:
        status = _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
        statuses[status] = statuses.get(status, 0) + 1
    record_problem_codes: dict[str, int] = {}
    record_problems: list[Problem] = []
    for record in records:
        status = _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
        if status not in {"superseded", "deprecated"}:
            record_problems.extend(_source_digest_problems(root, record, record_id=str(record.get("id") or "")))
    record_problems.extend(_supersession_problems(records))
    record_problems.extend(event_integrity_problems(root, repo_id=repo_id, records=records))
    for problem in record_problems:
        record_problem_codes[problem.code] = record_problem_codes.get(problem.code, 0) + 1
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
        "candidate_review_states": dict(sorted(candidate_review_states.items())),
        "candidate_checks": {
            "passed_count": candidate_checks["passed_count"],
            "error_count": candidate_checks["error_count"],
            "warning_count": candidate_checks["warning_count"],
            "problem_codes": dict(sorted(candidate_problem_codes.items())),
            "warning_codes": dict(sorted(candidate_warning_codes.items())),
        },
        "record_count": len(records),
        "record_statuses": dict(sorted(statuses.items())),
        "record_checks": {
            "error_count": len([problem for problem in record_problems if problem.severity == "error"]),
            "warning_count": len([problem for problem in record_problems if problem.severity == "warning"]),
            "problem_codes": dict(sorted(record_problem_codes.items())),
        },
        "event_count": len(events),
        "event_types": dict(sorted(event_types.items())),
    }


def list_knowledge_events(root: Path, *, repo_id: str, event_type: str = "", candidate_id: str = "", record_id: str = "") -> dict[str, Any]:
    events = _load_events(root, repo_id=repo_id)
    filtered: list[dict[str, Any]] = []
    for event in events:
        if event_type and str(event.get("type") or "") != event_type:
            continue
        if candidate_id and str(event.get("candidate_id") or "") != candidate_id:
            continue
        if record_id and str(event.get("record_id") or "") != record_id:
            continue
        filtered.append(event)
    return {
        "schema": "repoctl.knowledge.event_list",
        "schema_version": 1,
        "repo_id": repo_id,
        "filters": {
            "type": event_type,
            "candidate_id": candidate_id,
            "record_id": record_id,
        },
        "event_count": len(filtered),
        "events": [_event_summary(event) for event in filtered],
    }


def show_knowledge_event(root: Path, *, repo_id: str, event_id: str) -> tuple[dict[str, Any], list[Problem]]:
    if not re.fullmatch(r"E-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*", event_id):
        return {}, [Problem("error", "invalid_knowledge_event_id", "event id must look like E-YYYYMMDDHHMMSSZ--slug")]
    path = _event_dir(root) / f"{event_id}.json"
    if not path.is_file():
        return {}, [Problem("error", "knowledge_event_not_found", f"knowledge event not found: {event_id}", path.relative_to(root).as_posix())]
    event = _read_candidate(path)
    if str(event.get("repo_id") or "") != repo_id:
        return {}, [Problem("error", "knowledge_event_repo_mismatch", "knowledge event belongs to a different repo", event_id)]
    return {"event": event, "path": path.relative_to(root).as_posix()}, []


def show_knowledge_candidate(root: Path, *, repo_id: str, candidate_id: str) -> tuple[dict[str, Any], list[Problem]]:
    if not re.fullmatch(r"KC-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*", candidate_id):
        return {}, [Problem("error", "invalid_knowledge_candidate_id", "candidate id must look like KC-YYYYMMDDHHMMSSZ--slug")]
    path = _candidate_dir(root, repo_id) / f"{candidate_id}.json"
    if not path.is_file():
        return {}, [Problem("error", "knowledge_candidate_not_found", f"candidate not found: {candidate_id}", path.relative_to(root).as_posix())]
    return {"candidate": _read_candidate(path), "path": path.relative_to(root).as_posix()}, []


def check_knowledge_candidate(root: Path, *, repo_id: str, candidate_id: str) -> tuple[dict[str, Any], list[Problem]]:
    data, problems = show_knowledge_candidate(root, repo_id=repo_id, candidate_id=candidate_id)
    if problems:
        return {}, problems
    candidate = data["candidate"]
    check_problems = _candidate_quality_problems(root, candidate)
    return {
        "schema": "repoctl.knowledge.candidate_check",
        "schema_version": 1,
        "repo_id": repo_id,
        "candidate_id": candidate_id,
        "candidate_digest": candidate.get("candidate_digest", ""),
        "passed": not any(problem.severity == "error" for problem in check_problems),
        "checks": _candidate_check_flags(candidate, check_problems),
        "related_records": _candidate_related_records(root, candidate),
        "source_ref_statuses": _source_ref_statuses(root, candidate),
    }, check_problems


def check_all_knowledge_candidates(root: Path, *, repo_id: str, pending_only: bool = True) -> tuple[dict[str, Any], list[Problem]]:
    directory = _candidate_dir(root, repo_id)
    candidates = [_read_candidate(path) for path in sorted(directory.glob("KC-*.json"))] if directory.exists() else []
    if pending_only:
        review_states = _candidate_review_states(root, repo_id=repo_id)
        checked_candidates = [candidate for candidate in candidates if review_states.get(str(candidate.get("id") or ""), "pending") == "pending"]
    else:
        checked_candidates = candidates
    data = _candidate_checks(root, repo_id=repo_id, candidates=checked_candidates)
    data["candidate_total_count"] = len(candidates)
    data["pending_only"] = pending_only
    data["skipped_non_pending_count"] = len(candidates) - len(checked_candidates)
    problems: list[Problem] = []
    for result in data["results"]:
        for problem in result["problems"]:
            problems.append(Problem("error", str(problem.get("code") or ""), str(problem.get("message") or ""), str(problem.get("path") or "") or None))
        for warning in result["warnings"]:
            problems.append(Problem("warning", str(warning.get("code") or ""), str(warning.get("message") or ""), str(warning.get("path") or "") or None))
    return data, problems


def refresh_knowledge_candidate(root: Path, *, repo_id: str, candidate_id: str) -> tuple[dict[str, Any], list[Problem]]:
    candidate_data, problems = show_knowledge_candidate(root, repo_id=repo_id, candidate_id=candidate_id)
    if problems:
        return {}, problems
    old_candidate = candidate_data["candidate"]
    if str(old_candidate.get("repo_id") or "") != repo_id:
        return {}, [Problem("error", "knowledge_candidate_repo_mismatch", "candidate belongs to a different repo", candidate_id)]
    kind = str(old_candidate.get("kind") or "")
    derived_from = old_candidate.get("derived_from")
    if isinstance(derived_from, dict) and derived_from.get("kind") == "completion_receipt":
        task_id = str(derived_from.get("task_id") or "")
        if not task_id:
            return {}, [Problem("error", "knowledge_candidate_refresh_source_missing", "receipt-derived candidate is missing task_id", candidate_id)]
        refreshed_data, refresh_problems = build_knowledge_candidate_from_receipt(root, task_id=task_id, repo_id=repo_id, kind=kind)
    else:
        source_path = _refresh_source_path(old_candidate)
        if not source_path:
            return {}, [Problem("error", "knowledge_candidate_refresh_source_missing", "candidate has no refreshable document source", candidate_id)]
        refreshed_data, refresh_problems = build_knowledge_candidate(root, source=Path(source_path), repo_id=repo_id, kind=kind)
    if refresh_problems:
        return {}, refresh_problems

    new_candidate = refreshed_data["candidate"]
    event = {
        "schema": "repoctl.knowledge.event",
        "schema_version": 1,
        "id": _unique_event_id(root, "refreshed-candidate", candidate_id),
        "type": "refreshed_candidate",
        "repo_id": repo_id,
        "candidate_id": candidate_id,
        "new_candidate_id": new_candidate.get("id", ""),
        "candidate_digest": old_candidate.get("candidate_digest", ""),
        "new_candidate_digest": new_candidate.get("candidate_digest", ""),
    }
    event["event_digest"] = digest_data(event)
    event_path = _event_dir(root) / f"{event['id']}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(event_path, json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {
        "candidate": new_candidate,
        "path": refreshed_data["path"],
        "refreshed_from": candidate_id,
        "event": event,
        "event_path": event_path.relative_to(root).as_posix(),
    }, []


def refresh_knowledge_record_candidate(root: Path, *, repo_id: str, record_id: str) -> tuple[dict[str, Any], list[Problem]]:
    record_data, problems = show_knowledge_record(root, repo_id=repo_id, record_id=record_id)
    if problems:
        return {}, problems
    record = record_data["record"]
    if not _source_digest_problems(root, record, record_id=record_id):
        return {}, [Problem("error", "knowledge_record_refresh_not_stale", "knowledge record is not stale", record_id)]
    source_path = _refresh_source_path(record)
    if not source_path:
        return {}, [Problem("error", "knowledge_record_refresh_source_missing", "record has no refreshable document source", record_id)]
    kind = str(record.get("kind") or "")
    chunks = chunk_markdown_file(root, root / source_path)
    if not chunks:
        return {}, [Problem("error", "knowledge_candidate_source_empty", "candidate source has no readable content", source_path)]
    refreshed_data, refresh_problems = _write_candidate_from_chunk(
        root,
        repo_id=repo_id,
        kind=kind,
        primary=_primary_chunk(chunks, kind),
        derived_from={
            "kind": "knowledge_record",
            "record_id": record_id,
            "record_digest": record.get("record_digest", ""),
        },
        checklist=[
            "refreshed source refs resolve to current content digests",
            "candidate should replace the stale reviewed record only after explicit approval",
            "approval should supersede the original reviewed record instead of editing it",
            "candidate should not replace task, Board, Graph, or .repometa authority",
        ],
    )
    if refresh_problems:
        return {}, refresh_problems

    new_candidate = refreshed_data["candidate"]
    event = {
        "schema": "repoctl.knowledge.event",
        "schema_version": 1,
        "id": _unique_event_id(root, "refreshed-record-candidate", record_id),
        "type": "refreshed_record_candidate",
        "repo_id": repo_id,
        "record_id": record_id,
        "record_digest": record.get("record_digest", ""),
        "new_candidate_id": new_candidate.get("id", ""),
        "new_candidate_digest": new_candidate.get("candidate_digest", ""),
    }
    event["event_digest"] = digest_data(event)
    event_path = _event_dir(root) / f"{event['id']}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(event_path, json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {
        "candidate": new_candidate,
        "path": refreshed_data["path"],
        "refreshed_from_record": record_id,
        "event": event,
        "event_path": event_path.relative_to(root).as_posix(),
    }, []


def refresh_stale_knowledge_candidates(root: Path, *, repo_id: str, include_records: bool = False) -> tuple[dict[str, Any], list[Problem]]:
    directory = _candidate_dir(root, repo_id)
    candidates = [_read_candidate(path) for path in sorted(directory.glob("KC-*.json"))] if directory.exists() else []
    refreshed_before = _refreshed_candidate_ids(root, repo_id=repo_id)
    review_states = _candidate_review_states(root, repo_id=repo_id)
    refreshed_candidates: list[dict[str, Any]] = []
    skipped_candidates: list[dict[str, Any]] = []
    problems: list[Problem] = []
    for candidate in candidates:
        candidate_id = str(candidate.get("id") or "")
        if candidate_id in refreshed_before:
            skipped_candidates.append({"candidate_id": candidate_id, "reason": "already_refreshed"})
            continue
        review_state = review_states.get(candidate_id, "pending")
        if review_state != "pending":
            skipped_candidates.append({"candidate_id": candidate_id, "reason": "not_pending", "review_state": review_state})
            continue
        quality_problems = _candidate_quality_problems(root, candidate)
        has_source_problem = any(problem.code in {"knowledge_source_digest_drift", "knowledge_source_missing", "knowledge_source_refs_missing"} for problem in quality_problems)
        hard_errors = [problem for problem in quality_problems if problem.severity == "error" and problem.code != "knowledge_source_digest_drift"]
        if not has_source_problem:
            skipped_candidates.append({"candidate_id": candidate_id, "reason": "not_stale"})
            continue
        if hard_errors:
            skipped_candidates.append({"candidate_id": candidate_id, "reason": "blocked_by_non_drift_errors", "problem_codes": _problem_code_counts(hard_errors)})
            problems.extend(hard_errors)
            continue
        refreshed_data, refresh_problems = refresh_knowledge_candidate(root, repo_id=repo_id, candidate_id=candidate_id)
        if refresh_problems:
            skipped_candidates.append({"candidate_id": candidate_id, "reason": "refresh_failed", "problem_codes": _problem_code_counts(refresh_problems)})
            problems.extend(refresh_problems)
            continue
        refreshed_candidates.append(
            {
                "candidate_id": candidate_id,
                "new_candidate_id": refreshed_data["candidate"].get("id", ""),
                "event_id": refreshed_data["event"].get("id", ""),
            }
        )
    refreshed_records: list[dict[str, Any]] = []
    skipped_records: list[dict[str, Any]] = []
    if include_records:
        refreshed_record_ids = _refreshed_record_ids(root, repo_id=repo_id)
        records = [record for record in _load_records(root) if str(record.get("repo_id") or "") == repo_id]
        for record in records:
            record_id = str(record.get("id") or "")
            record_problems = _source_digest_problems(root, record, record_id=record_id)
            has_source_problem = any(problem.code in {"knowledge_source_digest_drift", "knowledge_source_missing", "knowledge_source_refs_missing"} for problem in record_problems)
            hard_errors = [problem for problem in record_problems if problem.severity == "error" and problem.code != "knowledge_source_digest_drift"]
            if not has_source_problem:
                skipped_records.append({"record_id": record_id, "reason": "not_stale"})
                continue
            if record_id in refreshed_record_ids:
                skipped_records.append({"record_id": record_id, "reason": "already_refreshed"})
                continue
            if hard_errors:
                skipped_records.append({"record_id": record_id, "reason": "blocked_by_non_drift_errors", "problem_codes": _problem_code_counts(hard_errors)})
                problems.extend(hard_errors)
                continue
            refreshed_data, refresh_problems = refresh_knowledge_record_candidate(root, repo_id=repo_id, record_id=record_id)
            if refresh_problems:
                skipped_records.append({"record_id": record_id, "reason": "refresh_failed", "problem_codes": _problem_code_counts(refresh_problems)})
                problems.extend(refresh_problems)
                continue
            refreshed_records.append(
                {
                    "record_id": record_id,
                    "new_candidate_id": refreshed_data["candidate"].get("id", ""),
                    "event_id": refreshed_data["event"].get("id", ""),
                }
            )
    return {
        "schema": "repoctl.knowledge.candidate_refresh_all_stale",
        "schema_version": 1,
        "repo_id": repo_id,
        "include_records": include_records,
        "candidate_count": len(candidates),
        "refreshed_count": len(refreshed_candidates) + len(refreshed_records),
        "skipped_count": len(skipped_candidates) + len(skipped_records),
        "refreshed": refreshed_candidates,
        "skipped": skipped_candidates,
        "refreshed_candidates": refreshed_candidates,
        "skipped_candidates": skipped_candidates,
        "refreshed_records": refreshed_records,
        "skipped_records": skipped_records,
    }, problems


def approve_knowledge_candidate(
    root: Path,
    *,
    repo_id: str,
    candidate_id: str,
    supersedes: list[str] | None = None,
    reviewed_by: str = "human",
    review_note: str = "",
) -> tuple[dict[str, Any], list[Problem]]:
    candidate_data, problems = show_knowledge_candidate(root, repo_id=repo_id, candidate_id=candidate_id)
    if problems:
        return {}, problems
    candidate = candidate_data["candidate"]
    quality_results = _candidate_quality_problems(root, candidate)
    quality_problems = [problem for problem in quality_results if problem.severity == "error"]
    if quality_problems:
        return {}, quality_problems
    supersedes = supersedes or _default_supersedes_for_candidate(candidate)
    relation_problems = _validate_supersedes(root, repo_id=repo_id, supersedes=supersedes)
    if relation_problems:
        return {}, relation_problems
    record_id = "K" + candidate_id[2:]
    if record_id in supersedes:
        return {}, [Problem("error", "knowledge_supersedes_self", "knowledge record cannot supersede itself", record_id)]
    reviewer = reviewed_by.strip() or "human"
    note = review_note.strip()
    source_digest_set = sorted(
        str(ref.get("content_sha256") or "")
        for ref in candidate.get("source_refs", [])
        if isinstance(ref, dict) and str(ref.get("content_sha256") or "")
    )
    approved_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
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
        "created_from": {
            "candidate_id": candidate_id,
            "candidate_digest": candidate.get("candidate_digest", ""),
            "candidate_derived_from": candidate.get("derived_from", {}) if isinstance(candidate.get("derived_from"), dict) else {},
            "candidate_check": {
                "passed": True,
                "warning_codes": sorted(problem.code for problem in quality_results if problem.severity == "warning"),
                "related_records": _candidate_related_records(root, candidate),
            },
        },
        "review": {
            "status": "reviewed",
            "reviewed_by": reviewer,
            "review_note": note,
            "reviewed_at": approved_at,
            "source_digest_set": source_digest_set,
        },
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
        "reviewed_by": reviewer,
        "review_note": note,
        "approved_at": approved_at,
        "source_digest_set": source_digest_set,
        "supersedes": supersedes,
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
            "approved_event_id": event["id"],
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


def deprecate_knowledge_record(root: Path, *, repo_id: str, record_id: str, reason_file: Path) -> tuple[dict[str, Any], list[Problem]]:
    record_data, problems = show_knowledge_record(root, record_id=record_id, repo_id=repo_id)
    if problems:
        return {}, problems
    reason_path = reason_file if reason_file.is_absolute() else root / reason_file
    if not reason_path.is_file():
        return {}, [Problem("error", "knowledge_deprecate_reason_missing", "deprecation reason file is missing", reason_path.as_posix())]
    reason = reason_path.read_text(encoding="utf-8").strip()
    if not reason:
        return {}, [Problem("error", "knowledge_deprecate_reason_empty", "deprecation reason file is empty", reason_path.as_posix())]
    if record_id in _deprecated_ids(root, repo_id=repo_id):
        return {}, [Problem("error", "knowledge_record_already_deprecated", "knowledge record is already deprecated", record_id)]
    record = record_data["record"]
    event = {
        "schema": "repoctl.knowledge.event",
        "schema_version": 1,
        "id": _event_id("deprecated", record_id),
        "type": "deprecated",
        "repo_id": repo_id,
        "record_id": record_id,
        "record_digest": record.get("record_digest", ""),
        "reason": reason,
    }
    event["event_digest"] = digest_data(event)
    event_path = _event_dir(root) / f"{event['id']}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    atomic_write(event_path, json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return {"event": event, "event_path": event_path.relative_to(root).as_posix()}, []


def show_knowledge_record(root: Path, *, record_id: str, repo_id: str) -> tuple[dict[str, Any], list[Problem]]:
    if not re.fullmatch(r"K-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*", record_id):
        return {}, [Problem("error", "invalid_knowledge_record_id", "record id must look like K-YYYYMMDDHHMMSSZ--slug")]
    path = _record_dir(root) / f"{record_id}.json"
    if not path.is_file():
        return {}, [Problem("error", "knowledge_record_not_found", f"knowledge record not found: {record_id}", path.relative_to(root).as_posix())]
    record = _read_candidate(path)
    if str(record.get("repo_id") or "") != repo_id:
        return {}, [Problem("error", "knowledge_record_repo_mismatch", "knowledge record belongs to a different repo", record_id)]
    return {"record": record, "path": path.relative_to(root).as_posix()}, []


def _problem_code_counts(problems: list[Problem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for problem in problems:
        counts[problem.code] = counts.get(problem.code, 0) + 1
    return dict(sorted(counts.items()))


def check_knowledge_records(root: Path, *, repo_id: str) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    records = [_read_candidate(path) for path in sorted(_record_dir(root).glob("K-*.json"))]
    selected = [record for record in records if str(record.get("repo_id") or "") == repo_id]
    superseded_ids = _superseded_ids(selected)
    deprecated_ids = _deprecated_ids(root, repo_id=repo_id)
    record_results: list[dict[str, Any]] = []
    for record in selected:
        record_id = str(record.get("id") or "")
        status = _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
        record_problems = [] if status in {"superseded", "deprecated"} else _source_digest_problems(root, record, record_id=record_id)
        problems.extend(record_problems)
        record_results.append(
            {
                "id": record_id,
                "kind": record.get("kind", ""),
                "status": status,
                "title": record.get("title", ""),
                "source_statuses": _source_ref_statuses(root, record),
                "error_count": len([problem for problem in record_problems if problem.severity == "error"]),
                "warning_count": len([problem for problem in record_problems if problem.severity == "warning"]),
                "problem_codes": _problem_code_counts(record_problems),
            }
        )
    supersession_problems = _supersession_problems(selected)
    event_problems = event_integrity_problems(root, repo_id=repo_id, records=selected)
    problems.extend(supersession_problems)
    problems.extend(event_problems)
    return {
        "schema": "repoctl.knowledge.check",
        "schema_version": 1,
        "repo_id": repo_id,
        "record_count": len(selected),
        "event_count": len(_load_events(root, repo_id=repo_id)),
        "records": record_results,
        "record_checks": {
            "error_count": len([problem for problem in problems if problem.severity == "error" and not problem.code.startswith("knowledge_event_")]),
            "warning_count": len([problem for problem in problems if problem.severity == "warning" and not problem.code.startswith("knowledge_event_")]),
            "problem_codes": _problem_code_counts([problem for problem in problems if not problem.code.startswith("knowledge_event_")]),
        },
        "event_checks": {
            "error_count": len([problem for problem in event_problems if problem.severity == "error"]),
            "warning_count": len([problem for problem in event_problems if problem.severity == "warning"]),
            "problem_codes": _problem_code_counts(event_problems),
        },
    }, problems


def query_knowledge_records(root: Path, *, repo_id: str, query: str, include_stale: bool = False, include_superseded: bool = False, include_deprecated: bool = False, limit: int = 10, explain: bool = False) -> tuple[dict[str, Any], list[Problem], list[Problem]]:
    problems: list[Problem] = []
    warnings: list[Problem] = []
    records = [record for record in _load_records(root) if str(record.get("repo_id") or "") == repo_id]
    events = _load_events(root, repo_id=repo_id)
    event_problems = event_integrity_problems(root, repo_id=repo_id, records=records)
    if event_problems:
        return {
            "schema": "repoctl.knowledge.query",
            "schema_version": 1,
            "repo_id": repo_id,
            "query": {"text": query, "include_stale": include_stale, "include_superseded": include_superseded, "include_deprecated": include_deprecated, "explain": explain},
            "lifecycle": {
                "available_statuses": {},
                "excluded_statuses": {},
                "returned_statuses": {},
                "default_excludes": ["stale", "superseded", "deprecated"],
                "event_checks": {"error_count": len(event_problems)},
            },
            "results": [],
            "result_count": 0,
            "available_record_count": len(records),
        }, event_problems, warnings
    superseded_ids = _superseded_ids(records)
    deprecated_ids = _deprecated_ids(root, repo_id=repo_id)
    available_statuses: dict[str, int] = {}
    excluded_statuses: dict[str, int] = {}
    scored: list[dict[str, Any]] = []
    for record in records:
        status = _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids)
        available_statuses[status] = available_statuses.get(status, 0) + 1
        if status == "stale" and not include_stale:
            excluded_statuses[status] = excluded_statuses.get(status, 0) + 1
            warnings.append(Problem("warning", "knowledge_stale_record_excluded", "stale knowledge record excluded from default query", str(record.get("id") or "")))
            continue
        if status == "superseded" and not include_superseded:
            excluded_statuses[status] = excluded_statuses.get(status, 0) + 1
            warnings.append(Problem("warning", "knowledge_superseded_record_excluded", "superseded knowledge record excluded from default query", str(record.get("id") or "")))
            continue
        if status == "deprecated" and not include_deprecated:
            excluded_statuses[status] = excluded_statuses.get(status, 0) + 1
            warnings.append(Problem("warning", "knowledge_deprecated_record_excluded", "deprecated knowledge record excluded from default query", str(record.get("id") or "")))
            continue
        if status not in {"reviewed", "stale", "superseded", "deprecated"}:
            continue
        score, breakdown, reasons = _record_score(query, record)
        if score <= 0:
            continue
        item = {
            "record": _public_record(record, status=status, lifecycle_relations=_record_lifecycle_relations(record, events)),
            "score": round(score, 6),
            "score_breakdown": {key: round(value, 6) for key, value in sorted(breakdown.items())},
            "selection_reasons": reasons,
        }
        if explain:
            item["explain"] = {
                "status": status,
                "source_ref_statuses": _source_ref_statuses(root, record),
                "superseded": status == "superseded",
                "stale": status == "stale",
                "deprecated": status == "deprecated",
            }
        scored.append(item)
    scored.sort(key=lambda item: (-float(item["score"]), str(item["record"].get("id") or "")))
    returned = scored[:limit]
    returned_statuses: dict[str, int] = {}
    for item in returned:
        record = item.get("record") if isinstance(item.get("record"), dict) else {}
        status = str(record.get("status") or "")
        if status:
            returned_statuses[status] = returned_statuses.get(status, 0) + 1
    return {
        "schema": "repoctl.knowledge.query",
        "schema_version": 1,
        "repo_id": repo_id,
        "query": {"text": query, "include_stale": include_stale, "include_superseded": include_superseded, "include_deprecated": include_deprecated, "explain": explain},
        "lifecycle": {
            "available_statuses": dict(sorted(available_statuses.items())),
            "excluded_statuses": dict(sorted(excluded_statuses.items())),
            "returned_statuses": dict(sorted(returned_statuses.items())),
            "default_excludes": ["stale", "superseded", "deprecated"],
        },
        "results": returned,
        "result_count": len(returned),
        "available_record_count": len(records),
    }, problems, warnings


def _source_rel(root: Path, source: Path) -> str:
    path = source if source.is_absolute() else root / source
    try:
        return path.resolve().relative_to(root.resolve()).as_posix()
    except ValueError as exc:
        raise RepoctlError("knowledge candidate source must be inside the workspace") from exc


def _read_context_pack_artifact(root: Path, path: Path) -> tuple[dict[str, Any], list[Problem]]:
    if not path.is_file():
        return {}, [Problem("error", "knowledge_candidate_pack_missing", "context pack artifact is missing", path.as_posix())]
    try:
        path.resolve().relative_to(root.resolve())
    except ValueError:
        return {}, [Problem("error", "knowledge_candidate_pack_outside_workspace", "context pack artifact must be inside the workspace", path.as_posix())]
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}, [Problem("error", "knowledge_candidate_pack_invalid_json", "context pack artifact is not valid JSON", path.as_posix())]
    if not isinstance(payload, dict):
        return {}, [Problem("error", "knowledge_candidate_pack_invalid", "context pack artifact must be an object", path.as_posix())]
    if str(payload.get("command") or "") == "context pack" and payload.get("ok") is False:
        return {}, [Problem("error", "knowledge_candidate_pack_failed", "failed context pack artifact cannot be used for knowledge candidate creation", path.as_posix())]
    data = payload.get("data") if str(payload.get("command") or "") == "context pack" else payload
    if not isinstance(data, dict) or not isinstance(data.get("groups"), dict):
        return {}, [Problem("error", "knowledge_candidate_pack_invalid_data", "context pack artifact is missing groups", path.as_posix())]
    expected_digest = str(data.get("pack_digest") or "")
    digest_basis = {key: value for key, value in data.items() if key not in {"pack_digest", "artifact", "repository", "graph"}}
    actual_digest = digest_data(digest_basis)
    if expected_digest != actual_digest:
        return {}, [Problem("error", "knowledge_candidate_pack_digest_mismatch", "context pack artifact digest does not match its content", path.as_posix())]
    if path.resolve().is_relative_to((root / "docs/knowledge/generated").resolve()):
        return {}, [Problem("error", "knowledge_candidate_pack_generated", "generated wiki output cannot be used as a context pack artifact", path.as_posix())]
    return data, []


def _pack_authority_source_ref(root: Path, pack_data: dict[str, Any], kind: str) -> tuple[dict[str, Any], Problem | None]:
    groups = pack_data.get("groups") if isinstance(pack_data.get("groups"), dict) else {}
    must_read = groups.get("must_read")
    if not isinstance(must_read, list):
        return {}, Problem("error", "knowledge_candidate_pack_sources_missing", "context pack has no must_read sources")
    candidates: list[dict[str, Any]] = []
    for item in must_read:
        if not isinstance(item, dict):
            continue
        ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        rel = str(ref.get("path") or "")
        if _validate_source(root, rel) is not None:
            continue
        if str(ref.get("kind") or "document") not in {"document", "authority_document"}:
            continue
        expected = str(ref.get("content_sha256") or "")
        path = root / rel
        if expected != _sha256_text(path.read_text(encoding="utf-8")):
            return {}, Problem("error", "knowledge_candidate_pack_source_drift", "context pack source ref digest no longer matches source file", rel)
        candidates.append(ref)
    if not candidates:
        return {}, Problem("error", "knowledge_candidate_pack_authority_source_missing", "context pack has no eligible authority source refs")
    preferred_sections = {
        "decision": {"Decision", "Authority Rules"},
        "invariant": {"Invariant", "Invariants", "Authority Rules"},
        "failure_mode": {"Failure Mode", "Failure Modes", "Known failure modes"},
    }[kind]
    for ref in candidates:
        if str(ref.get("section") or "") in preferred_sections:
            return ref, None
    return candidates[0], None


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


def _receipt_changed_files(receipt: dict[str, Any]) -> list[str]:
    changed_entries = receipt.get("changed_entries")
    if not isinstance(changed_entries, list):
        return []
    paths: list[str] = []
    for item in changed_entries:
        if not isinstance(item, dict):
            continue
        path = str(item.get("path") or "")
        if path:
            paths.append(path)
    return sorted(dict.fromkeys(paths))


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
    if not _knowledge_candidate_id_exists(root, base):
        return base
    for index in range(2, 100):
        candidate = f"{base}-{index}"
        if not _knowledge_candidate_id_exists(root, candidate):
            return candidate
    raise RepoctlError("could not allocate unique knowledge candidate id")


def _knowledge_candidate_id_exists(root: Path, candidate_id: str) -> bool:
    state_root = root / ".repoctl-state/knowledge/candidates"
    if state_root.exists() and any(path.is_file() for path in state_root.glob(f"*/{candidate_id}.json")):
        return True
    record_id = "K" + candidate_id[2:] if candidate_id.startswith("KC-") else candidate_id
    return (_record_dir(root) / f"{record_id}.json").exists()


def _candidate_id(title: str, source_digest: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", title.lower()).strip("-") or "candidate"
    suffix = re.sub(r"[^a-f0-9]", "", source_digest.lower())[:8] or "candidate"
    return f"KC-{stamp}--{slug[:48].strip('-')}-{suffix}"


def _event_id(kind: str, target_id: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", target_id.lower()).strip("-") or kind
    return f"E-{stamp}--{kind}-{slug[:48].strip('-')}"


def _unique_event_id(root: Path, kind: str, target_id: str) -> str:
    base = _event_id(kind, target_id)
    directory = _event_dir(root)
    if not (directory / f"{base}.json").exists():
        return base
    for index in range(2, 100):
        candidate = f"{base}-{index}"
        if not (directory / f"{candidate}.json").exists():
            return candidate
    raise RepoctlError("could not allocate unique knowledge event id")


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


def _candidate_review_states(root: Path, *, repo_id: str) -> dict[str, str]:
    states: dict[str, str] = {}
    for event in _load_events(root, repo_id=repo_id):
        candidate_id = str(event.get("candidate_id") or "")
        if not candidate_id:
            continue
        event_type = str(event.get("type") or "")
        if event_type == "approved":
            states[candidate_id] = "approved"
        elif event_type == "rejected_candidate":
            if states.get(candidate_id) != "approved":
                states[candidate_id] = "rejected"
        elif event_type == "refreshed_candidate":
            states.setdefault(candidate_id, "refreshed")
    return states


def _event_summary(event: dict[str, Any]) -> dict[str, Any]:
    summary = {
        "id": event.get("id", ""),
        "type": event.get("type", ""),
        "repo_id": event.get("repo_id", ""),
        "candidate_id": event.get("candidate_id", ""),
        "record_id": event.get("record_id", ""),
        "event_digest": event.get("event_digest", ""),
    }
    for key in ("new_candidate_id", "superseded_by"):
        if event.get(key):
            summary[key] = event.get(key)
    return summary


def _refreshed_candidate_ids(root: Path, *, repo_id: str) -> set[str]:
    refreshed: set[str] = set()
    for event in _load_events(root, repo_id=repo_id):
        if event.get("type") == "refreshed_candidate":
            candidate_id = str(event.get("candidate_id") or "")
            if candidate_id:
                refreshed.add(candidate_id)
    return refreshed


def _refreshed_record_ids(root: Path, *, repo_id: str) -> set[str]:
    refreshed: set[str] = set()
    for event in _load_events(root, repo_id=repo_id):
        if event.get("type") == "refreshed_record_candidate":
            record_id = str(event.get("record_id") or "")
            if record_id:
                refreshed.add(record_id)
    return refreshed


def _default_supersedes_for_candidate(candidate: dict[str, Any]) -> list[str]:
    derived = candidate.get("derived_from")
    if not isinstance(derived, dict) or str(derived.get("kind") or "") != "knowledge_record":
        return []
    record_id = str(derived.get("record_id") or "")
    return [record_id] if record_id else []


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


def _deprecated_ids(root: Path, *, repo_id: str) -> set[str]:
    values: set[str] = set()
    for event in _load_events(root, repo_id=repo_id):
        if event.get("type") == "deprecated":
            record_id = str(event.get("record_id") or "")
            if record_id:
                values.add(record_id)
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


def event_integrity_problems(root: Path, *, repo_id: str, records: list[dict[str, Any]]) -> list[Problem]:
    problems: list[Problem] = []
    by_id = {str(record.get("id") or ""): record for record in records}
    for event in _load_events(root, repo_id=repo_id):
        event_id = str(event.get("id") or "")
        expected_digest = str(event.get("event_digest") or "")
        actual_digest = digest_data({key: value for key, value in event.items() if key != "event_digest"})
        if expected_digest != actual_digest:
            problems.append(Problem("error", "knowledge_event_digest_mismatch", "knowledge event digest does not match event content", event_id))
            continue
        event_type = str(event.get("type") or "")
        if event_type in {"approved", "deprecated"}:
            record_id = str(event.get("record_id") or "")
            record = by_id.get(record_id)
            if record is None:
                problems.append(Problem("error", "knowledge_event_record_missing", "knowledge event references a missing record", record_id or event_id))
                continue
            if str(event.get("record_digest") or "") != str(record.get("record_digest") or ""):
                problems.append(Problem("error", "knowledge_event_record_digest_mismatch", "knowledge event record digest does not match current record", record_id))
        elif event_type == "superseded":
            record_id = str(event.get("record_id") or "")
            superseded_by = str(event.get("superseded_by") or "")
            if record_id not in by_id:
                problems.append(Problem("error", "knowledge_event_record_missing", "knowledge superseded event references a missing record", record_id or event_id))
            replacement = by_id.get(superseded_by)
            if replacement is None:
                problems.append(Problem("error", "knowledge_event_superseded_by_missing", "knowledge superseded event references a missing replacement record", superseded_by or event_id))
            elif str(event.get("record_digest") or "") != str(replacement.get("record_digest") or ""):
                problems.append(Problem("error", "knowledge_event_record_digest_mismatch", "knowledge superseded event digest does not match replacement record", superseded_by))
        elif event_type in {"rejected_candidate", "refreshed_candidate"}:
            continue
        elif event_type == "refreshed_record_candidate":
            record_id = str(event.get("record_id") or "")
            record = by_id.get(record_id)
            if record is None:
                problems.append(Problem("error", "knowledge_event_record_missing", "knowledge refreshed-record event references a missing record", record_id or event_id))
                continue
            if str(event.get("record_digest") or "") != str(record.get("record_digest") or ""):
                problems.append(Problem("error", "knowledge_event_record_digest_mismatch", "knowledge refreshed-record event digest does not match record", record_id))
        else:
            problems.append(Problem("error", "knowledge_event_type_unknown", "knowledge event type is unknown", event_id))
    return problems


def _derived_status(root: Path, record: dict[str, Any], *, superseded_ids: set[str], deprecated_ids: set[str] | None = None) -> str:
    record_id = str(record.get("id") or "")
    if record_id in superseded_ids:
        return "superseded"
    if deprecated_ids and record_id in deprecated_ids:
        return "deprecated"
    if _source_digest_problems(root, record):
        return "stale"
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


def _source_ref_statuses(root: Path, data: dict[str, Any]) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    refs = data.get("source_refs", [])
    if not isinstance(refs, list):
        return statuses
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        rel = str(ref.get("path") or "")
        expected = str(ref.get("content_sha256") or "")
        path = root / rel
        exists = path.is_file()
        actual = ""
        if exists:
            actual = "sha256:" + hashlib.sha256(path.read_text(encoding="utf-8").encode("utf-8")).hexdigest()
        statuses.append(
            {
                "path": rel,
                "kind": str(ref.get("kind") or ""),
                "section": str(ref.get("section") or ""),
                "exists": exists,
                "expected_sha256": expected,
                "actual_sha256": actual,
                "digest_matches": bool(expected) and expected == actual,
            }
        )
    return statuses


def _candidate_quality_problems(root: Path, candidate: dict[str, Any]) -> list[Problem]:
    problems: list[Problem] = []
    candidate_id = str(candidate.get("id") or "")
    if candidate.get("schema") != "repoctl.knowledge.candidate" or candidate.get("schema_version") != 1:
        problems.append(Problem("error", "knowledge_candidate_schema_invalid", "candidate schema is invalid", candidate_id))
    if candidate.get("authoritative") is not False:
        problems.append(Problem("error", "knowledge_candidate_authoritative", "candidate must be non-authoritative", candidate_id))
    if candidate.get("status") != "candidate":
        problems.append(Problem("error", "knowledge_candidate_status_invalid", "candidate status must be candidate", candidate_id))
    if str(candidate.get("kind") or "") not in ALLOWED_KINDS:
        problems.append(Problem("error", "knowledge_candidate_kind_invalid", "candidate kind is invalid", candidate_id))
    claim = str(candidate.get("claim") or "").strip()
    if not claim:
        problems.append(Problem("error", "knowledge_candidate_claim_missing", "candidate claim is missing", candidate_id))
    if len(claim) > 300:
        problems.append(Problem("error", "knowledge_candidate_claim_too_long", "candidate claim is too long", candidate_id))
    review = candidate.get("review")
    if not isinstance(review, dict) or review.get("required") is not True:
        problems.append(Problem("error", "knowledge_candidate_review_not_required", "candidate must require explicit review", candidate_id))
    refs = candidate.get("source_refs")
    if not isinstance(refs, list) or not refs:
        problems.append(Problem("error", "knowledge_candidate_source_refs_missing", "candidate source refs are missing", candidate_id))
    else:
        for ref in refs:
            if not isinstance(ref, dict):
                problems.append(Problem("error", "knowledge_candidate_source_ref_invalid", "candidate source ref is invalid", candidate_id))
                continue
            rel = str(ref.get("path") or "")
            ref_kind = str(ref.get("kind") or "document")
            if ref_kind in {"document", "authority_document"} and _source_ref_excluded(rel):
                problems.append(Problem("error", "knowledge_candidate_source_excluded", "candidate source is excluded from knowledge ingestion", rel))
            if not str(ref.get("content_sha256") or "").startswith("sha256:"):
                problems.append(Problem("error", "knowledge_candidate_source_hash_invalid", "candidate source hash is invalid", rel))
    problems.extend(_source_digest_problems(root, candidate, record_id=candidate_id))
    problems.extend(_context_pack_provenance_warnings(root, candidate))
    duplicate = _duplicate_reviewed_claim(root, candidate)
    if duplicate:
        problems.append(Problem("warning", "knowledge_candidate_duplicate_reviewed_claim", f"candidate claim already exists in reviewed record {duplicate}", duplicate))
    return problems


def _context_pack_provenance_warnings(root: Path, candidate: dict[str, Any]) -> list[Problem]:
    derived = candidate.get("derived_from")
    if not isinstance(derived, dict) or str(derived.get("kind") or "") != "context_pack":
        return []
    rel = str(derived.get("path") or "")
    expected = str(derived.get("pack_digest") or "")
    if not rel or not expected:
        return [Problem("warning", "knowledge_candidate_pack_provenance_incomplete", "context pack provenance is incomplete", str(candidate.get("id") or ""))]
    path = root / rel
    if not path.is_file():
        return [Problem("warning", "knowledge_candidate_pack_provenance_missing", "context pack provenance artifact is missing", rel)]
    pack_data, pack_problems = _read_context_pack_artifact(root, path)
    if pack_problems:
        return [Problem("warning", "knowledge_candidate_pack_provenance_invalid", "context pack provenance artifact is invalid", rel)]
    actual = str(pack_data.get("pack_digest") or "")
    if actual != expected:
        return [Problem("warning", "knowledge_candidate_pack_provenance_drift", "context pack provenance digest changed", rel)]
    return []


def _candidate_check_flags(candidate: dict[str, Any], problems: list[Problem]) -> dict[str, bool]:
    return {
        "schema_valid": not any(problem.code.startswith("knowledge_candidate_schema") for problem in problems),
        "source_refs_valid": not any(problem.code.startswith("knowledge_candidate_source") for problem in problems),
        "digest_current": not any(problem.code == "knowledge_source_digest_drift" for problem in problems),
        "pack_provenance_current": not any(problem.code.startswith("knowledge_candidate_pack_provenance") for problem in problems),
        "review_required": bool(candidate.get("review", {}).get("required")) if isinstance(candidate.get("review"), dict) else False,
        "duplicate_reviewed_claim": any(problem.code == "knowledge_candidate_duplicate_reviewed_claim" for problem in problems),
    }


def _candidate_checks(root: Path, *, repo_id: str, candidates: list[dict[str, Any]]) -> dict[str, Any]:
    results: list[dict[str, Any]] = []
    error_count = 0
    warning_count = 0
    passed_count = 0
    for candidate in candidates:
        candidate_id = str(candidate.get("id") or "")
        problems = _candidate_quality_problems(root, candidate)
        errors = [problem.to_dict() for problem in problems if problem.severity == "error"]
        warnings = [problem.to_dict() for problem in problems if problem.severity == "warning"]
        passed = not errors
        passed_count += 1 if passed else 0
        error_count += len(errors)
        warning_count += len(warnings)
        results.append(
            {
                "candidate_id": candidate_id,
                "passed": passed,
                "checks": _candidate_check_flags(candidate, problems),
                "related_records": _candidate_related_records(root, candidate),
                "error_count": len(errors),
                "warning_count": len(warnings),
                "problems": errors,
                "warnings": warnings,
            }
        )
    return {
        "schema": "repoctl.knowledge.candidate_check_all",
        "schema_version": 1,
        "repo_id": repo_id,
        "candidate_count": len(candidates),
        "passed_count": passed_count,
        "error_count": error_count,
        "warning_count": warning_count,
        "results": results,
    }


def _source_ref_excluded(rel: str) -> bool:
    parts = set(Path(rel).parts)
    if parts & EXCLUDED_SOURCE_PARTS:
        return True
    if rel.startswith("docs/knowledge/generated/"):
        return True
    return False


def _refresh_source_path(candidate: dict[str, Any]) -> str:
    refs = candidate.get("source_refs", [])
    if not isinstance(refs, list):
        return ""
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        rel = str(ref.get("path") or "")
        if str(ref.get("kind") or "document") in {"document", "authority_document"} and rel.startswith(ALLOWED_SOURCE_PREFIXES) and not _source_ref_excluded(rel):
            return rel
    return ""


def _duplicate_reviewed_claim(root: Path, candidate: dict[str, Any]) -> str:
    related = _candidate_related_records(root, candidate)
    for item in related:
        if item.get("relation") == "same_claim":
            return str(item.get("record_id") or "")
    return ""


def _candidate_related_records(root: Path, candidate: dict[str, Any]) -> list[dict[str, str]]:
    candidate_claim = str(candidate.get("claim") or "").strip().casefold()
    if not candidate_claim:
        return []
    repo_id = str(candidate.get("repo_id") or "")
    records = [record for record in _load_records(root) if str(record.get("repo_id") or "") == repo_id]
    superseded_ids = _superseded_ids(records)
    deprecated_ids = _deprecated_ids(root, repo_id=repo_id)
    related: list[dict[str, str]] = []
    for record in records:
        if str(record.get("claim") or "").strip().casefold() != candidate_claim:
            continue
        related.append(
            {
                "record_id": str(record.get("id") or ""),
                "status": _derived_status(root, record, superseded_ids=superseded_ids, deprecated_ids=deprecated_ids),
                "relation": "same_claim",
            }
        )
    return sorted(related, key=lambda item: (item["status"], item["record_id"]))


def _public_record(record: dict[str, Any], *, status: str, lifecycle_relations: dict[str, Any] | None = None) -> dict[str, Any]:
    public = {
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
    if lifecycle_relations:
        public["lifecycle_relations"] = lifecycle_relations
    approval_context = _approval_context(record)
    if approval_context:
        public["approval_context"] = approval_context
    return public


def _record_lifecycle_relations(record: dict[str, Any], events: list[dict[str, Any]]) -> dict[str, Any]:
    record_id = str(record.get("id") or "")
    supersedes = record.get("supersedes", [])
    relations = {
        "supersedes": [str(item) for item in supersedes if str(item)] if isinstance(supersedes, list) else [],
        "superseded_by": [],
        "deprecated_by": [],
    }
    for event in events:
        if event.get("type") == "superseded" and str(event.get("record_id") or "") == record_id and event.get("superseded_by"):
            relations["superseded_by"].append(str(event.get("superseded_by") or ""))
        if event.get("type") == "deprecated" and str(event.get("record_id") or "") == record_id:
            relations["deprecated_by"].append(str(event.get("id") or ""))
    return {key: sorted(value) for key, value in relations.items() if value}


def _approval_context(record: dict[str, Any]) -> dict[str, Any]:
    created_from = record.get("created_from")
    if not isinstance(created_from, dict):
        return {}
    candidate_check = created_from.get("candidate_check")
    if not isinstance(candidate_check, dict):
        candidate_check = {}
    warning_codes = candidate_check.get("warning_codes")
    related_records = candidate_check.get("related_records")
    return {
        "candidate_id": created_from.get("candidate_id", ""),
        "candidate_digest": created_from.get("candidate_digest", ""),
        "warning_codes": warning_codes if isinstance(warning_codes, list) else [],
        "related_records": related_records if isinstance(related_records, list) else [],
    }


def _record_score(query: str, record: dict[str, Any]) -> tuple[float, dict[str, float], list[str]]:
    identity_text = "\n".join([str(record.get("id") or ""), str(record.get("kind") or "")])
    title_text = str(record.get("title") or "")
    claim_text = str(record.get("claim") or "")
    summary_text = str(record.get("summary") or "")
    source_text = json.dumps(record.get("source_refs", []), ensure_ascii=False, sort_keys=True)
    exact_identity = _exact_score(query, identity_text)
    exact_title = _exact_score(query, title_text)
    exact_claim = _exact_score(query, claim_text)
    exact_summary = _exact_score(query, summary_text)
    exact_source = _exact_score(query, source_text)
    body = "\n".join([identity_text, title_text, claim_text, summary_text, source_text])
    fts = _fts_score(query, body)
    authority = 0.5 if str(record.get("status") or "") == "reviewed" else 0.0
    score = exact_identity * 1.0 + exact_title * 2.4 + exact_claim * 2.0 + exact_summary * 1.2 + exact_source * 0.8 + fts * 1.2 + authority
    reasons: list[str] = []
    if exact_identity:
        reasons.append("exact record identity match")
    if exact_title:
        reasons.append("exact title match")
    if exact_claim:
        reasons.append("exact claim match")
    if exact_summary:
        reasons.append("exact summary match")
    if exact_source:
        reasons.append("exact source reference match")
    if fts:
        reasons.append("SQLite FTS record match")
    if authority:
        reasons.append("reviewed knowledge record")
    return score, {
        "exact_identity": exact_identity,
        "exact_title": exact_title,
        "exact_claim": exact_claim,
        "exact_summary": exact_summary,
        "exact_source": exact_source,
        "fts": fts,
        "authority": authority,
    }, reasons


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

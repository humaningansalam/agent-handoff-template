from __future__ import annotations

import json
import hashlib
import re
import sqlite3
from dataclasses import dataclass
from datetime import UTC, datetime
from enum import StrEnum
from pathlib import Path
from typing import Any

from .graph_model import digest_data
from .io import RepoctlError, atomic_write
from .knowledge_projection import (
    KNOWLEDGE_RECORD_SCHEMA_VERSION,
    knowledge_projection_path,
    load_knowledge_projection,
    rebuild_knowledge_projection,
    verify_current_knowledge_projection,
)
from .repositories import RepoSelectorStatus, require_repo_target, resolve_repo_selector_path
from .tasks import Problem


ALLOWED_KINDS = {"decision", "invariant", "failure_mode"}
ALLOWED_SOURCE_PREFIXES = ("docs/adr/", "docs/contracts/", "docs/workflows/")
EXCLUDED_SOURCE_PARTS = {".repoctl-state", "generated", "plans"}
MAX_KNOWLEDGE_CLAIM_LENGTH = 300
CLAIM_ORIGINS = {"explicit"}


class KnowledgeExplicitPathKind(StrEnum):
    APPLIES_TO_PATH = "applies_to_path"
    SOURCE_REF = "source_ref"


class KnowledgeExplicitPathRole(StrEnum):
    CODE_ANCHOR = "code_anchor"
    PROVENANCE_ONLY = "provenance_only"


class KnowledgeSourceRefKind(StrEnum):
    CURRENT_SOURCE = "current_source"
    AUTHORITY_DOCUMENT = "authority_document"
    COMPLETION_RECEIPT = "completion_receipt"
    TASK_ARTIFACT = "task_artifact"
    WORKSPACE_FILE = "workspace_file"


class KnowledgeSourceResolutionStatus(StrEnum):
    CURRENT = "current"
    RELOCATED = "relocated"
    MISSING = "missing"
    DIGEST_MISMATCH = "digest_mismatch"
    INVALID_IDENTITY = "invalid_identity"


class KnowledgeQueryMatchStrength(StrEnum):
    NONE = "none"
    WEAK = "weak"
    STRONG = "strong"
    EXACT = "exact"


class KnowledgeArtifactErrorCode(StrEnum):
    RECORD_INVALID_JSON = "knowledge_record_invalid_json"
    RECORD_NOT_OBJECT = "knowledge_record_not_object"
    RECORD_UNREADABLE = "knowledge_record_unreadable"


@dataclass(frozen=True)
class PreparedKnowledgeRecord:
    data: dict[str, Any]
    path: Path
    text: str


@dataclass(frozen=True)
class KnowledgeArtifactRead:
    data: dict[str, Any] | None
    problem: Problem | None


@dataclass(frozen=True)
class KnowledgeSourceResolution:
    status: KnowledgeSourceResolutionStatus
    declared_path: str
    resolved_path: str
    expected_sha256: str
    actual_sha256: str = ""
    cause_code: str = ""


def prepare_knowledge_record(
    root: Path,
    *,
    repo_id: str,
    kind: str,
    claim: str,
    reason: str,
    sources: list[str],
    applies_to: list[str] | None = None,
    replaces: list[str] | None = None,
    author: str = "",
    allow_pending_sources: bool = False,
) -> tuple[PreparedKnowledgeRecord | None, list[Problem]]:
    """Prepare one durable Knowledge record without a candidate lifecycle."""

    if kind not in ALLOWED_KINDS:
        return None, [
            Problem(
                "error",
                "invalid_knowledge_kind",
                f"knowledge kind must be one of {sorted(ALLOWED_KINDS)}",
            )
        ]
    normalized_claim, claim_problem = _validated_claim(
        claim=claim,
        problem_path="",
    )
    if claim_problem is not None:
        return None, [claim_problem]
    normalized_reason = reason.strip()
    if not normalized_reason:
        return None, [
            Problem(
                "error",
                "knowledge_reason_required",
                "knowledge requires a concrete reason",
            )
        ]
    normalized_applies_to, path_problems = _validated_applicability_paths(
        root,
        repo_id=repo_id,
        values=list(applies_to or []),
    )
    if path_problems:
        return None, path_problems
    target = require_repo_target(root, repo_id=repo_id)
    source_refs: list[dict[str, str]] = []
    source_problems: list[Problem] = []
    for raw_source in sources:
        ref, problem = _direct_knowledge_source_ref(
            root,
            target=target,
            raw_source=raw_source,
            allow_pending=allow_pending_sources,
        )
        if problem is not None:
            source_problems.append(problem)
        elif ref not in source_refs:
            source_refs.append(ref)
    if not sources:
        source_problems.append(
            Problem(
                "error",
                "knowledge_source_required",
                "knowledge requires at least one workspace source",
            )
        )
    if source_problems:
        return None, source_problems

    replacement_ids = sorted({str(value).strip() for value in (replaces or []) if str(value).strip()})
    known_records = {
        str(record.get("id") or ""): record
        for record in _load_records(root)
    }
    replacement_problems: list[Problem] = []
    for record_id in replacement_ids:
        record = known_records.get(record_id)
        if record is None:
            replacement_problems.append(
                Problem(
                    "error",
                    "knowledge_replaces_missing",
                    "replacement target does not exist",
                    record_id,
                )
            )
        elif str(record.get("repo_id") or "") != repo_id:
            replacement_problems.append(
                Problem(
                    "error",
                    "knowledge_replaces_repo_mismatch",
                    "replacement target belongs to a different repository",
                    record_id,
                )
            )
    if replacement_problems:
        return None, replacement_problems

    recorded_at = datetime.now(UTC).strftime("%Y-%m-%dT%H:%M:%SZ")
    record_id = _unique_direct_record_id(
        root,
        claim=normalized_claim,
        reason=normalized_reason,
    )
    record: dict[str, Any] = {
        "schema": "repoctl.knowledge.record",
        "schema_version": KNOWLEDGE_RECORD_SCHEMA_VERSION,
        "id": record_id,
        "repo_id": repo_id,
        "kind": kind,
        "claim": normalized_claim,
        "reason": normalized_reason,
        "source_refs": source_refs,
        "applies_to": {"paths": normalized_applies_to},
        "replaces": replacement_ids,
        "recorded_at": recorded_at,
    }
    normalized_author = author.strip()
    if normalized_author:
        record["author"] = normalized_author
    record["record_digest"] = _knowledge_record_digest(record)
    path = _record_dir(root) / f"{record_id}.json"
    return PreparedKnowledgeRecord(
        data=record,
        path=path,
        text=json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
    ), []


def add_knowledge_record(
    root: Path,
    *,
    repo_id: str,
    kind: str,
    claim: str,
    reason: str,
    sources: list[str],
    applies_to: list[str] | None = None,
    replaces: list[str] | None = None,
    author: str = "",
) -> tuple[dict[str, Any], list[Problem], list[Problem]]:
    prepared, problems = prepare_knowledge_record(
        root,
        repo_id=repo_id,
        kind=kind,
        claim=claim,
        reason=reason,
        sources=sources,
        applies_to=applies_to,
        replaces=replaces,
        author=author,
    )
    if prepared is None or problems:
        return {}, problems, []
    if prepared.path.exists():
        return {}, [
            Problem(
                "error",
                "knowledge_record_exists",
                "knowledge record already exists",
                prepared.path.relative_to(root).as_posix(),
            )
        ], []
    atomic_write(prepared.path, prepared.text)
    projection, projection_problems = rebuild_knowledge_projection(
        root,
        repo_id=repo_id,
    )
    warnings = [
        Problem(
            "warning",
            "knowledge_projection_sync_failed",
            "knowledge was saved; refresh the derived Knowledge projection when needed",
            problem.path,
            cause_code=problem.code,
        )
        for problem in projection_problems
    ]
    return {
        "record": prepared.data,
        "record_path": prepared.path.relative_to(root).as_posix(),
        "projection": (
            {
                "status": "synced",
                "path": knowledge_projection_path(root, repo_id=repo_id)
                .relative_to(root)
                .as_posix(),
                "generation": int(projection.get("generation") or 0),
            }
            if not projection_problems
            else {"status": "stale"}
        ),
    }, [], warnings


def _direct_knowledge_source_ref(
    root: Path,
    *,
    target: Any,
    raw_source: str,
    allow_pending: bool,
) -> tuple[dict[str, str], Problem | None]:
    value = str(raw_source).strip().replace("\\", "/")
    candidate = Path(value)
    if (
        not value
        or candidate.is_absolute()
        or candidate.as_posix() != value
        or any(part in {"", ".", ".."} for part in candidate.parts)
    ):
        return {}, Problem(
            "error",
            "knowledge_source_path_invalid",
            "knowledge source must be a canonical workspace-relative path",
            value,
        )
    path = root / candidate
    try:
        root_resolved = root.resolve()
        resolved = path.resolve()
    except (OSError, ValueError, RuntimeError):
        return {}, Problem(
            "error",
            "knowledge_source_path_invalid",
            "knowledge source path is unresolvable",
            value,
        )
    if root_resolved not in (resolved, *resolved.parents):
        return {}, Problem(
            "error",
            "knowledge_source_path_invalid",
            "knowledge source escapes the workspace",
            value,
        )
    if not allow_pending and not path.is_file():
        return {}, Problem(
            "error",
            "knowledge_source_missing",
            "knowledge source file is missing",
            value,
        )
    prefix = target.display_path.rstrip("/") + "/"
    if value.startswith(prefix):
        kind = KnowledgeSourceRefKind.CURRENT_SOURCE.value
    elif value.startswith("docs/archive/task-completions/"):
        kind = KnowledgeSourceRefKind.COMPLETION_RECEIPT.value
    elif value.startswith(("docs/tasks/", "docs/archive/tasks/")):
        kind = KnowledgeSourceRefKind.TASK_ARTIFACT.value
    elif value.startswith(ALLOWED_SOURCE_PREFIXES):
        kind = KnowledgeSourceRefKind.AUTHORITY_DOCUMENT.value
    else:
        kind = KnowledgeSourceRefKind.WORKSPACE_FILE.value
    ref = {"kind": kind, "path": value}
    if path.is_file():
        try:
            ref["content_sha256"] = _sha256_text(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeDecodeError):
            pass
    return ref, None


def _unique_direct_record_id(root: Path, *, claim: str, reason: str) -> str:
    stamp = datetime.now(UTC).strftime("%Y%m%d%H%M%SZ")
    slug = re.sub(r"[^a-z0-9]+", "-", claim.casefold()).strip("-") or "knowledge"
    suffix = hashlib.sha256(f"{claim}\n{reason}".encode("utf-8")).hexdigest()[:8]
    base = f"K-{stamp}--{slug[:40].strip('-')}-{suffix}"
    directory = _record_dir(root)
    if not (directory / f"{base}.json").exists():
        return base
    for index in range(2, 100):
        candidate = f"{base}-{index}"
        if not (directory / f"{candidate}.json").exists():
            return candidate
    raise RepoctlError("could not allocate unique knowledge record id")


def knowledge_status(root: Path, *, repo_id: str) -> dict[str, Any]:
    artifacts = _load_record_artifacts(root)
    records = [
        record
        for record in artifacts
        if record.get("schema_version") == KNOWLEDGE_RECORD_SCHEMA_VERSION
        and str(record.get("repo_id") or "") == repo_id
    ]
    legacy_record_count = len(
        [
            record
            for record in artifacts
            if record.get("schema") == "repoctl.knowledge.record"
            and record.get("schema_version") == 1
            and str(record.get("repo_id") or "") == repo_id
        ]
    )
    superseded_ids = _superseded_ids(records)
    statuses: dict[str, int] = {}
    source_changed = 0
    for record in records:
        status = _derived_status(record, superseded_ids=superseded_ids)
        statuses[status] = statuses.get(status, 0) + 1
        if _source_digest_problems(root, record, record_id=str(record.get("id") or "")):
            source_changed += 1
    projection, projection_problems = load_knowledge_projection(root, repo_id=repo_id)
    return {
        "schema": "repoctl.knowledge.status",
        "schema_version": 2,
        "repo_id": repo_id,
        "record_count": len(records),
        "migration_required_count": legacy_record_count,
        "record_statuses": dict(sorted(statuses.items())),
        "source_changed_count": source_changed,
        "projection": {
            "status": "stale" if projection_problems else "current",
            "head_count": len(projection.get("heads", [])) if not projection_problems else 0,
        },
    }


def show_knowledge_record(root: Path, *, record_id: str, repo_id: str) -> tuple[dict[str, Any], list[Problem]]:
    if not re.fullmatch(r"K-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*", record_id):
        return {}, [Problem("error", "invalid_knowledge_record_id", "record id must look like K-YYYYMMDDHHMMSSZ--slug")]
    path = _record_dir(root) / f"{record_id}.json"
    if not path.is_file():
        return {}, [Problem("error", "knowledge_record_not_found", f"knowledge record not found: {record_id}", path.relative_to(root).as_posix())]
    read = _read_knowledge_artifact(root, path)
    if read.problem is not None:
        return {}, [read.problem]
    record = read.data or {}
    if (
        record.get("schema") != "repoctl.knowledge.record"
        or record.get("schema_version") != KNOWLEDGE_RECORD_SCHEMA_VERSION
    ):
        return {}, [
            Problem(
                "error",
                "knowledge_record_schema_invalid",
                "knowledge record is not in the current format; recreate it with knowledge add",
                path.relative_to(root).as_posix(),
            )
        ]
    if str(record.get("repo_id") or "") != repo_id:
        return {}, [Problem("error", "knowledge_record_repo_mismatch", "knowledge record belongs to a different repo", record_id)]
    problems = _knowledge_record_contract_problems(
        record,
        record_id=record_id,
        repo_id=repo_id,
    )
    return {"record": record, "path": path.relative_to(root).as_posix()}, problems


def _problem_code_counts(problems: list[Problem]) -> dict[str, int]:
    counts: dict[str, int] = {}
    for problem in problems:
        counts[problem.code] = counts.get(problem.code, 0) + 1
    return dict(sorted(counts.items()))


def check_knowledge_records(root: Path, *, repo_id: str) -> tuple[dict[str, Any], list[Problem]]:
    record_paths = sorted(_record_dir(root).glob("K-*.json"))
    records, record_read_problems = _read_knowledge_artifacts(
        root,
        record_paths,
    )
    problems: list[Problem] = list(record_read_problems)
    legacy = [
        record
        for record in records
        if record.get("schema") == "repoctl.knowledge.record"
        and record.get("schema_version") == 1
        and str(record.get("repo_id") or "") == repo_id
    ]
    problems.extend(
        Problem(
            "warning",
            "knowledge_record_migration_required",
            "legacy Knowledge is preserved but inactive; read it and save the reusable claim as a current record",
            f"docs/knowledge/records/{record.get('id', '')}.json",
        )
        for record in legacy
    )
    selected = [
        record
        for record in records
        if record.get("schema_version") == KNOWLEDGE_RECORD_SCHEMA_VERSION
        and str(record.get("repo_id") or "") == repo_id
    ]
    superseded_ids = _superseded_ids(selected)
    record_results: list[dict[str, Any]] = []
    for record in selected:
        record_id = str(record.get("id") or "")
        status = _derived_status(record, superseded_ids=superseded_ids)
        contract_problems = _knowledge_record_contract_problems(record, record_id=record_id, repo_id=repo_id)
        source_warnings = [
            Problem(
                "warning",
                "knowledge_source_changed",
                "a Knowledge source changed or is unavailable; the saved record remains usable",
                problem.path,
                cause_code=problem.code,
            )
            for problem in (
                []
                if status == "superseded"
                else _source_digest_problems(root, record, record_id=record_id)
            )
        ]
        record_problems = [
            *contract_problems,
            *source_warnings,
        ]
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
    problems.extend(supersession_problems)
    return {
        "schema": "repoctl.knowledge.check",
        "schema_version": 1,
        "repo_id": repo_id,
        "record_count": len(selected),
        "legacy_record_count": len(legacy),
        "records": record_results,
        "record_checks": {
            "error_count": len([problem for problem in problems if problem.severity == "error"]),
            "warning_count": len([problem for problem in problems if problem.severity == "warning"]),
            "problem_codes": _problem_code_counts(problems),
        },
    }, problems


def _empty_public_knowledge_query(*, repo_id: str, query: str) -> dict[str, Any]:
    return {
        "schema": "repoctl.knowledge.query",
        "schema_version": 2,
        "repo_id": repo_id,
        "query": {"text": query},
        "lifecycle": {
            "available_statuses": {},
            "excluded_statuses": {},
            "returned_statuses": {},
            "default_excludes": ["superseded"],
        },
        "results": [],
        "result_count": 0,
        "available_record_count": 0,
    }


def _knowledge_query_source_views(
    root: Path,
    records: list[dict[str, Any]],
) -> dict[str, tuple[list[KnowledgeSourceResolution], list[dict[str, Any]]]]:
    views: dict[str, tuple[list[KnowledgeSourceResolution], list[dict[str, Any]]]] = {}
    for record in records:
        record_id = str(record.get("id") or "")
        resolutions = knowledge_source_ref_resolutions(root, record)
        views[record_id] = (
            resolutions,
            resolved_knowledge_source_refs(
                root,
                record,
                resolutions=resolutions,
            ),
        )
    return views


def _knowledge_query_result(
    root: Path,
    *,
    record: dict[str, Any],
    status: str,
    records: list[dict[str, Any]],
    query: str,
    fts_score: float,
    source_resolutions: list[KnowledgeSourceResolution],
    resolved_source_refs: list[dict[str, Any]],
    wanted_paths: set[str],
    require_related: bool,
    explain: bool,
) -> dict[str, Any] | None:
    matched_paths = sorted(
        wanted_paths
        & _record_related_paths(
            record,
            resolved_source_refs=resolved_source_refs,
        )
    )
    if require_related and not matched_paths:
        return None
    score, breakdown, selection_reasons = _record_score(
        query,
        record,
        fts=fts_score,
        resolved_source_refs=resolved_source_refs,
    )
    query_matched = any(
        float(breakdown.get(key) or 0.0) > 0
        for key in (
            "exact_identity",
            "exact_title",
            "exact_claim",
            "exact_summary",
            "exact_source",
            "fts",
        )
    )
    if matched_paths:
        breakdown["path_relation"] = 1.0
        score += 1.0
        selection_reasons.append("explicit source/path relation")
    if not query_matched and not matched_paths:
        return None
    result = {
        "record": _public_record(
            root,
            record,
            status=status,
            lifecycle_relations=_record_lifecycle_relations(
                record,
                records,
            ),
            source_resolutions=source_resolutions,
            resolved_source_refs=resolved_source_refs,
        ),
        "score": round(score, 6),
        "score_breakdown": {
            key: round(value, 6)
            for key, value in sorted(breakdown.items())
        },
        "selection_reasons": selection_reasons,
        "query_match_strength": _knowledge_query_match_strength(
            breakdown,
            query_matched=query_matched,
        ).value,
    }
    if matched_paths:
        result["matched_paths"] = matched_paths
    if explain:
        result["explain"] = {
            "status": status,
            "source_ref_statuses": _source_ref_statuses(
                root,
                record,
                resolutions=source_resolutions,
            ),
            "superseded": status == "superseded",
        }
    return result


def query_knowledge_records(
    root: Path,
    *,
    repo_id: str,
    query: str,
    include_history: bool = False,
    limit: int = 10,
    explain: bool = False,
    related_paths: set[str] | None = None,
    require_related: bool = False,
) -> tuple[dict[str, Any], list[Problem], list[Problem]]:
    wanted_paths = {
        str(path).strip()
        for path in (related_paths or set())
        if str(path).strip()
    }
    problems: list[Problem] = []
    warnings: list[Problem] = []

    if include_history:
        records = [
            record
            for record in _load_records(root)
            if str(record.get("repo_id") or "") == repo_id
        ]
        for record in records:
            problems.extend(
                _knowledge_record_contract_problems(
                    record,
                    record_id=str(record.get("id") or ""),
                    repo_id=repo_id,
                )
            )
        problems.extend(_supersession_problems(records))
        if problems:
            unavailable = _empty_public_knowledge_query(repo_id=repo_id, query=query)
            unavailable["query"]["include_history"] = True
            return unavailable, problems, []
        available_record_count = len(records)
        superseded_ids = _superseded_ids(records)
    else:
        projection, projection_problems = load_knowledge_projection(
            root,
            repo_id=repo_id,
        )
        projection_missing = bool(projection_problems) and all(
            problem.code == "knowledge_projection_unavailable"
            and problem.cause_code == "missing"
            for problem in projection_problems
        )
        if projection_missing and not _load_records(root):
            return _empty_public_knowledge_query(repo_id=repo_id, query=query), [], []
        if projection_problems:
            return (
                _empty_public_knowledge_query(repo_id=repo_id, query=query),
                projection_problems,
                [],
            )
        projection, projection_problems = verify_current_knowledge_projection(
            root,
            repo_id=repo_id,
            projection=projection,
        )
        if projection_problems:
            return (
                _empty_public_knowledge_query(repo_id=repo_id, query=query),
                projection_problems,
                [],
            )
        records = list(projection["heads"])
        lifecycle_counts = projection["lifecycle_counts"]
        available_record_count = int(projection["checkpoint"]["record_count"])
        superseded_ids = set()
        superseded_count = int(lifecycle_counts.get("superseded") or 0)
        if superseded_count:
            warnings.append(
                Problem(
                    "warning",
                    "knowledge_superseded_record_excluded",
                    "superseded knowledge records excluded from default query",
                    repo_id,
                )
            )

    source_views = _knowledge_query_source_views(root, records)
    fts_scores = _record_fts_scores(
        query,
        records,
        resolved_source_refs_by_id={
            record_id: view[1]
            for record_id, view in source_views.items()
        },
    )
    available_statuses: dict[str, int] = {}
    scored: list[dict[str, Any]] = []
    source_changed_count = 0
    for record in records:
        record_id = str(record.get("id") or "")
        status = _derived_status(record, superseded_ids=superseded_ids)
        available_statuses[status] = available_statuses.get(status, 0) + 1
        source_resolutions, resolved_source_refs = source_views[record_id]
        if _source_digest_problems(
            root,
            record,
            record_id=record_id,
            resolutions=source_resolutions,
        ):
            source_changed_count += 1
            warnings.append(
                Problem(
                    "warning",
                    "knowledge_source_changed",
                    "a Knowledge source changed or is unavailable; the saved record remains searchable",
                    record_id,
                )
            )
        item = _knowledge_query_result(
            root,
            record=record,
            status=status,
            records=records,
            query=query,
            fts_score=fts_scores.get(record_id, 0.0),
            source_resolutions=source_resolutions,
            resolved_source_refs=resolved_source_refs,
            wanted_paths=wanted_paths,
            require_related=require_related,
            explain=explain,
        )
        if item is not None:
            scored.append(item)

    if not include_history:
        current_count = int(projection["lifecycle_counts"].get("current") or 0)
        superseded_count = int(projection["lifecycle_counts"].get("superseded") or 0)
        available_statuses = {}
        if current_count:
            available_statuses["reviewed"] = current_count
        if superseded_count:
            available_statuses["superseded"] = superseded_count

    scored.sort(
        key=lambda item: (
            -float(item.get("score") or 0.0),
            str(item.get("record", {}).get("id") or ""),
        )
    )
    returned = scored[: max(0, int(limit))]
    returned_statuses: dict[str, int] = {}
    for item in returned:
        record = item.get("record") if isinstance(item.get("record"), dict) else {}
        status = str(record.get("status") or "")
        if status:
            returned_statuses[status] = returned_statuses.get(status, 0) + 1
    excluded_statuses = (
        {"superseded": int(available_statuses["superseded"])}
        if not include_history and available_statuses.get("superseded")
        else {}
    )
    return {
        "schema": "repoctl.knowledge.query",
        "schema_version": 2,
        "repo_id": repo_id,
        "query": {
            "text": query,
            "include_history": include_history,
            "explain": explain,
            "require_related": require_related,
        },
        "lifecycle": {
            "available_statuses": dict(sorted(available_statuses.items())),
            "excluded_statuses": excluded_statuses,
            "returned_statuses": dict(sorted(returned_statuses.items())),
            "default_excludes": ["superseded"],
            "source_changed_count": source_changed_count,
        },
        "results": returned,
        "result_count": len(returned),
        "available_record_count": available_record_count,
    }, [], warnings

def _knowledge_query_match_strength(
    breakdown: dict[str, float],
    *,
    query_matched: bool,
) -> KnowledgeQueryMatchStrength:
    if float(breakdown.get("exact_identity") or 0.0) >= 1.0:
        return KnowledgeQueryMatchStrength.EXACT
    if any(
        float(breakdown.get(key) or 0.0) >= 1.0
        for key in ("exact_title", "exact_claim", "exact_summary", "exact_source")
    ):
        return KnowledgeQueryMatchStrength.STRONG
    return KnowledgeQueryMatchStrength.WEAK if query_matched else KnowledgeQueryMatchStrength.NONE


def knowledge_records_for_graph(
    root: Path,
    *,
    repo_id: str,
) -> tuple[list[dict[str, Any]], list[Problem]]:
    projection, projection_problems = load_knowledge_projection(root, repo_id=repo_id)
    if projection_problems:
        return [], projection_problems
    graph_record_ids = [
        str(record.get("id") or "")
        for record in projection.get("heads", [])
        if isinstance(record, dict)
    ]
    projection, projection_problems = verify_current_knowledge_projection(
        root,
        repo_id=repo_id,
        projection=projection,
        record_ids=graph_record_ids,
    )
    if projection_problems:
        return [], projection_problems
    projected_current: list[dict[str, Any]] = []
    records = list(projection["heads"])
    for record in records:
        source_resolutions = knowledge_source_ref_resolutions(
            root,
            record,
        )
        projected_current.append(
            _public_record(
                root,
                record,
                status="reviewed",
                lifecycle_relations=_record_lifecycle_relations(record, records),
                source_resolutions=source_resolutions,
                resolved_source_refs=resolved_knowledge_source_refs(root, record, resolutions=source_resolutions),
            )
        )
    return sorted(projected_current, key=lambda item: str(item.get("id") or "")), []


def _validate_document_source(root: Path, rel: str) -> Problem | None:
    if (
        not rel
        or rel != rel.strip().replace("\\", "/")
        or Path(rel).is_absolute()
        or ".." in Path(rel).parts
    ):
        return Problem(
            "error",
            "knowledge_source_path_invalid",
            "knowledge source path is invalid",
            rel,
            cause_code="source_path_invalid",
        )
    parts = set(Path(rel).parts)
    if parts & EXCLUDED_SOURCE_PARTS:
        return Problem("error", "knowledge_source_excluded", "knowledge source is excluded", rel)
    if not rel.startswith(ALLOWED_SOURCE_PREFIXES):
        return Problem(
            "error",
            "knowledge_source_not_allowed",
            f"knowledge source must be under one of: {', '.join(ALLOWED_SOURCE_PREFIXES)}",
            rel,
        )
    path = root / rel
    try:
        root_resolved = root.resolve()
        path_resolved = path.resolve()
    except (OSError, ValueError, RuntimeError):
        return Problem(
            "error",
            "knowledge_source_path_invalid",
            "knowledge source path is unresolvable",
            rel,
            cause_code="source_path_unresolvable",
        )
    if root_resolved not in (path_resolved, *path_resolved.parents):
        return Problem(
            "error",
            "knowledge_source_path_invalid",
            "knowledge source escapes the workspace",
            rel,
            cause_code="source_path_invalid",
        )
    resolved_rel = path_resolved.relative_to(root_resolved).as_posix()
    if not resolved_rel.startswith(ALLOWED_SOURCE_PREFIXES) or set(Path(resolved_rel).parts) & EXCLUDED_SOURCE_PARTS:
        return Problem(
            "error",
            "knowledge_source_path_invalid",
            "knowledge source resolves outside the allowed authority document boundary",
            rel,
            cause_code="source_authority_boundary_invalid",
        )
    if not path.is_file():
        return Problem("error", "knowledge_source_missing", "knowledge source file is missing", rel)
    return None


def _knowledge_source_ref_boundary_problem(
    root: Path,
    *,
    repo_id: str,
    ref: dict[str, Any],
) -> Problem | None:
    rel = str(ref.get("path") or "")
    try:
        ref_kind = KnowledgeSourceRefKind(str(ref.get("kind") or ""))
    except ValueError:
        return Problem("error", "knowledge_source_kind_invalid", "knowledge source ref kind is invalid", rel)
    if ref_kind is KnowledgeSourceRefKind.AUTHORITY_DOCUMENT:
        return _validate_document_source(root, rel)
    if ref_kind is KnowledgeSourceRefKind.WORKSPACE_FILE:
        path = root / rel
        try:
            contained = root.resolve() in (path.resolve(), *path.resolve().parents)
        except (OSError, ValueError, RuntimeError):
            contained = False
        if not rel or Path(rel).is_absolute() or ".." in Path(rel).parts or not contained:
            return Problem(
                "error",
                "knowledge_source_path_invalid",
                "knowledge source must remain inside the workspace",
                rel,
            )
        return None
    if ref_kind is KnowledgeSourceRefKind.CURRENT_SOURCE:
        try:
            target = require_repo_target(root, repo_id=repo_id)
        except RepoctlError:
            return Problem(
                "error",
                "knowledge_source_current_invalid",
                "current_source must belong to the selected repository",
                rel,
            )
        resolution = resolve_repo_selector_path(rel, repository_path=target.display_path)
        expected = f"{target.display_path}/{resolution.path}" if resolution.path else ""
        source_path = target.root_path / resolution.path
        try:
            target_root = target.root_path.resolve()
            resolved_source = source_path.resolve()
            source_contained = target_root in (resolved_source, *resolved_source.parents)
        except (OSError, ValueError, RuntimeError):
            source_contained = False
        if (
            resolution.status != RepoSelectorStatus.RESOLVED
            or rel != expected
            or not source_contained
            or not source_path.is_file()
        ):
            return Problem(
                "error",
                "knowledge_source_current_invalid",
                "current_source must be a current workspace-qualified file in the selected repository",
                rel,
            )
    return None


def _validated_applicability_paths(
    root: Path,
    *,
    repo_id: str,
    values: list[str],
) -> tuple[list[str], list[Problem]]:
    if not values:
        return [], []
    target = require_repo_target(root, repo_id=repo_id)
    target_root = target.root_path.resolve()
    paths: list[str] = []
    problems: list[Problem] = []
    for value in values:
        resolution = resolve_repo_selector_path(value, repository_path=target.display_path)
        if resolution.status != RepoSelectorStatus.RESOLVED or not resolution.path:
            problems.append(
                Problem(
                    "error",
                    "knowledge_applies_to_invalid",
                    "knowledge applicability path must be a normalized path inside the selected repository",
                    value,
                )
            )
            continue
        path = target.root_path / resolution.path
        try:
            resolved = path.resolve()
        except OSError:
            resolved = path.absolute()
        if target_root not in (resolved, *resolved.parents) or not path.is_file():
            problems.append(
                Problem(
                    "error",
                    "knowledge_applies_to_missing",
                    "knowledge applicability path must resolve to a current file in the selected repository",
                    value,
                )
            )
            continue
        if resolution.path not in paths:
            paths.append(resolution.path)
    return sorted(paths), problems


def _validated_claim(*, claim: str, problem_path: str) -> tuple[str, Problem | None]:
    claim = claim.strip()
    if not claim:
        return "", Problem(
            "error",
            "knowledge_claim_required",
            "knowledge requires an explicit reusable claim; pass --claim or --claim-file",
            problem_path,
        )
    if len(claim) > MAX_KNOWLEDGE_CLAIM_LENGTH:
        return "", Problem(
            "error",
            "knowledge_claim_too_long",
            f"knowledge claim exceeds {MAX_KNOWLEDGE_CLAIM_LENGTH} characters; provide a concise complete claim with --claim or --claim-file",
            problem_path,
        )
    return claim, None


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _record_dir(root: Path) -> Path:
    return root / "docs/knowledge/records"


def _artifact_error_code(
    *,
    invalid_json: bool = False,
    not_object: bool = False,
) -> KnowledgeArtifactErrorCode:
    if invalid_json:
        return KnowledgeArtifactErrorCode.RECORD_INVALID_JSON
    if not_object:
        return KnowledgeArtifactErrorCode.RECORD_NOT_OBJECT
    return KnowledgeArtifactErrorCode.RECORD_UNREADABLE


def _read_knowledge_artifact(root: Path, path: Path) -> KnowledgeArtifactRead:
    rel = path.relative_to(root).as_posix()
    try:
        text = path.read_text(encoding="utf-8")
    except (OSError, UnicodeError):
        return KnowledgeArtifactRead(
            None,
            Problem("error", _artifact_error_code(), "knowledge record is unreadable", rel),
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError:
        return KnowledgeArtifactRead(
            None,
            Problem("error", _artifact_error_code(invalid_json=True), "knowledge record JSON is invalid", rel),
        )
    if not isinstance(data, dict):
        return KnowledgeArtifactRead(
            None,
            Problem("error", _artifact_error_code(not_object=True), "knowledge record must be a JSON object", rel),
        )
    return KnowledgeArtifactRead(data, None)


def _require_knowledge_artifact(root: Path, path: Path) -> dict[str, Any]:
    result = _read_knowledge_artifact(root, path)
    if result.problem is not None:
        raise RepoctlError(
            result.problem.message,
            code=result.problem.code,
            path=result.problem.path,
        )
    return result.data or {}


def _read_knowledge_artifacts(
    root: Path,
    paths: list[Path],
) -> tuple[list[dict[str, Any]], list[Problem]]:
    items: list[dict[str, Any]] = []
    problems: list[Problem] = []
    for path in paths:
        result = _read_knowledge_artifact(root, path)
        if result.problem is not None:
            problems.append(result.problem)
        elif result.data is not None:
            items.append(result.data)
    return items, problems


def _load_record_artifacts(root: Path) -> list[dict[str, Any]]:
    directory = _record_dir(root)
    if not directory.exists():
        return []
    return [
        _require_knowledge_artifact(root, path)
        for path in sorted(directory.glob("K-*.json"))
    ]


def _load_records(root: Path) -> list[dict[str, Any]]:
    return [
        record
        for record in _load_record_artifacts(root)
        if not (
            record.get("schema") == "repoctl.knowledge.record"
            and record.get("schema_version") == 1
        )
    ]


def _superseded_ids(records: list[dict[str, Any]]) -> set[str]:
    values: set[str] = set()
    for record in records:
        values.update(_record_replacements(record))
    return values


def _supersession_problems(records: list[dict[str, Any]]) -> list[Problem]:
    problems: list[Problem] = []
    known = {str(record.get("id") or "") for record in records}
    for record in records:
        record_id = str(record.get("id") or "")
        raw_replacements = record.get("replaces")
        if not isinstance(raw_replacements, list):
            problems.append(Problem("error", "knowledge_replaces_invalid", "knowledge replacement references must be a list", record_id))
            continue
        for superseded_id in _record_replacements(record):
            superseded = str(superseded_id)
            if superseded == record_id:
                problems.append(Problem("error", "knowledge_replaces_self", "knowledge record cannot replace itself", record_id))
            if superseded and superseded not in known:
                problems.append(Problem("error", "knowledge_replaces_missing", "replaced record does not exist", superseded))
    return problems


def _record_replacements(record: dict[str, Any]) -> list[str]:
    values = record.get("replaces")
    if not isinstance(values, list):
        return []
    return sorted({str(value).strip() for value in values if isinstance(value, str) and value.strip()})


def _knowledge_record_digest(record: dict[str, Any]) -> str:
    return digest_data({key: value for key, value in record.items() if key != "record_digest"})


def _knowledge_record_contract_problems(
    record: dict[str, Any],
    *,
    record_id: str,
    repo_id: str,
) -> list[Problem]:
    problems: list[Problem] = []
    schema_version = record.get("schema_version")
    if record.get("schema") != "repoctl.knowledge.record" or schema_version != KNOWLEDGE_RECORD_SCHEMA_VERSION:
        problems.append(Problem("error", "knowledge_record_schema_invalid", "knowledge record schema is invalid", record_id))
    if str(record.get("id") or "") != record_id:
        problems.append(Problem("error", "knowledge_record_id_mismatch", "knowledge record id does not match its path", record_id))
    if str(record.get("repo_id") or "") != repo_id:
        problems.append(Problem("error", "knowledge_record_repo_mismatch", "knowledge record belongs to a different repo", record_id))
    if str(record.get("kind") or "") not in ALLOWED_KINDS:
        problems.append(Problem("error", "knowledge_record_kind_invalid", "knowledge record kind is invalid", record_id))
    if not str(record.get("claim") or "").strip():
        problems.append(Problem("error", "knowledge_record_claim_missing", "knowledge record claim is missing", record_id))
    if schema_version == KNOWLEDGE_RECORD_SCHEMA_VERSION:
        if not str(record.get("reason") or "").strip():
            problems.append(Problem("error", "knowledge_record_reason_missing", "knowledge record reason is missing", record_id))
        if not isinstance(record.get("source_refs"), list) or not record.get("source_refs"):
            problems.append(Problem("error", "knowledge_record_provenance_invalid", "knowledge record sources are missing", record_id))
        if not isinstance(record.get("applies_to"), dict) or not isinstance(record.get("applies_to", {}).get("paths"), list):
            problems.append(Problem("error", "knowledge_record_paths_invalid", "knowledge record paths are invalid", record_id))
        if not isinstance(record.get("replaces"), list):
            problems.append(Problem("error", "knowledge_replaces_invalid", "knowledge replacement references are invalid", record_id))
        if not str(record.get("recorded_at") or "").strip():
            problems.append(Problem("error", "knowledge_record_provenance_invalid", "knowledge record recorded_at is missing", record_id))
        if "author" in record and not str(record.get("author") or "").strip():
            problems.append(Problem("error", "knowledge_record_author_invalid", "knowledge author must be omitted or non-empty", record_id))
    if str(record.get("record_digest") or "") != _knowledge_record_digest(record):
        problems.append(Problem("error", "knowledge_record_digest_mismatch", "knowledge record digest does not match its content", record_id))
    return problems


def _derived_status(
    record: dict[str, Any],
    *,
    superseded_ids: set[str],
) -> str:
    return (
        "superseded"
        if str(record.get("id") or "") in superseded_ids
        else "reviewed"
    )


def _source_resolution(
    ref: dict[str, Any],
    status: KnowledgeSourceResolutionStatus,
    *,
    resolved_path: str = "",
    actual_sha256: str = "",
    cause_code: str = "",
) -> KnowledgeSourceResolution:
    declared_path = str(ref.get("path") or "")
    return KnowledgeSourceResolution(
        status=status,
        declared_path=declared_path,
        resolved_path=resolved_path or declared_path,
        expected_sha256=str(ref.get("content_sha256") or ""),
        actual_sha256=actual_sha256,
        cause_code=cause_code,
    )


def _literal_source_resolution(root: Path, ref: dict[str, Any]) -> KnowledgeSourceResolution:
    declared_path = str(ref.get("path") or "")
    expected = str(ref.get("content_sha256") or "")
    if (
        not declared_path
        or declared_path != declared_path.strip().replace("\\", "/")
        or Path(declared_path).is_absolute()
        or ".." in Path(declared_path).parts
    ):
        return _source_resolution(ref, KnowledgeSourceResolutionStatus.INVALID_IDENTITY, cause_code="source_path_invalid")
    path = root / declared_path
    try:
        root_resolved = root.resolve()
        path_resolved = path.resolve()
    except (OSError, ValueError, RuntimeError):
        return _source_resolution(ref, KnowledgeSourceResolutionStatus.INVALID_IDENTITY, cause_code="source_path_unresolvable")
    if root_resolved not in (path_resolved, *path_resolved.parents):
        return _source_resolution(ref, KnowledgeSourceResolutionStatus.INVALID_IDENTITY, cause_code="source_path_invalid")
    if not path.is_file():
        return _source_resolution(ref, KnowledgeSourceResolutionStatus.MISSING)
    try:
        if str(ref.get("kind") or "") == KnowledgeSourceRefKind.COMPLETION_RECEIPT.value:
            actual = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        else:
            actual = _sha256_text(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError):
        return _source_resolution(ref, KnowledgeSourceResolutionStatus.MISSING, cause_code="source_unreadable")
    return _source_resolution(
        ref,
        (
            KnowledgeSourceResolutionStatus.CURRENT
            if not expected or expected == actual
            else KnowledgeSourceResolutionStatus.DIGEST_MISMATCH
        ),
        actual_sha256=actual,
    )


def knowledge_source_ref_resolutions(
    root: Path,
    data: dict[str, Any],
) -> list[KnowledgeSourceResolution]:
    refs = data.get("source_refs", [])
    if not isinstance(refs, list):
        return []
    resolutions: list[KnowledgeSourceResolution] = []
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        boundary_problem = _knowledge_source_ref_boundary_problem(
            root,
            repo_id=str(data.get("repo_id") or ""),
            ref=ref,
        )
        if boundary_problem is None:
            resolutions.append(_literal_source_resolution(root, ref))
            continue
        resolutions.append(
            _source_resolution(
                ref,
                (
                    KnowledgeSourceResolutionStatus.MISSING
                    if boundary_problem.code == "knowledge_source_missing"
                    else KnowledgeSourceResolutionStatus.INVALID_IDENTITY
                ),
                cause_code=boundary_problem.cause_code or boundary_problem.code,
            )
        )
    return resolutions


def resolved_knowledge_source_refs(
    root: Path,
    data: dict[str, Any],
    *,
    resolutions: list[KnowledgeSourceResolution] | None = None,
) -> list[dict[str, Any]]:
    refs = data.get("source_refs", [])
    if not isinstance(refs, list):
        return []
    resolutions = resolutions if resolutions is not None else knowledge_source_ref_resolutions(root, data)
    resolved_refs: list[dict[str, Any]] = []
    resolution_index = 0
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        resolution = resolutions[resolution_index]
        resolution_index += 1
        projected = dict(ref)
        projected["declared_path"] = resolution.declared_path
        projected["resolved_path"] = resolution.resolved_path
        projected["resolution_status"] = resolution.status.value
        if resolution.cause_code:
            projected["resolution_cause_code"] = resolution.cause_code
        if resolution.status in {
            KnowledgeSourceResolutionStatus.CURRENT,
            KnowledgeSourceResolutionStatus.RELOCATED,
        }:
            projected["path"] = resolution.resolved_path
        resolved_refs.append(projected)
    return resolved_refs


def _source_digest_problems(
    root: Path,
    data: dict[str, Any],
    *,
    record_id: str = "",
    resolutions: list[KnowledgeSourceResolution] | None = None,
) -> list[Problem]:
    refs = data.get("source_refs", [])
    if not isinstance(refs, list) or not refs:
        return [
            Problem(
                "error",
                "knowledge_source_refs_missing",
                "knowledge item has no source refs",
                str(record_id),
            )
        ]
    problems: list[Problem] = []
    source_resolutions = resolutions if resolutions is not None else knowledge_source_ref_resolutions(root, data)
    for resolution in source_resolutions:
        if resolution.status in {
            KnowledgeSourceResolutionStatus.CURRENT,
            KnowledgeSourceResolutionStatus.RELOCATED,
        }:
            continue
        if resolution.status is KnowledgeSourceResolutionStatus.MISSING:
            problems.append(
                Problem(
                    "error",
                    "knowledge_source_missing",
                    "knowledge source file is missing",
                    resolution.declared_path,
                    cause_code=resolution.cause_code or None,
                )
            )
        elif resolution.status is KnowledgeSourceResolutionStatus.DIGEST_MISMATCH:
            problems.append(
                Problem(
                    "error",
                    "knowledge_source_digest_drift",
                    "knowledge source digest changed",
                    resolution.resolved_path or resolution.declared_path,
                )
            )
        else:
            problems.append(
                Problem(
                    "error",
                    "knowledge_source_identity_invalid",
                    "receipt-derived knowledge source identity could not be verified",
                    resolution.declared_path,
                    cause_code=resolution.cause_code or None,
                )
            )
    return problems


def _source_ref_statuses(
    root: Path,
    data: dict[str, Any],
    *,
    resolutions: list[KnowledgeSourceResolution] | None = None,
) -> list[dict[str, Any]]:
    statuses: list[dict[str, Any]] = []
    refs = data.get("source_refs", [])
    if not isinstance(refs, list):
        return statuses
    resolutions = resolutions if resolutions is not None else knowledge_source_ref_resolutions(root, data)
    resolution_index = 0
    for ref in refs:
        if not isinstance(ref, dict):
            continue
        resolution = resolutions[resolution_index]
        resolution_index += 1
        declared_exists = resolution.status in {
            KnowledgeSourceResolutionStatus.CURRENT,
            KnowledgeSourceResolutionStatus.DIGEST_MISMATCH,
        }
        resolved_exists = resolution.status in {
            KnowledgeSourceResolutionStatus.CURRENT,
            KnowledgeSourceResolutionStatus.RELOCATED,
            KnowledgeSourceResolutionStatus.DIGEST_MISMATCH,
        }
        statuses.append(
            {
                "path": resolution.declared_path,
                "declared_path": resolution.declared_path,
                "resolved_path": resolution.resolved_path,
                "kind": str(ref.get("kind") or ""),
                "section": str(ref.get("section") or ""),
                "status": resolution.status.value,
                "exists": declared_exists,
                "declared_exists": declared_exists,
                "resolved_exists": resolved_exists,
                "expected_sha256": resolution.expected_sha256,
                "actual_sha256": resolution.actual_sha256,
                "digest_matches": bool(resolution.expected_sha256) and resolution.expected_sha256 == resolution.actual_sha256,
                **({"cause_code": resolution.cause_code} if resolution.cause_code else {}),
            }
        )
    return statuses


def _public_record(
    root: Path,
    record: dict[str, Any],
    *,
    status: str,
    lifecycle_relations: dict[str, Any] | None = None,
    source_resolutions: list[KnowledgeSourceResolution] | None = None,
    resolved_source_refs: list[dict[str, Any]] | None = None,
) -> dict[str, Any]:
    resolutions = source_resolutions if source_resolutions is not None else knowledge_source_ref_resolutions(root, record)
    public_source_refs = (
        resolved_source_refs
        if resolved_source_refs is not None
        else resolved_knowledge_source_refs(root, record, resolutions=resolutions)
    )
    public = {
        "id": record.get("id", ""),
        "repo_id": record.get("repo_id", ""),
        "kind": record.get("kind", ""),
        "status": status,
        "title": record.get("title") or record.get("claim", ""),
        "claim": record.get("claim", ""),
        "summary": record.get("summary") or record.get("reason", ""),
        "reason": record.get("reason", ""),
        "source_refs": record.get("source_refs", []),
        "resolved_source_refs": public_source_refs,
        "source_resolutions": _source_ref_statuses(root, record, resolutions=resolutions),
        "explicit_path_refs": explicit_knowledge_path_refs(record),
        "record_digest": record.get("record_digest", ""),
        "applies_to": {"paths": sorted(_record_applicability_paths(record))},
    }
    if lifecycle_relations:
        public["lifecycle_relations"] = lifecycle_relations
    if str(record.get("author") or ""):
        public["author"] = str(record["author"])
    if str(record.get("recorded_at") or ""):
        public["recorded_at"] = str(record["recorded_at"])
    return public


def explicit_knowledge_path_refs(record: dict[str, Any]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    for path in sorted(_record_applicability_paths(record)):
        refs.append(
            {
                "kind": KnowledgeExplicitPathKind.APPLIES_TO_PATH.value,
                "path": path,
                "role": KnowledgeExplicitPathRole.CODE_ANCHOR.value,
            }
        )
    source_refs = record.get("source_refs") if isinstance(record.get("source_refs"), list) else []
    for source_ref in source_refs:
        if not isinstance(source_ref, dict):
            continue
        path = str(source_ref.get("path") or "").strip()
        if not path:
            continue
        source_kind = str(source_ref.get("kind") or "")
        ref = {
            "kind": KnowledgeExplicitPathKind.SOURCE_REF.value,
            "path": path,
            "source_kind": source_kind,
            "role": (
                KnowledgeExplicitPathRole.CODE_ANCHOR.value
                if source_kind == KnowledgeSourceRefKind.CURRENT_SOURCE.value
                else KnowledgeExplicitPathRole.PROVENANCE_ONLY.value
            ),
        }
        if source_ref.get("content_sha256"):
            ref["content_sha256"] = str(source_ref["content_sha256"])
        refs.append(ref)
    return [
        value
        for _key, value in sorted(
            {
                (
                    str(ref.get("kind") or ""),
                    str(ref.get("path") or ""),
                    str(ref.get("source_kind") or ""),
                    str(ref.get("role") or ""),
                ): ref
                for ref in refs
            }.items()
        )
    ]


def _record_related_paths(
    record: dict[str, Any],
    *,
    resolved_source_refs: list[dict[str, Any]] | None = None,
) -> set[str]:
    paths = _record_applicability_paths(record)
    source_refs = record.get("source_refs") if isinstance(record.get("source_refs"), list) else []
    projected_refs = resolved_source_refs if resolved_source_refs is not None else []
    for ref in [*source_refs, *projected_refs]:
        if isinstance(ref, dict) and str(ref.get("path") or "").strip():
            paths.add(str(ref.get("path") or "").strip())
    return paths


def _record_applicability_paths(record: dict[str, Any]) -> set[str]:
    applies_to = record.get("applies_to") if isinstance(record.get("applies_to"), dict) else {}
    values = applies_to.get("paths") if isinstance(applies_to.get("paths"), list) else []
    return {str(value).strip() for value in values if str(value).strip()}


def _record_lifecycle_relations(record: dict[str, Any], records: list[dict[str, Any]]) -> dict[str, Any]:
    record_id = str(record.get("id") or "")
    relations = {
        "replaces": _record_replacements(record),
        "superseded_by": sorted(
            str(item.get("id") or "")
            for item in records
            if record_id in _record_replacements(item)
        ),
    }
    return {key: value for key, value in relations.items() if value}


def _record_search_body(record: dict[str, Any], *, resolved_source_refs: list[dict[str, Any]] | None = None) -> str:
    source_refs = record.get("source_refs", [])
    searchable_source_refs = [
        *(source_refs if isinstance(source_refs, list) else []),
        *(resolved_source_refs or []),
    ]
    return "\n".join(
        [
            str(record.get("id") or ""),
            str(record.get("kind") or ""),
            str(record.get("title") or ""),
            str(record.get("claim") or ""),
            str(record.get("summary") or record.get("reason") or ""),
            json.dumps(searchable_source_refs, ensure_ascii=False, sort_keys=True),
        ]
    )


def _record_score(
    query: str,
    record: dict[str, Any],
    *,
    fts: float = 0.0,
    resolved_source_refs: list[dict[str, Any]] | None = None,
) -> tuple[float, dict[str, float], list[str]]:
    identity_text = "\n".join([str(record.get("id") or ""), str(record.get("kind") or "")])
    title_text = str(record.get("title") or record.get("claim") or "")
    claim_text = str(record.get("claim") or "")
    summary_text = str(record.get("summary") or record.get("reason") or "")
    source_refs = record.get("source_refs", [])
    source_text = json.dumps(
        [*(source_refs if isinstance(source_refs, list) else []), *(resolved_source_refs or [])],
        ensure_ascii=False,
        sort_keys=True,
    )
    exact_identity = _exact_score(query, identity_text)
    exact_title = _exact_score(query, title_text)
    exact_claim = _exact_score(query, claim_text)
    exact_summary = _exact_score(query, summary_text)
    exact_source = _exact_score(query, source_text)
    authority = 0.5
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


def _record_fts_scores(
    query: str,
    records: list[dict[str, Any]],
    *,
    resolved_source_refs_by_id: dict[str, list[dict[str, Any]]] | None = None,
) -> dict[str, float]:
    terms = _query_terms(query)
    if not terms or not records:
        return {}
    conn = sqlite3.connect(":memory:")
    try:
        conn.execute("CREATE VIRTUAL TABLE records USING fts5(record_id UNINDEXED, body)")
        conn.executemany(
            "INSERT INTO records(record_id, body) VALUES (?, ?)",
            [
                (
                    str(record.get("id") or ""),
                    _record_search_body(
                        record,
                        resolved_source_refs=(resolved_source_refs_by_id or {}).get(str(record.get("id") or "")),
                    ),
                )
                for record in records
                if str(record.get("id") or "")
            ],
        )
        phrase = " OR ".join('"' + term.replace('"', '""') + '"' for term in terms)
        rows = conn.execute(
            "SELECT record_id, bm25(records) AS rank FROM records WHERE records MATCH ? ORDER BY rank, record_id",
            (phrase,),
        ).fetchall()
        return {
            str(record_id): 1.0 / position
            for position, (record_id, _rank) in enumerate(rows, start=1)
        }
    except sqlite3.Error:
        return {}
    finally:
        conn.close()


def _query_terms(query: str) -> list[str]:
    stopwords = {"a", "an", "and", "are", "for", "from", "how", "is", "of", "the", "to", "what", "why"}
    tokens = re.findall(r"[A-Za-z0-9_./:-]+|[가-힣]+", query)
    return sorted({token for token in tokens if len(token) >= 2 and token.lower() not in stopwords})

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .graph_model import digest_data
from .io import atomic_write
from .tasks import Problem


KNOWLEDGE_PROJECTION_SCHEMA = "repoctl.knowledge.current-head"
KNOWLEDGE_PROJECTION_SCHEMA_VERSION = 2
KNOWLEDGE_RECORD_SCHEMA = "repoctl.knowledge.record"
KNOWLEDGE_RECORD_SCHEMA_VERSION = 2

_RECORD_ID_RE = re.compile(r"K-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*")


def knowledge_projection_path(root: Path, *, repo_id: str) -> Path:
    return root / ".repoctl-state/knowledge" / repo_id / "current-head.json"


def initialize_empty_knowledge_projection(
    root: Path,
    *,
    repo_id: str,
    output_path: Path | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    """Initialize a new workspace projection without walking cold history."""

    destination = output_path or knowledge_projection_path(root, repo_id=repo_id)
    if destination.exists():
        return load_knowledge_projection(root, repo_id=repo_id, projection_path=destination)
    data = empty_knowledge_projection(repo_id=repo_id)
    atomic_write(destination, _json_text(data))
    return data, []


def empty_knowledge_projection(*, repo_id: str) -> dict[str, Any]:
    """Build the first projection without publishing workspace state."""

    checkpoint = {"record_count": 0}
    data = _projection(
        repo_id=repo_id,
        generation=1,
        heads=[],
        checkpoint=checkpoint,
        lifecycle_counts={"current": 0, "superseded": 0},
    )
    return data


def rebuild_knowledge_projection(
    root: Path,
    *,
    repo_id: str,
    output_path: Path | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    """Rebuild current heads from current-format records."""

    destination = output_path or knowledge_projection_path(root, repo_id=repo_id)
    records, record_problems = _load_all_records(root, repo_id=repo_id)
    if record_problems:
        destination.unlink(missing_ok=True)
        return {}, record_problems

    superseded_ids = {
        str(record_id)
        for record in records
        for record_id in _record_supersedes(record)
    }
    known_ids = {str(record.get("id") or "") for record in records}
    missing = sorted(superseded_ids - known_ids)
    if missing:
        destination.unlink(missing_ok=True)
        return {}, [
            Problem("error", "knowledge_replaces_missing", "replacement target does not exist", record_id)
            for record_id in missing
        ]
    heads = [record for record in records if str(record.get("id") or "") not in superseded_ids]
    checkpoint = {"record_count": len(records)}
    data = _projection(
        repo_id=repo_id,
        generation=1,
        heads=heads,
        checkpoint=checkpoint,
        lifecycle_counts={
            "current": len(heads),
            "superseded": len(superseded_ids),
        },
    )
    atomic_write(destination, _json_text(data))
    return data, []



def load_knowledge_projection(
    root: Path,
    *,
    repo_id: str,
    projection_path: Path | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    """Load one derived current-head file without scanning saved records."""

    path = projection_path or knowledge_projection_path(root, repo_id=repo_id)
    if not path.is_file():
        return {}, [
            _unavailable(
                "knowledge_projection_unavailable",
                "knowledge current-head projection is missing; rebuild it explicitly",
                path,
                root,
                cause_code="missing",
            )
        ]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}, [
            _unavailable(
                "knowledge_projection_unavailable",
                "knowledge current-head projection is unreadable",
                path,
                root,
                cause_code="unreadable",
            )
        ]
    if (
        not isinstance(data, dict)
        or data.get("schema") != KNOWLEDGE_PROJECTION_SCHEMA
        or data.get("schema_version") != KNOWLEDGE_PROJECTION_SCHEMA_VERSION
        or str(data.get("repo_id") or "") != repo_id
        or type(data.get("generation")) is not int
        or not isinstance(data.get("heads"), list)
        or not isinstance(data.get("checkpoint"), dict)
        or not isinstance(data.get("lifecycle_counts"), dict)
    ):
        return {}, [
            _unavailable(
                "knowledge_projection_schema_mismatch",
                "knowledge current-head projection schema or repository identity is incompatible",
                path,
                root,
                cause_code="schema_mismatch",
            )
        ]
    if data.get("head_count") != len(data["heads"]):
        return {}, [
            _unavailable(
                "knowledge_projection_schema_mismatch",
                "knowledge current-head projection count does not match its members",
                path,
                root,
                cause_code="member_count_mismatch",
            )
        ]
    lifecycle_counts = data["lifecycle_counts"]
    expected_lifecycle_keys = {"current", "superseded"}
    if set(lifecycle_counts) != expected_lifecycle_keys or any(
        type(value) is not int or value < 0
        for value in lifecycle_counts.values()
    ):
        return {}, [
            _unavailable(
                "knowledge_projection_schema_mismatch",
                "knowledge current-head projection lifecycle counts are invalid",
                path,
                root,
                cause_code="lifecycle_counts_invalid",
            )
        ]
    if lifecycle_counts["current"] != len(data["heads"]):
        return {}, [
            _unavailable(
                "knowledge_projection_schema_mismatch",
                "knowledge current-head projection current count does not match its heads",
                path,
                root,
                cause_code="lifecycle_current_count_mismatch",
            )
        ]
    checkpoint_record_count = data["checkpoint"].get("record_count")
    if (
        type(checkpoint_record_count) is not int
        or checkpoint_record_count < 0
        or sum(lifecycle_counts.values()) != checkpoint_record_count
    ):
        return {}, [
            _unavailable(
                "knowledge_projection_schema_mismatch",
                "knowledge current-head projection lifecycle does not match its checkpoint",
                path,
                root,
                cause_code="lifecycle_record_count_mismatch",
            )
        ]
    head_ids: set[str] = set()
    for record in data["heads"]:
        if not isinstance(record, dict):
            return {}, [
                _unavailable(
                    "knowledge_projection_schema_mismatch",
                    "knowledge current-head projection contains an invalid head",
                    path,
                    root,
                    cause_code="head_invalid",
                )
            ]
        record_id = str(record.get("id") or "")
        record_problems = _record_problems(record, expected_id=record_id, repo_id=repo_id)
        if record_problems or record_id in head_ids:
            return {}, [
                _unavailable(
                    "knowledge_projection_schema_mismatch",
                    "knowledge current-head projection contains an invalid or duplicate head",
                    path,
                    root,
                    cause_code=(record_problems[0].code if record_problems else "head_duplicate_or_unbound"),
                )
            ]
        head_ids.add(record_id)
    expected_digest = _projection_digest(data)
    if str(data.get("projection_digest") or "") != expected_digest:
        return {}, [
            _unavailable(
                "knowledge_projection_digest_mismatch",
                "knowledge current-head projection digest does not match its content",
                path,
                root,
                cause_code="digest_mismatch",
            )
        ]
    return data, []


def verify_current_knowledge_projection(
    root: Path,
    *,
    repo_id: str,
    projection: dict[str, Any] | None = None,
    projection_path: Path | None = None,
    record_ids: Iterable[str] | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    """Verify only the immutable artifacts named by current heads.

    This is bounded by the number of admitted heads and does not enumerate the
    record directory.
    """

    current = projection
    if current is None:
        current, load_problems = load_knowledge_projection(
            root,
            repo_id=repo_id,
            projection_path=projection_path,
        )
        if load_problems:
            return {}, load_problems
    selected_ids = (
        None
        if record_ids is None
        else {
            str(record_id)
            for record_id in record_ids
            if str(record_id)
        }
    )
    problems: list[Problem] = []
    for record in current.get("heads", []):
        if not isinstance(record, dict):
            continue
        record_id = str(record.get("id") or "")
        if selected_ids is not None and record_id not in selected_ids:
            continue
        record_path = root / "docs/knowledge/records" / f"{record_id}.json"
        stored_record, read_problems = _read_json_object(
            root,
            record_path,
            missing_code="knowledge_record_not_found",
            invalid_code="knowledge_record_unreadable",
        )
        if read_problems:
            problems.extend(read_problems)
        else:
            problems.extend(_record_problems(stored_record, expected_id=record_id, repo_id=repo_id))
            if stored_record != record:
                problems.append(
                    Problem(
                        "error",
                        "knowledge_record_digest_mismatch",
                        "reviewed knowledge record content no longer matches the admitted current head",
                        record_path.relative_to(root).as_posix(),
                    )
                )

    if problems:
        unique = {
            (problem.code, problem.path, problem.cause_code, problem.message): problem
            for problem in problems
        }
        integrity_priority = {
            "knowledge_record_digest_mismatch": 0,
            "knowledge_record_id_mismatch": 0,
            "knowledge_record_repo_mismatch": 0,
            "knowledge_record_schema_invalid": 0,
            "knowledge_record_provenance_invalid": 0,
        }
        return current, sorted(
            unique.values(),
            key=lambda problem: (
                integrity_priority.get(str(problem.code), 1),
                str(problem.code),
                str(problem.path or ""),
                problem.message,
            ),
        )
    return current, []


def _load_all_records(root: Path, *, repo_id: str) -> tuple[list[dict[str, Any]], list[Problem]]:
    directory = root / "docs/knowledge/records"
    records: list[dict[str, Any]] = []
    problems: list[Problem] = []
    if not directory.exists():
        return records, problems
    for path in sorted(directory.glob("K-*.json")):
        record, read_problems = _read_json_object(
            root,
            path,
            missing_code="knowledge_record_not_found",
            invalid_code="knowledge_record_unreadable",
        )
        if read_problems:
            problems.extend(read_problems)
            continue
        if record.get("schema") == KNOWLEDGE_RECORD_SCHEMA and record.get("schema_version") == 1:
            continue
        if str(record.get("repo_id") or "") != repo_id:
            continue
        problems.extend(_record_problems(record, expected_id=path.stem, repo_id=repo_id))
        records.append(record)
    return sorted(records, key=lambda item: str(item.get("id") or "")), problems


def _record_problems(record: dict[str, Any], *, expected_id: str, repo_id: str) -> list[Problem]:
    problems: list[Problem] = []
    schema_version = record.get("schema_version")
    if record.get("schema") != KNOWLEDGE_RECORD_SCHEMA or schema_version != KNOWLEDGE_RECORD_SCHEMA_VERSION:
        problems.append(Problem("error", "knowledge_record_schema_invalid", "knowledge record schema is invalid", expected_id))
    if not _RECORD_ID_RE.fullmatch(expected_id) or str(record.get("id") or "") != expected_id:
        problems.append(Problem("error", "knowledge_record_id_mismatch", "knowledge record id does not match its canonical path", expected_id))
    if str(record.get("repo_id") or "") != repo_id:
        problems.append(Problem("error", "knowledge_record_repo_mismatch", "knowledge record belongs to a different repository", expected_id))
    if schema_version == KNOWLEDGE_RECORD_SCHEMA_VERSION:
        if not str(record.get("claim") or "").strip() or not str(record.get("reason") or "").strip():
            problems.append(Problem("error", "knowledge_record_content_invalid", "knowledge record requires a claim and reason", expected_id))
        if not isinstance(record.get("source_refs"), list) or not record.get("source_refs"):
            problems.append(Problem("error", "knowledge_record_provenance_invalid", "knowledge record requires at least one source", expected_id))
        if not isinstance(record.get("applies_to"), dict) or not isinstance(record.get("applies_to", {}).get("paths"), list):
            problems.append(Problem("error", "knowledge_record_paths_invalid", "knowledge record applies_to paths are invalid", expected_id))
        if not isinstance(record.get("replaces"), list):
            problems.append(Problem("error", "knowledge_replaces_invalid", "knowledge record replaces must be a list", expected_id))
        if not str(record.get("recorded_at") or "").strip():
            problems.append(Problem("error", "knowledge_record_provenance_invalid", "knowledge record recorded_at is missing", expected_id))
        if "author" in record and not str(record.get("author") or "").strip():
            problems.append(Problem("error", "knowledge_record_author_invalid", "knowledge record author must be omitted or non-empty", expected_id))
    if str(record.get("record_digest") or "") != digest_data({key: value for key, value in record.items() if key != "record_digest"}):
        problems.append(Problem("error", "knowledge_record_digest_mismatch", "knowledge record digest does not match its content", expected_id))
    relation_values = record.get("replaces")
    if not isinstance(relation_values, list) or len(_record_supersedes(record)) != len(relation_values):
        problems.append(Problem("error", "knowledge_replaces_invalid", "knowledge replacement references must contain unique canonical ids", expected_id))
    return problems


def _read_json_object(
    root: Path,
    path: Path,
    *,
    missing_code: str,
    invalid_code: str,
) -> tuple[dict[str, Any], list[Problem]]:
    rel = path.relative_to(root).as_posix()
    if not path.is_file():
        return {}, [Problem("error", missing_code, "knowledge artifact is missing", rel)]
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeError, json.JSONDecodeError):
        return {}, [Problem("error", invalid_code, "knowledge artifact is unreadable", rel)]
    if not isinstance(data, dict):
        return {}, [Problem("error", invalid_code, "knowledge artifact must be a JSON object", rel)]
    return data, []


def _projection(
    *,
    repo_id: str,
    generation: int,
    heads: list[dict[str, Any]],
    checkpoint: dict[str, Any],
    lifecycle_counts: dict[str, Any],
) -> dict[str, Any]:
    ordered_heads = sorted(heads, key=lambda item: str(item.get("id") or ""))
    data = {
        "schema": KNOWLEDGE_PROJECTION_SCHEMA,
        "schema_version": KNOWLEDGE_PROJECTION_SCHEMA_VERSION,
        "repo_id": repo_id,
        "generation": generation,
        "checkpoint": checkpoint,
        "head_count": len(ordered_heads),
        "heads": ordered_heads,
        "lifecycle_counts": {
            key: int(value or 0)
            for key, value in sorted(lifecycle_counts.items())
        },
    }
    data["projection_digest"] = _projection_digest(data)
    return data


def _projection_digest(data: dict[str, Any]) -> str:
    return digest_data({key: value for key, value in data.items() if key != "projection_digest"})


def _record_supersedes(record: dict[str, Any]) -> list[str]:
    values = record.get("replaces")
    if not isinstance(values, list):
        return []
    return sorted({str(item).strip() for item in values if isinstance(item, str) and str(item).strip()})


def _unavailable(
    code: str,
    message: str,
    path: Path,
    root: Path,
    *,
    cause_code: str,
) -> Problem:
    try:
        label = path.relative_to(root).as_posix()
    except ValueError:
        label = path.as_posix()
    return Problem("error", code, message, label, cause_code=cause_code)


def _json_text(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n"

from __future__ import annotations

import json
import re
from pathlib import Path
from typing import Any, Iterable

from .graph_model import digest_data
from .io import atomic_write
from .tasks import Problem


KNOWLEDGE_PROJECTION_SCHEMA = "repoctl.knowledge.current-head"
KNOWLEDGE_PROJECTION_SCHEMA_VERSION = 1
KNOWLEDGE_RECORD_SCHEMA = "repoctl.knowledge.record"
KNOWLEDGE_EVENT_SCHEMA = "repoctl.knowledge.event"
KNOWLEDGE_ARTIFACT_SCHEMA_VERSION = 1

# Current Knowledge is an agent hot path, not an ever-growing audit store.
# Immutable records/events remain the cold source of truth; admitting more
# active heads than this requires an explicit lifecycle compaction first.
MAX_KNOWLEDGE_HOT_HEADS = 256
MAX_KNOWLEDGE_HOT_BYTES = 2 * 1024 * 1024

_RECORD_ID_RE = re.compile(r"K-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*")
_EVENT_ID_RE = re.compile(r"E-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*")
_KNOWN_EVENT_TYPES = {
    "approved",
    "deprecated",
    "refreshed_candidate",
    "refreshed_record_candidate",
    "rejected_candidate",
    "superseded",
}


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

    checkpoint = {
        "kind": "initialized_empty",
        "record_count": 0,
        "event_count": 0,
        "record_set_digest": digest_data([]),
        "event_set_digest": digest_data([]),
        "tail_sequence": 0,
        "source_chain_digest": digest_data({"records": [], "events": []}),
    }
    data = _projection(
        repo_id=repo_id,
        generation=1,
        heads=[],
        checkpoint=checkpoint,
        lifecycle_counts={"current": 0, "deprecated": 0, "superseded": 0},
    )
    return data


def rebuild_knowledge_projection(
    root: Path,
    *,
    repo_id: str,
    output_path: Path | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    """Explicitly rebuild the current-head projection from immutable cold state."""

    records, record_problems = _load_all_records(root, repo_id=repo_id)
    events, event_problems = _load_all_events(root, repo_id=repo_id)
    problems = [*record_problems, *event_problems]
    if problems:
        return {}, problems

    lifecycle_problems, approval_by_record = _validate_lifecycle(
        repo_id=repo_id,
        records=records,
        events=events,
    )
    if lifecycle_problems:
        return {}, lifecycle_problems

    superseded_ids = {
        str(record_id)
        for record in records
        for record_id in _record_supersedes(record)
    }
    deprecated_ids = {
        str(event.get("record_id") or "")
        for event in events
        if event.get("type") == "deprecated" and str(event.get("record_id") or "")
    }
    heads = []
    for record in records:
        record_id = str(record.get("id") or "")
        if record_id in superseded_ids | deprecated_ids:
            continue
        approval = approval_by_record[record_id]
        binding_events = [approval]
        binding_events.extend(
            event
            for event in events
            if event.get("type") == "superseded"
            and str(event.get("superseded_by") or "") == record_id
            and str(event.get("approved_event_id") or "") == str(approval.get("id") or "")
        )
        heads.append(_head_entry(record, approval, binding_events=binding_events))
    checkpoint = {
        "kind": "full_rebuild",
        "record_count": len(records),
        "event_count": len(events),
        "record_set_digest": digest_data([_record_identity(item) for item in records]),
        "event_set_digest": digest_data([_event_identity(item) for item in events]),
        "tail_sequence": 0,
        "source_chain_digest": digest_data(
            {
                "records": [_record_identity(item) for item in records],
                "events": [_event_identity(item) for item in events],
            }
        ),
    }
    destination = output_path or knowledge_projection_path(root, repo_id=repo_id)
    try:
        data = _projection(
            repo_id=repo_id,
            generation=1,
            heads=heads,
            checkpoint=checkpoint,
            lifecycle_counts={
                "current": len(heads),
                "deprecated": len(deprecated_ids),
                "superseded": len(superseded_ids),
            },
        )
    except ValueError:
        return {}, [
            _unavailable(
                "knowledge_projection_hot_limit_exceeded",
                "knowledge rebuild would exceed the finite current-head projection; supersede or deprecate records before rebuilding",
                destination,
                root,
                cause_code="hot_limit_exceeded",
            )
        ]
    atomic_write(destination, _json_text(data))
    return data, []


def apply_knowledge_projection_tail(
    root: Path,
    *,
    repo_id: str,
    records: Iterable[dict[str, Any]] = (),
    events: Iterable[dict[str, Any]] = (),
    expected_generation: int,
    expected_projection_digest: str,
    projection_path: Path | None = None,
    base_projection: dict[str, Any] | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    """Apply one bounded lifecycle mutation without walking cold directories."""

    path = projection_path or knowledge_projection_path(root, repo_id=repo_id)
    if base_projection is None:
        current, problems = load_knowledge_projection(root, repo_id=repo_id, projection_path=path)
        if problems:
            return {}, problems
    else:
        current = dict(base_projection)
    if (
        current.get("generation") != expected_generation
        or current.get("projection_digest") != expected_projection_digest
    ):
        return {}, [
            _unavailable(
                "knowledge_projection_tail_gap",
                "knowledge projection tail does not continue from the admitted generation and digest",
                path,
                root,
                cause_code="base_mismatch",
            )
        ]

    tail_records = sorted((dict(item) for item in records), key=lambda item: str(item.get("id") or ""))
    tail_events = sorted((dict(item) for item in events), key=lambda item: str(item.get("id") or ""))
    if not tail_records and not tail_events:
        return {}, [
            _unavailable(
                "knowledge_projection_tail_gap",
                "knowledge projection tail is empty",
                path,
                root,
                cause_code="empty_tail",
            )
        ]

    immutable_problems = _validate_exact_tail_artifacts(
        root,
        repo_id=repo_id,
        records=tail_records,
        events=tail_events,
    )
    if immutable_problems:
        return {}, immutable_problems

    approved_events = [item for item in tail_events if item.get("type") == "approved"]
    superseded_events = [item for item in tail_events if item.get("type") == "superseded"]
    deprecated_events = [item for item in tail_events if item.get("type") == "deprecated"]
    other_events = [
        item
        for item in tail_events
        if item.get("type") not in {"approved", "superseded", "deprecated"}
    ]
    if other_events:
        return {}, [
            _unavailable(
                "knowledge_projection_tail_gap",
                "candidate-only lifecycle events do not change the current-head projection",
                path,
                root,
                cause_code="unsupported_tail_event",
            )
        ]

    heads = {
        str(item.get("record", {}).get("id") or ""): dict(item)
        for item in current.get("heads", [])
        if isinstance(item, dict) and isinstance(item.get("record"), dict)
    }
    lifecycle_counts = dict(current.get("lifecycle_counts") or {})

    if tail_records:
        if len(tail_records) != 1 or len(approved_events) != 1 or deprecated_events:
            return {}, [
                _unavailable(
                    "knowledge_projection_tail_gap",
                    "an approval tail must contain one record, one approval event, and its exact supersession events",
                    path,
                    root,
                    cause_code="approval_tail_incomplete",
                )
            ]
        record = tail_records[0]
        record_id = str(record.get("id") or "")
        approval = approved_events[0]
        supersedes = _record_supersedes(record)
        expected_superseded = sorted(supersedes)
        actual_superseded = sorted(str(item.get("record_id") or "") for item in superseded_events)
        if (
            str(approval.get("record_id") or "") != record_id
            or str(approval.get("record_digest") or "") != str(record.get("record_digest") or "")
            or sorted(str(item) for item in approval.get("supersedes", []) if str(item)) != expected_superseded
            or actual_superseded != expected_superseded
        ):
            return {}, [
                _unavailable(
                    "knowledge_projection_tail_gap",
                    "approval tail is missing a record or supersession binding",
                    path,
                    root,
                    cause_code="approval_tail_binding_mismatch",
                )
            ]
        for event in superseded_events:
            old_id = str(event.get("record_id") or "")
            if (
                old_id not in heads
                or str(event.get("superseded_by") or "") != record_id
                or str(event.get("approved_event_id") or "") != str(approval.get("id") or "")
                or str(event.get("record_digest") or "") != str(record.get("record_digest") or "")
            ):
                return {}, [
                    _unavailable(
                        "knowledge_projection_tail_gap",
                        "supersession tail does not replace an admitted current head",
                        path,
                        root,
                        cause_code="superseded_head_missing",
                    )
                ]
        for old_id in expected_superseded:
            heads.pop(old_id)
        if record_id in heads:
            return {}, [
                _unavailable(
                    "knowledge_projection_tail_gap",
                    "approval tail record is already a current head",
                    path,
                    root,
                    cause_code="duplicate_head",
                )
            ]
        heads[record_id] = _head_entry(
            record,
            approval,
            binding_events=[approval, *superseded_events],
        )
        lifecycle_counts["superseded"] = int(lifecycle_counts.get("superseded") or 0) + len(expected_superseded)
    else:
        if approved_events or superseded_events or not deprecated_events:
            return {}, [
                _unavailable(
                    "knowledge_projection_tail_gap",
                    "a record-free tail must contain only deprecations of admitted current heads",
                    path,
                    root,
                    cause_code="deprecation_tail_incomplete",
                )
            ]
        for event in deprecated_events:
            record_id = str(event.get("record_id") or "")
            head = heads.get(record_id)
            if head is None or str(event.get("record_digest") or "") != str(head["record"].get("record_digest") or ""):
                return {}, [
                    _unavailable(
                        "knowledge_projection_tail_gap",
                        "deprecation tail does not reference an admitted current head",
                        path,
                        root,
                        cause_code="deprecated_head_missing",
                    )
                ]
        for event in deprecated_events:
            heads.pop(str(event.get("record_id") or ""))
        lifecycle_counts["deprecated"] = int(lifecycle_counts.get("deprecated") or 0) + len(deprecated_events)

    normalized_tail = {
        "records": [_record_identity(item) for item in tail_records],
        "events": [_event_identity(item) for item in tail_events],
    }
    previous_checkpoint = dict(current.get("checkpoint") or {})
    checkpoint = {
        **previous_checkpoint,
        "kind": "mutation_tail",
        "record_count": int(previous_checkpoint.get("record_count") or 0) + len(tail_records),
        "event_count": int(previous_checkpoint.get("event_count") or 0) + len(tail_events),
        "tail_sequence": int(previous_checkpoint.get("tail_sequence") or 0) + 1,
        "source_chain_digest": digest_data(
            {
                "previous": str(previous_checkpoint.get("source_chain_digest") or ""),
                "tail": normalized_tail,
            }
        ),
        "last_tail_digest": digest_data(normalized_tail),
    }
    lifecycle_counts["current"] = len(heads)
    try:
        updated = _projection(
            repo_id=repo_id,
            generation=int(current["generation"]) + 1,
            heads=list(heads.values()),
            checkpoint=checkpoint,
            lifecycle_counts=lifecycle_counts,
        )
    except ValueError:
        return {}, [
            _unavailable(
                "knowledge_projection_hot_limit_exceeded",
                "knowledge mutation would exceed the finite current-head projection; supersede or deprecate records before retrying",
                path,
                root,
                cause_code="hot_limit_exceeded",
            )
        ]
    atomic_write(path, _json_text(updated))
    return updated, []


def load_knowledge_projection(
    root: Path,
    *,
    repo_id: str,
    projection_path: Path | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    """Load one derived current-head file; never scans records or events."""

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
    if (
        len(data["heads"]) > MAX_KNOWLEDGE_HOT_HEADS
        or len(_json_text(data).encode("utf-8")) > MAX_KNOWLEDGE_HOT_BYTES
    ):
        return {}, [
            _unavailable(
                "knowledge_projection_hot_limit_exceeded",
                "knowledge current-head projection exceeds its finite hot-state limit; supersede or deprecate records before retrying",
                path,
                root,
                cause_code="hot_limit_exceeded",
            )
        ]
    head_ids: set[str] = set()
    for item in data["heads"]:
        if not isinstance(item, dict) or not isinstance(item.get("record"), dict):
            return {}, [
                _unavailable(
                    "knowledge_projection_schema_mismatch",
                    "knowledge current-head projection contains an invalid head",
                    path,
                    root,
                    cause_code="head_invalid",
                )
            ]
        record = item["record"]
        record_id = str(record.get("id") or "")
        record_problems = _record_problems(record, expected_id=record_id, repo_id=repo_id)
        if record_problems or record_id in head_ids or not str(item.get("approval_event_id") or ""):
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

    This is intentionally bounded by the number of admitted heads.  It never
    enumerates the cold record/event directories, but still prevents a stale
    projection from hiding deletion or mutation of an active binding.
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
    for head in current.get("heads", []):
        record = head.get("record") if isinstance(head, dict) else None
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
        admitted_record = record
        if read_problems:
            problems.extend(read_problems)
        else:
            problems.extend(_record_problems(stored_record, expected_id=record_id, repo_id=repo_id))
            admitted_record = stored_record
            if stored_record != record:
                problems.append(
                    Problem(
                        "error",
                        "knowledge_record_digest_mismatch",
                        "reviewed knowledge record content no longer matches the admitted current head",
                        record_path.relative_to(root).as_posix(),
                    )
                )

        binding_events = head.get("binding_events") if isinstance(head.get("binding_events"), list) else []
        stored_binding_events: list[dict[str, Any]] = []
        for projected_event in binding_events:
            if not isinstance(projected_event, dict):
                continue
            event_id = str(projected_event.get("id") or "")
            event_path = root / "docs/knowledge/events" / f"{event_id}.json"
            stored_event, event_read_problems = _read_json_object(
                root,
                event_path,
                missing_code=(
                    "knowledge_approval_incomplete"
                    if projected_event.get("type") == "approved"
                    else "knowledge_superseded_event_missing"
                ),
                invalid_code="knowledge_event_unreadable",
            )
            if event_read_problems:
                problems.extend(event_read_problems)
                continue
            problems.extend(_event_problems(stored_event, expected_id=event_id, repo_id=repo_id))
            stored_binding_events.append(stored_event)
            if stored_event != projected_event:
                problems.append(
                    Problem(
                        "error",
                        "knowledge_event_digest_mismatch",
                        "knowledge lifecycle event content no longer matches the admitted current binding",
                        event_id,
                    )
                )
        actual_record_digest = digest_data(
            {key: value for key, value in admitted_record.items() if key != "record_digest"}
        )
        approval_events = [
            event
            for event in stored_binding_events
            if event.get("type") == "approved"
            and str(event.get("id") or "") == str(head.get("approval_event_id") or "")
        ]
        if len(approval_events) == 1:
            approval = approval_events[0]
            created_from = admitted_record.get("created_from") if isinstance(admitted_record.get("created_from"), dict) else {}
            if (
                str(approval.get("record_id") or "") != record_id
                or str(approval.get("candidate_id") or "") != str(created_from.get("candidate_id") or "")
                or sorted(str(item) for item in approval.get("supersedes", []) if str(item))
                != _record_supersedes(admitted_record)
            ):
                problems.append(
                    Problem(
                        "error",
                        "knowledge_approval_binding_mismatch",
                        "knowledge approval event no longer binds the admitted current record",
                        record_id,
                    )
                )
            if str(approval.get("record_digest") or "") != actual_record_digest:
                problems.append(
                    Problem(
                        "error",
                        "knowledge_event_record_digest_mismatch",
                        "knowledge approval event digest does not match current record content",
                        record_id,
                    )
                )
        expected_supersedes = _record_supersedes(admitted_record)
        stored_supersessions = [
            event for event in stored_binding_events if event.get("type") == "superseded"
        ]
        for old_id in expected_supersedes:
            matches = [
                event
                for event in stored_supersessions
                if str(event.get("record_id") or "") == old_id
                and str(event.get("superseded_by") or "") == record_id
            ]
            if not matches and not any(
                problem.code == "knowledge_superseded_event_missing"
                for problem in problems
            ):
                problems.append(
                    Problem(
                        "error",
                        "knowledge_superseded_event_missing",
                        "reviewed knowledge supersession has no matching lifecycle event",
                        old_id,
                    )
                )
            for event in matches:
                if (
                    str(event.get("approved_event_id") or "") != str(head.get("approval_event_id") or "")
                    or str(event.get("record_digest") or "") != actual_record_digest
                ):
                    problems.append(
                        Problem(
                            "error",
                            "knowledge_event_record_digest_mismatch",
                            "knowledge supersession event no longer binds the replacement record",
                            record_id,
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
            "knowledge_record_status_invalid": 0,
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
        if str(record.get("repo_id") or "") != repo_id:
            continue
        problems.extend(_record_problems(record, expected_id=path.stem, repo_id=repo_id))
        records.append(record)
    return sorted(records, key=lambda item: str(item.get("id") or "")), problems


def _load_all_events(root: Path, *, repo_id: str) -> tuple[list[dict[str, Any]], list[Problem]]:
    directory = root / "docs/knowledge/events"
    events: list[dict[str, Any]] = []
    problems: list[Problem] = []
    if not directory.exists():
        return events, problems
    for path in sorted(directory.glob("E-*.json")):
        event, read_problems = _read_json_object(
            root,
            path,
            missing_code="knowledge_event_not_found",
            invalid_code="knowledge_event_unreadable",
        )
        if read_problems:
            problems.extend(read_problems)
            continue
        if str(event.get("repo_id") or "") != repo_id:
            continue
        problems.extend(_event_problems(event, expected_id=path.stem, repo_id=repo_id))
        events.append(event)
    return sorted(events, key=lambda item: str(item.get("id") or "")), problems


def _validate_lifecycle(
    *,
    repo_id: str,
    records: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> tuple[list[Problem], dict[str, dict[str, Any]]]:
    problems: list[Problem] = []
    records_by_id = {str(item.get("id") or ""): item for item in records}
    approvals: dict[str, list[dict[str, Any]]] = {}
    supersessions: dict[tuple[str, str], list[dict[str, Any]]] = {}
    for event in events:
        event_type = str(event.get("type") or "")
        record_id = str(event.get("record_id") or "")
        if event_type == "approved":
            approvals.setdefault(record_id, []).append(event)
        elif event_type == "superseded":
            supersessions.setdefault((record_id, str(event.get("superseded_by") or "")), []).append(event)
        elif event_type == "deprecated":
            record = records_by_id.get(record_id)
            if record is None:
                problems.append(Problem("error", "knowledge_event_record_missing", "knowledge deprecation references a missing record", record_id))
            elif str(event.get("record_digest") or "") != str(record.get("record_digest") or ""):
                problems.append(Problem("error", "knowledge_event_record_digest_mismatch", "knowledge deprecation digest does not match its record", record_id))

    approval_by_record: dict[str, dict[str, Any]] = {}
    for record_id, record in records_by_id.items():
        matches = approvals.get(record_id, [])
        if len(matches) != 1:
            problems.append(
                Problem(
                    "error",
                    "knowledge_approval_incomplete" if not matches else "knowledge_approval_event_duplicate",
                    "reviewed knowledge record must have exactly one approval event",
                    record_id,
                )
            )
            continue
        approval = matches[0]
        approval_by_record[record_id] = approval
        created_from = record.get("created_from") if isinstance(record.get("created_from"), dict) else {}
        if (
            str(approval.get("record_digest") or "") != str(record.get("record_digest") or "")
            or str(approval.get("candidate_id") or "") != str(created_from.get("candidate_id") or "")
            or sorted(str(item) for item in approval.get("supersedes", []) if str(item)) != _record_supersedes(record)
        ):
            problems.append(Problem("error", "knowledge_approval_binding_mismatch", "knowledge approval event does not bind the reviewed record", record_id))
        for old_id in _record_supersedes(record):
            linked = supersessions.get((old_id, record_id), [])
            if len(linked) != 1:
                problems.append(
                    Problem(
                        "error",
                        "knowledge_superseded_event_missing" if not linked else "knowledge_superseded_event_duplicate",
                        "knowledge supersession must have exactly one lifecycle event",
                        old_id,
                    )
                )
                continue
            event = linked[0]
            if (
                old_id not in records_by_id
                or str(event.get("approved_event_id") or "") != str(approval.get("id") or "")
                or str(event.get("record_digest") or "") != str(record.get("record_digest") or "")
            ):
                problems.append(Problem("error", "knowledge_superseded_event_binding_mismatch", "knowledge supersession event is not bound to its replacement approval", old_id))

    for record_id in approvals:
        if record_id not in records_by_id:
            problems.append(Problem("error", "knowledge_event_record_missing", "knowledge approval references a missing record", record_id))
    for (old_id, replacement_id), linked in supersessions.items():
        replacement = records_by_id.get(replacement_id)
        if old_id not in records_by_id or replacement is None or old_id not in _record_supersedes(replacement):
            problems.append(Problem("error", "knowledge_superseded_event_unexpected", "knowledge supersession event is not declared by a replacement record", old_id or replacement_id))
        if len(linked) > 1:
            problems.append(Problem("error", "knowledge_superseded_event_duplicate", "knowledge supersession has duplicate lifecycle events", old_id))
    return problems, approval_by_record


def _validate_exact_tail_artifacts(
    root: Path,
    *,
    repo_id: str,
    records: list[dict[str, Any]],
    events: list[dict[str, Any]],
) -> list[Problem]:
    problems: list[Problem] = []
    for record in records:
        record_id = str(record.get("id") or "")
        problems.extend(_record_problems(record, expected_id=record_id, repo_id=repo_id))
        path = root / "docs/knowledge/records" / f"{record_id}.json"
        stored, read_problems = _read_json_object(root, path, missing_code="knowledge_projection_tail_gap", invalid_code="knowledge_record_unreadable")
        problems.extend(read_problems)
        if not read_problems and stored != record:
            problems.append(Problem("error", "knowledge_projection_tail_digest_mismatch", "tail record does not match immutable cold state", path.relative_to(root).as_posix()))
    for event in events:
        event_id = str(event.get("id") or "")
        problems.extend(_event_problems(event, expected_id=event_id, repo_id=repo_id))
        path = root / "docs/knowledge/events" / f"{event_id}.json"
        stored, read_problems = _read_json_object(root, path, missing_code="knowledge_projection_tail_gap", invalid_code="knowledge_event_unreadable")
        problems.extend(read_problems)
        if not read_problems and stored != event:
            problems.append(Problem("error", "knowledge_projection_tail_digest_mismatch", "tail event does not match immutable cold state", path.relative_to(root).as_posix()))
    return problems


def _record_problems(record: dict[str, Any], *, expected_id: str, repo_id: str) -> list[Problem]:
    problems: list[Problem] = []
    if (
        record.get("schema") != KNOWLEDGE_RECORD_SCHEMA
        or record.get("schema_version") != KNOWLEDGE_ARTIFACT_SCHEMA_VERSION
    ):
        problems.append(Problem("error", "knowledge_record_schema_invalid", "knowledge record schema is invalid", expected_id))
    if not _RECORD_ID_RE.fullmatch(expected_id) or str(record.get("id") or "") != expected_id:
        problems.append(Problem("error", "knowledge_record_id_mismatch", "knowledge record id does not match its canonical path", expected_id))
    if str(record.get("repo_id") or "") != repo_id:
        problems.append(Problem("error", "knowledge_record_repo_mismatch", "knowledge record belongs to a different repository", expected_id))
    if record.get("status") != "reviewed" or record.get("authoritative") is not True:
        problems.append(Problem("error", "knowledge_record_status_invalid", "knowledge record is not explicitly reviewed and authoritative", expected_id))
    if not isinstance(record.get("created_from"), dict) or not isinstance(record.get("review"), dict):
        problems.append(Problem("error", "knowledge_record_provenance_invalid", "knowledge record review provenance is invalid", expected_id))
    if str(record.get("record_digest") or "") != digest_data({key: value for key, value in record.items() if key != "record_digest"}):
        problems.append(Problem("error", "knowledge_record_digest_mismatch", "knowledge record digest does not match its content", expected_id))
    if not isinstance(record.get("supersedes"), list) or len(_record_supersedes(record)) != len(record.get("supersedes", [])):
        problems.append(Problem("error", "knowledge_supersedes_invalid", "knowledge record supersedes must contain unique canonical ids", expected_id))
    return problems


def _event_problems(event: dict[str, Any], *, expected_id: str, repo_id: str) -> list[Problem]:
    problems: list[Problem] = []
    if (
        event.get("schema") != KNOWLEDGE_EVENT_SCHEMA
        or event.get("schema_version") != KNOWLEDGE_ARTIFACT_SCHEMA_VERSION
    ):
        problems.append(Problem("error", "knowledge_event_schema_invalid", "knowledge event schema is invalid", expected_id))
    if not _EVENT_ID_RE.fullmatch(expected_id) or str(event.get("id") or "") != expected_id:
        problems.append(Problem("error", "knowledge_event_id_mismatch", "knowledge event id does not match its canonical path", expected_id))
    if str(event.get("repo_id") or "") != repo_id:
        problems.append(Problem("error", "knowledge_event_repo_mismatch", "knowledge event belongs to a different repository", expected_id))
    if str(event.get("type") or "") not in _KNOWN_EVENT_TYPES:
        problems.append(Problem("error", "knowledge_event_type_unknown", "knowledge event type is unknown", expected_id))
    if str(event.get("event_digest") or "") != digest_data({key: value for key, value in event.items() if key != "event_digest"}):
        problems.append(Problem("error", "knowledge_event_digest_mismatch", "knowledge event digest does not match its content", expected_id))
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
    ordered_heads = sorted(heads, key=lambda item: str(item.get("record", {}).get("id") or ""))
    if len(ordered_heads) > MAX_KNOWLEDGE_HOT_HEADS:
        raise ValueError(
            f"knowledge current-head count exceeds finite hot limit ({MAX_KNOWLEDGE_HOT_HEADS})"
        )
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
    if len(_json_text(data).encode("utf-8")) > MAX_KNOWLEDGE_HOT_BYTES:
        raise ValueError(
            f"knowledge current-head projection exceeds finite byte limit ({MAX_KNOWLEDGE_HOT_BYTES})"
        )
    return data


def _projection_digest(data: dict[str, Any]) -> str:
    return digest_data({key: value for key, value in data.items() if key != "projection_digest"})


def _head_entry(
    record: dict[str, Any],
    approval: dict[str, Any],
    *,
    binding_events: Iterable[dict[str, Any]] = (),
) -> dict[str, Any]:
    return {
        "record": record,
        "approval_event_id": str(approval.get("id") or ""),
        "binding_events": [dict(event) for event in binding_events],
    }


def _record_identity(record: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(record.get("id") or ""),
        "record_digest": str(record.get("record_digest") or ""),
    }


def _event_identity(event: dict[str, Any]) -> dict[str, str]:
    return {
        "id": str(event.get("id") or ""),
        "event_digest": str(event.get("event_digest") or ""),
    }


def _record_supersedes(record: dict[str, Any]) -> list[str]:
    values = record.get("supersedes")
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

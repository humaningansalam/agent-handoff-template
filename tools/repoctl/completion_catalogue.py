from __future__ import annotations

import hashlib
import json
import os
import re
from collections.abc import Iterable, Mapping
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path, PurePosixPath
from typing import Any

from .io import RepoctlError, atomic_write


CATALOGUE_SCHEMA_VERSION = 1
CATALOGUE_PROJECTOR_VERSION = 5
CATALOGUE_POLICY_VERSION = 2
PREFIX_WINDOW_BYTES = 4096
EMPTY_PREFIX_DIGEST = "sha256:" + hashlib.sha256(b"").hexdigest()

_TASK_ID_RE = re.compile(r"T-[0-9]{14}Z")
_REPO_ID_RE = re.compile(r"[a-z][a-z0-9_-]*")
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")
_SUBJECT_KIND_RE = re.compile(r"[a-z][a-z0-9_-]*")
_SEARCH_TOKEN_RE = re.compile(r"[A-Za-z0-9_./:-]+|[가-힣]+")
_IDENTIFIER_PART_RE = re.compile(r"[A-Z]+(?=[A-Z][a-z]|\b)|[A-Z]?[a-z]+|[0-9]+|[가-힣]+")
MAX_HISTORY_SEARCH_TERMS = 512
MAX_HISTORY_SEARCH_TERM_BYTES = 256


class CompletionCatalogueUnavailableReason(StrEnum):
    MISSING = "completion_catalogue_missing"
    CORRUPT = "completion_catalogue_corrupt"
    SCHEMA_MISMATCH = "completion_catalogue_schema_mismatch"
    PROJECTOR_MISMATCH = "completion_catalogue_projector_mismatch"
    POLICY_MISMATCH = "completion_catalogue_policy_mismatch"
    PREFIX_MISMATCH = "completion_catalogue_prefix_mismatch"
    GAP = "completion_catalogue_gap"
    REPOSITORY_MISMATCH = "completion_catalogue_repository_mismatch"
    DUPLICATE_TASK = "completion_catalogue_duplicate_task"
    SOURCE_AUDIT_FAILED = "completion_catalogue_source_audit_failed"
    SOURCE_MISMATCH = "completion_catalogue_source_mismatch"


class CompletionCatalogueUnavailable(RepoctlError):
    """Typed fail-closed result for an unavailable catalogue projection."""

    def __init__(
        self,
        reason: CompletionCatalogueUnavailableReason,
        message: str,
        *,
        path: str | None = None,
    ) -> None:
        super().__init__(message, code=reason.value, path=path)
        self.reason = reason


@dataclass(frozen=True)
class CompletionCataloguePolicy:
    """Finite hot-state policy; cold JSONL history is intentionally unbounded."""

    max_subjects: int = 256
    max_frontier_per_subject: int = 4
    max_subjects_per_event: int = 128
    max_subject_key_bytes: int = 512
    max_hot_path_bytes: int = 1024
    max_hot_record_bytes: int = 8192
    max_hot_projection_bytes: int = 16 * 1024 * 1024
    max_catalogue_event_bytes: int = 2 * 1024 * 1024
    version: int = CATALOGUE_POLICY_VERSION

    def __post_init__(self) -> None:
        limits = {
            "max_subjects": (self.max_subjects, 4096),
            "max_frontier_per_subject": (self.max_frontier_per_subject, 32),
            "max_subjects_per_event": (self.max_subjects_per_event, 1024),
            "max_subject_key_bytes": (self.max_subject_key_bytes, 4096),
            "max_hot_path_bytes": (self.max_hot_path_bytes, 16384),
            "max_hot_record_bytes": (self.max_hot_record_bytes, 65536),
            "max_hot_projection_bytes": (self.max_hot_projection_bytes, 268435456),
            "max_catalogue_event_bytes": (self.max_catalogue_event_bytes, 67108864),
        }
        if type(self.version) is not int or self.version != CATALOGUE_POLICY_VERSION:
            raise ValueError("completion catalogue policy version is unsupported")
        for name, (value, upper_bound) in limits.items():
            if type(value) is not int or value < 1 or value > upper_bound:
                raise ValueError(f"{name} must be a finite integer between 1 and {upper_bound}")

    def to_dict(self) -> dict[str, int]:
        return {
            "version": self.version,
            "max_subjects": self.max_subjects,
            "max_frontier_per_subject": self.max_frontier_per_subject,
            "max_subjects_per_event": self.max_subjects_per_event,
            "max_subject_key_bytes": self.max_subject_key_bytes,
            "max_hot_path_bytes": self.max_hot_path_bytes,
            "max_hot_record_bytes": self.max_hot_record_bytes,
            "max_hot_projection_bytes": self.max_hot_projection_bytes,
            "max_catalogue_event_bytes": self.max_catalogue_event_bytes,
        }

    @property
    def digest(self) -> str:
        return _digest_data(self.to_dict())


DEFAULT_COMPLETION_CATALOGUE_POLICY = CompletionCataloguePolicy()


@dataclass(frozen=True)
class CompletionCataloguePaths:
    directory: Path
    events_directory: Path
    catalogue: Path
    head: Path
    checkpoint: Path
    projection_slots: tuple[Path, Path]


@dataclass(frozen=True)
class CompletionCatalogueWriteSet:
    """Immutable ingress writes that can join task finish's rollback boundary."""

    event_id: str
    sequence: int
    prefix_digest: str
    event_path: Path
    head_path: Path
    writes: tuple[tuple[Path, str], ...]

    @property
    def additional_state_writes(self) -> list[tuple[Path, str]]:
        return list(self.writes)


@dataclass(frozen=True)
class CompletionReceiptInput:
    receipt: Mapping[str, Any]
    receipt_path: str | Path
    receipt_text: str
    artifact_path: str | Path
    artifact_text: str = ""


@dataclass(frozen=True)
class CompletionCatalogueRefresh:
    repo_id: str
    mode: str
    changed: bool
    ingested_count: int
    last_sequence: int
    last_event_id: str
    prefix_digest: str
    checkpoint_path: str
    projection_path: str


@dataclass(frozen=True)
class CompletionCatalogueStatus:
    """Bounded catalogue state used by ordinary Graph/Context consumers."""

    repo_id: str
    status: str
    head_sequence: int
    checkpoint_sequence: int
    head_event_id: str
    checkpoint_event_id: str
    head_prefix_digest: str
    checkpoint_prefix_digest: str
    catalogue_size_bytes: int
    checkpoint_catalogue_size_bytes: int
    retained_event_count: int
    history_complete: bool

    @property
    def tail_pending(self) -> bool:
        return self.status == "tail_pending"

    def graph_identity(self) -> dict[str, Any]:
        return {
            "schema_version": CATALOGUE_SCHEMA_VERSION,
            "projector_version": CATALOGUE_PROJECTOR_VERSION,
            "policy_version": CATALOGUE_POLICY_VERSION,
            "repo_id": self.repo_id,
            "status": self.status,
            "head_sequence": self.head_sequence,
            "checkpoint_sequence": self.checkpoint_sequence,
            "head_event_id": self.head_event_id,
            "checkpoint_event_id": self.checkpoint_event_id,
            "head_prefix_digest": self.head_prefix_digest,
            "checkpoint_prefix_digest": self.checkpoint_prefix_digest,
            "catalogue_size_bytes": self.catalogue_size_bytes,
            "checkpoint_catalogue_size_bytes": self.checkpoint_catalogue_size_bytes,
            "retained_event_count": self.retained_event_count,
            "history_complete": self.history_complete,
        }


def unavailable_completion_catalogue_status(
    repo_id: str,
    reason: str,
) -> CompletionCatalogueStatus:
    """Return the canonical Graph identity for unavailable catalogue state."""

    return CompletionCatalogueStatus(
        repo_id=repo_id,
        status="unavailable",
        head_sequence=0,
        checkpoint_sequence=0,
        head_event_id="",
        checkpoint_event_id="",
        head_prefix_digest=reason,
        checkpoint_prefix_digest="",
        catalogue_size_bytes=0,
        checkpoint_catalogue_size_bytes=0,
        retained_event_count=0,
        history_complete=False,
    )


@dataclass(frozen=True)
class CompletionFrontierLookup:
    repo_id: str
    subject_key: str
    records: tuple[dict[str, Any], ...]
    retained: bool
    may_have_cold_history: bool
    omitted_record_count: int
    checkpoint_sequence: int
    prefix_digest: str


@dataclass(frozen=True)
class CompletionCatalogueRecord:
    event: dict[str, Any]
    authority_receipt: dict[str, Any] | None = None

    @property
    def task_id(self) -> str:
        return str(self.event["task_id"])

    @property
    def receipt(self) -> dict[str, Any]:
        return _json_copy(self.authority_receipt or {})

    @property
    def receipt_path(self) -> str:
        return str(self.event["receipt_path"])

    @property
    def artifact_path(self) -> str:
        return str(self.event["artifact_path"])


@dataclass(frozen=True)
class CompletionGraphInput:
    """One Graph task input projected only from finite committed hot state."""

    event_id: str
    sequence: int
    receipt: dict[str, Any]
    receipt_path: str
    receipt_sha256: str
    artifact_path: str
    artifact_sha256: str


@dataclass(frozen=True)
class CompletionHistoryMatch:
    """One authority-revalidated record selected by an explicit history query."""

    record: CompletionCatalogueRecord
    receipt_text: str
    artifact_text: str
    matched_terms: tuple[str, ...]
    exact_task: bool


@dataclass(frozen=True)
class CompletionHistorySearch:
    """Bounded result envelope for an explicitly requested cold-history scan."""

    repo_id: str
    records: tuple[CompletionHistoryMatch, ...]
    scanned_event_count: int
    matched_event_count: int
    truncated: bool
    search_terms_truncated_event_count: int
    checkpoint_sequence: int
    prefix_digest: str


@dataclass(frozen=True)
class CompletionCatalogueAudit:
    repo_id: str
    event_count: int
    last_sequence: int
    last_event_id: str
    prefix_digest: str
    task_ids: tuple[str, ...] = ()
    source_checked: bool = False


def completion_catalogue_paths(root: Path, repo_id: str = "") -> CompletionCataloguePaths:
    normalized_repo_id = _normalize_repo_id(repo_id)
    key = normalized_repo_id or "__workspace__"
    directory = Path(root) / "docs/tasks/.repoctl-state/completion-catalogue"
    return CompletionCataloguePaths(
        directory=directory,
        events_directory=directory / "events" / key,
        catalogue=directory / f"{key}.jsonl",
        head=directory / f"{key}.head.json",
        checkpoint=directory / f"{key}.checkpoint.json",
        projection_slots=(
            directory / f"{key}.projection.0.json",
            directory / f"{key}.projection.1.json",
        ),
    )


def completion_catalogue_namespaces(root: Path) -> tuple[str, ...]:
    """Enumerate persisted namespaces only for an explicit full audit."""

    root = Path(root)
    directory = completion_catalogue_paths(root).directory
    if not directory.exists():
        return ()
    if not directory.is_dir() or directory.is_symlink():
        _unavailable(
            CompletionCatalogueUnavailableReason.CORRUPT,
            "completion catalogue directory is invalid",
            root=root,
            path=directory,
        )
    suffixes = (
        ".head.json",
        ".checkpoint.json",
        ".projection.0.json",
        ".projection.1.json",
        ".jsonl",
    )
    namespaces: set[str] = set()
    for path in directory.iterdir():
        if path.name == "events":
            continue
        key = next(
            (
                path.name[: -len(suffix)]
                for suffix in suffixes
                if path.name.endswith(suffix)
            ),
            "",
        )
        if not key:
            continue
        repo_id = "" if key == "__workspace__" else key
        if repo_id and _REPO_ID_RE.fullmatch(repo_id) is None:
            _unavailable(
                CompletionCatalogueUnavailableReason.CORRUPT,
                "completion catalogue namespace is invalid",
                root=root,
                path=path,
            )
        namespaces.add(repo_id)
    events_directory = directory / "events"
    if events_directory.exists() or events_directory.is_symlink():
        if not events_directory.is_dir() or events_directory.is_symlink():
            _unavailable(
                CompletionCatalogueUnavailableReason.CORRUPT,
                "completion catalogue events directory is invalid",
                root=root,
                path=events_directory,
            )
        for path in events_directory.iterdir():
            if not path.is_dir() or path.is_symlink():
                _unavailable(
                    CompletionCatalogueUnavailableReason.CORRUPT,
                    "completion catalogue event namespace is invalid",
                    root=root,
                    path=path,
                )
            key = path.name
            repo_id = "" if key == "__workspace__" else key
            if repo_id and _REPO_ID_RE.fullmatch(repo_id) is None:
                _unavailable(
                    CompletionCatalogueUnavailableReason.CORRUPT,
                    "completion catalogue namespace is invalid",
                    root=root,
                    path=path,
                )
            namespaces.add(repo_id)
    return tuple(sorted(namespaces, key=lambda value: (value != "", value)))


def file_completion_subject_key(path: str, *, policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY) -> str:
    normalized = _normalize_repo_path(path)
    return _normalize_subject_key(f"file:{normalized}", policy=policy)


def versioned_completion_subject_key(
    subject_key: str,
    version_digest: str,
    *,
    policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY,
) -> str:
    key = _normalize_subject_key(subject_key, policy=policy)
    if not _is_digest(version_digest):
        raise ValueError("completion subject version digest is invalid")
    return _normalize_subject_key(
        f"versioned:{_digest_data({'key': key, 'version_digest': version_digest})}",
        policy=policy,
    )


def prepare_completion_sidecar_writes(
    root: Path,
    *,
    receipt: Mapping[str, Any],
    receipt_path: str | Path,
    receipt_text: str = "",
    artifact_path: str | Path = "",
    artifact_text: str = "",
    policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY,
) -> CompletionCatalogueWriteSet:
    """Prepare O(1) finish-side writes without enumerating completion receipts.

    The event sidecar and head are written with the receipt by the caller.  A
    later tail ingest follows the head's immutable linked events and therefore
    never needs a receipt-directory glob on an ordinary query/build path.
    """

    normalized_receipt = _receipt_dict(receipt)
    repo_id = _receipt_repo_id(normalized_receipt)
    paths = completion_catalogue_paths(root, repo_id)
    head = _load_head(paths, repo_id=repo_id, policy=policy, required=False)
    if head is None:
        _require_no_or_empty_materialization(
            paths,
            repo_id=repo_id,
            policy=policy,
        )
        sequence = 1
        previous_event_id = ""
        previous_prefix_digest = EMPTY_PREFIX_DIGEST
    else:
        _validate_head_sidecar(paths, head, repo_id=repo_id, policy=policy)
        sequence = int(head["last_sequence"]) + 1
        previous_event_id = str(head["last_event_id"])
        previous_prefix_digest = str(head["prefix_digest"])

    event = _build_event(
        Path(root),
        receipt=normalized_receipt,
        receipt_path=receipt_path,
        receipt_text=receipt_text,
        artifact_path=artifact_path,
        artifact_text=artifact_text,
        policy=policy,
        sequence=sequence,
        previous_event_id=previous_event_id,
        previous_prefix_digest=previous_prefix_digest,
    )
    event_path = _event_path(paths, str(event["event_id"]))
    if event_path.exists() or event_path.is_symlink():
        _unavailable(
            CompletionCatalogueUnavailableReason.DUPLICATE_TASK,
            f"completion catalogue event already exists for {event['task_id']}",
            root=Path(root),
            path=event_path,
        )
    head_data = _head_for_event(repo_id, policy, event)
    return CompletionCatalogueWriteSet(
        event_id=str(event["event_id"]),
        sequence=sequence,
        prefix_digest=str(head_data["prefix_digest"]),
        event_path=event_path,
        head_path=paths.head,
        writes=(
            (event_path, _canonical_line(event)),
            (paths.head, _pretty_json(head_data)),
        ),
    )


def ingest_completion_catalogue_tail(
    root: Path,
    repo_id: str = "",
    *,
    policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY,
) -> CompletionCatalogueRefresh:
    """Ingest only the cold-log tail and pending linked sidecars."""

    root = Path(root)
    repo_id = _normalize_repo_id(repo_id)
    paths = completion_catalogue_paths(root, repo_id)
    checkpoint, projection = _load_committed_projection(paths, repo_id=repo_id, policy=policy, allow_empty=True)
    base_sequence = int(checkpoint["last_sequence"]) if checkpoint else 0
    base_event_id = str(checkpoint["last_event_id"]) if checkpoint else ""
    base_prefix_digest = str(checkpoint["prefix_digest"]) if checkpoint else EMPTY_PREFIX_DIGEST
    base_size = int(checkpoint["catalogue_size_bytes"]) if checkpoint else 0
    _validate_catalogue_prefix(paths, checkpoint, root=root)

    cold_tail = _read_catalogue_tail(
        paths,
        offset=base_size,
        repo_id=repo_id,
        policy=policy,
        previous_sequence=base_sequence,
        previous_event_id=base_event_id,
        previous_prefix_digest=base_prefix_digest,
    )
    working_projection = _json_copy(projection) if projection is not None else _empty_projection(repo_id, policy)
    sequence = base_sequence
    event_id = base_event_id
    prefix_digest = base_prefix_digest
    seen_tail_tasks: set[str] = set()
    for event in cold_tail:
        task_id = str(event["task_id"])
        if task_id in seen_tail_tasks:
            _unavailable(CompletionCatalogueUnavailableReason.DUPLICATE_TASK, f"duplicate completion task in catalogue tail: {task_id}", root=root, path=paths.catalogue)
        seen_tail_tasks.add(task_id)
        working_projection = _apply_event(working_projection, event, policy=policy)
        sequence = int(event["sequence"])
        event_id = str(event["event_id"])
        prefix_digest = _event_prefix_digest(event)

    head = _load_head(paths, repo_id=repo_id, policy=policy, required=False)
    pending = _pending_sidecar_events(
        paths,
        head=head,
        repo_id=repo_id,
        policy=policy,
        anchor_sequence=sequence,
        anchor_event_id=event_id,
        anchor_prefix_digest=prefix_digest,
    )
    if head is None and sequence:
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue head is missing for non-empty history", root=root, path=paths.head)

    if pending:
        _append_catalogue_events(paths, pending, expected_size=paths.catalogue.stat().st_size if paths.catalogue.is_file() else 0)
        for event in pending:
            task_id = str(event["task_id"])
            if task_id in seen_tail_tasks:
                _unavailable(CompletionCatalogueUnavailableReason.DUPLICATE_TASK, f"duplicate pending completion task: {task_id}", root=root, path=paths.head)
            seen_tail_tasks.add(task_id)
            working_projection = _apply_event(working_projection, event, policy=policy)
            sequence = int(event["sequence"])
            event_id = str(event["event_id"])
            prefix_digest = _event_prefix_digest(event)

    recovered_count = len(cold_tail)
    ingested_count = recovered_count + len(pending)
    if checkpoint is not None and not ingested_count:
        return _refresh_result(root, paths, checkpoint, mode="tail", changed=False, ingested_count=0)

    catalogue_size = paths.catalogue.stat().st_size if paths.catalogue.is_file() else 0
    projection_slot = 0 if checkpoint is None else 1 - int(checkpoint["projection_slot"])
    projection_path = paths.projection_slots[projection_slot]
    projection_digest = _digest_data(working_projection)
    new_checkpoint = _checkpoint_data(
        repo_id,
        policy,
        sequence=sequence,
        event_id=event_id,
        prefix_digest=prefix_digest,
        catalogue_size=catalogue_size,
        prefix_window_digest=_prefix_window_digest(paths.catalogue, catalogue_size),
        projection_slot=projection_slot,
        projection_digest=projection_digest,
    )
    atomic_write(projection_path, _pretty_json(working_projection))
    atomic_write(paths.checkpoint, _pretty_json(new_checkpoint))
    return _refresh_result(root, paths, new_checkpoint, mode="tail", changed=bool(ingested_count), ingested_count=ingested_count)


def current_completion_frontiers(
    root: Path,
    repo_id: str,
    subject_keys: Iterable[str],
    *,
    policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY,
) -> tuple[CompletionFrontierLookup, ...]:
    """Read bounded hot frontiers from one admitted projection."""

    root = Path(root)
    repo_id = _normalize_repo_id(repo_id)
    normalized_subjects = tuple(
        dict.fromkeys(_normalize_subject_key(subject, policy=policy) for subject in subject_keys)
    )
    paths = completion_catalogue_paths(root, repo_id)
    checkpoint, projection = _load_committed_projection(paths, repo_id=repo_id, policy=policy, allow_empty=False)
    assert checkpoint is not None and projection is not None
    subjects = {item["subject_key"]: item for item in projection["subjects"]}
    may_have_cold_history = bool(
        int(projection["eviction_count"]) > 0
        or int(projection["truncated_event_count"]) > 0
    )
    return tuple(
        CompletionFrontierLookup(
            repo_id=repo_id,
            subject_key=key,
            records=tuple(_json_copy(record) for record in subjects[key]["frontier"])
            if key in subjects
            else (),
            retained=key in subjects,
            may_have_cold_history=(
                (key in subjects and int(subjects[key]["overflow_count"]) > 0)
                or may_have_cold_history
            ),
            omitted_record_count=(
                int(subjects[key]["overflow_count"])
                if key in subjects
                else 0
            ),
            checkpoint_sequence=int(checkpoint["last_sequence"]),
            prefix_digest=str(checkpoint["prefix_digest"]),
        )
        for key in normalized_subjects
    )


def current_completion_frontier(
    root: Path,
    repo_id: str,
    subject_key: str,
    *,
    policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY,
) -> CompletionFrontierLookup:
    """Read one bounded hot frontier; this never opens the cold catalogue."""

    return current_completion_frontiers(
        root,
        repo_id,
        (subject_key,),
        policy=policy,
    )[0]


def completion_catalogue_status(
    root: Path,
    repo_id: str = "",
    *,
    policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY,
) -> CompletionCatalogueStatus:
    """Read bounded ingress/materialization state without opening cold JSONL.

    File size is a fixed-path probe.  Content validation of the cold prefix is
    reserved for explicit tail ingest, rebuild, and audit operations.
    """

    root = Path(root)
    repo_id = _normalize_repo_id(repo_id)
    paths = completion_catalogue_paths(root, repo_id)
    head = _load_head(paths, repo_id=repo_id, policy=policy, required=False)
    checkpoint, projection = _load_committed_projection(
        paths,
        repo_id=repo_id,
        policy=policy,
        allow_empty=True,
    )
    head_sequence = int(head["last_sequence"]) if head else 0
    checkpoint_sequence = int(checkpoint["last_sequence"]) if checkpoint else 0
    head_event_id = str(head["last_event_id"]) if head else ""
    checkpoint_event_id = str(checkpoint["last_event_id"]) if checkpoint else ""
    head_prefix = str(head["prefix_digest"]) if head else EMPTY_PREFIX_DIGEST
    checkpoint_prefix = str(checkpoint["prefix_digest"]) if checkpoint else EMPTY_PREFIX_DIGEST
    checkpoint_size = int(checkpoint["catalogue_size_bytes"]) if checkpoint else 0
    if paths.catalogue.exists() or paths.catalogue.is_symlink():
        _require_regular_file(root, paths.catalogue)
        catalogue_size = paths.catalogue.stat().st_size
    else:
        catalogue_size = 0

    if head_sequence < checkpoint_sequence or catalogue_size < checkpoint_size:
        _unavailable(
            CompletionCatalogueUnavailableReason.GAP,
            "completion catalogue ingress/materialization frontier regressed; run explicit rebuild",
            root=root,
            path=paths.directory,
        )
    if head_sequence == checkpoint_sequence:
        if head_sequence and (
            head_event_id != checkpoint_event_id or head_prefix != checkpoint_prefix
        ):
            _unavailable(
                CompletionCatalogueUnavailableReason.PREFIX_MISMATCH,
                "completion catalogue head does not bind the admitted checkpoint",
                root=root,
                path=paths.head,
            )
        if catalogue_size != checkpoint_size:
            _unavailable(
                CompletionCatalogueUnavailableReason.GAP,
                "completion catalogue contains an unbound cold tail; run explicit rebuild",
                root=root,
                path=paths.catalogue,
            )
        status = "current" if checkpoint is not None else "empty"
    else:
        if catalogue_size != checkpoint_size:
            _unavailable(
                CompletionCatalogueUnavailableReason.GAP,
                "completion catalogue cold tail is not represented by ingress sidecars",
                root=root,
                path=paths.catalogue,
            )
        status = "tail_pending"

    retained_event_ids = _projection_event_ids(projection or {})
    history_complete = bool(
        checkpoint is not None
        and int((projection or {}).get("eviction_count") or 0) == 0
        and int((projection or {}).get("truncated_event_count") or 0) == 0
        and all(
            int(subject.get("overflow_count") or 0) == 0
            for subject in (projection or {}).get("subjects", [])
            if isinstance(subject, Mapping)
        )
        and len(retained_event_ids) == checkpoint_sequence
    )
    return CompletionCatalogueStatus(
        repo_id=repo_id,
        status=status,
        head_sequence=head_sequence,
        checkpoint_sequence=checkpoint_sequence,
        head_event_id=head_event_id,
        checkpoint_event_id=checkpoint_event_id,
        head_prefix_digest=head_prefix,
        checkpoint_prefix_digest=checkpoint_prefix,
        catalogue_size_bytes=catalogue_size,
        checkpoint_catalogue_size_bytes=checkpoint_size,
        retained_event_count=len(retained_event_ids),
        history_complete=history_complete,
    )


def completion_graph_inputs(
    root: Path,
    repo_id: str = "",
    *,
    policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY,
) -> tuple[CompletionGraphInput, ...]:
    """Project de-duplicated Graph inputs without opening cold event sidecars."""

    root = Path(root)
    repo_id = _normalize_repo_id(repo_id)
    paths = completion_catalogue_paths(root, repo_id)
    checkpoint, projection = _load_committed_projection(
        paths,
        repo_id=repo_id,
        policy=policy,
        allow_empty=True,
    )
    if checkpoint is None or projection is None:
        return ()
    by_task: dict[str, dict[str, Any]] = {}
    for subject in projection["subjects"]:
        for record in subject["frontier"]:
            graph = record.get("graph") if isinstance(record.get("graph"), Mapping) else {}
            task_id = str(record.get("task_id") or "")
            if not task_id or not graph:
                continue
            existing = by_task.setdefault(
                task_id,
                {
                    "event_id": str(record["event_id"]),
                    "sequence": int(record["sequence"]),
                    "task_id": task_id,
                    "status": str(record.get("status") or ""),
                    "completed_at": str(record.get("completed_at") or ""),
                    "task_path_at_completion": str(graph.get("task_path_at_completion") or ""),
                    "content_sha256": str(record.get("artifact_sha256") or ""),
                    "receipt_path": str(record.get("receipt_path") or ""),
                    "receipt_sha256": str(record.get("receipt_sha256") or ""),
                    "artifact_path": str(record.get("artifact_path") or ""),
                    "artifact_sha256": str(record.get("artifact_sha256") or ""),
                    "verification": _json_copy(graph.get("verification") or {}),
                    "repo_evidence": _json_copy(graph.get("repo_evidence") or {}),
                    "changed_entries": {},
                },
            )
            if (
                existing["event_id"] != record["event_id"]
                or existing["receipt_sha256"] != record["receipt_sha256"]
            ):
                _unavailable(
                    CompletionCatalogueUnavailableReason.CORRUPT,
                    "completion catalogue task witnesses disagree",
                    root=root,
                    path=paths.projection_slots[int(checkpoint["projection_slot"])],
                )
            change = graph.get("changed_entry")
            if isinstance(change, Mapping):
                change_key = _canonical_json(change)
                existing["changed_entries"][change_key] = _json_copy(change)
    records: list[CompletionGraphInput] = []
    for task in sorted(by_task.values(), key=lambda item: (int(item["sequence"]), str(item["task_id"]))):
        changed_entries = [task["changed_entries"][key] for key in sorted(task["changed_entries"])]
        receipt = {
            "schema": "repoctl.task.completion-hot-graph",
            "schema_version": 1,
            "repo_id": repo_id,
            "task_id": task["task_id"],
            "status": task["status"],
            "completed_at": task["completed_at"],
            "task_path_at_completion": task["task_path_at_completion"],
            "content_sha256": task["content_sha256"],
            "changed_entries": changed_entries,
            "repo_evidence": task["repo_evidence"],
            "verification": task["verification"],
        }
        records.append(
            CompletionGraphInput(
                event_id=task["event_id"],
                sequence=int(task["sequence"]),
                receipt=receipt,
                receipt_path=task["receipt_path"],
                receipt_sha256=task["receipt_sha256"],
                artifact_path=task["artifact_path"],
                artifact_sha256=task["artifact_sha256"],
            )
        )
    return tuple(records)


def lookup_completion_exact(
    root: Path,
    repo_id: str,
    task_id: str,
    *,
    policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY,
) -> CompletionCatalogueRecord | None:
    """Explicit O(N) cold lookup. Ordinary Context/Graph paths must not call it."""

    if not isinstance(task_id, str) or _TASK_ID_RE.fullmatch(task_id) is None:
        raise ValueError("completion catalogue task_id is invalid")
    root = Path(root)
    repo_id = _normalize_repo_id(repo_id)
    paths = completion_catalogue_paths(root, repo_id)
    checkpoint, projection = _load_committed_projection(paths, repo_id=repo_id, policy=policy, allow_empty=False)
    assert checkpoint is not None and projection is not None
    events = _scan_catalogue(paths, repo_id=repo_id, policy=policy)
    _validate_scan_against_state(root, paths, checkpoint, projection, events, repo_id=repo_id, policy=policy, verify_sidecars=False)
    match = next((event for event in events if event["task_id"] == task_id), None)
    if match is None:
        return None
    # Exact cold lookup is the authority-sensitive path: revalidate the one
    # referenced receipt/artifact rather than trusting a derived JSONL copy.
    from .tasks import completion_receipt_artifact_for_task

    artifact, problems = completion_receipt_artifact_for_task(
        root,
        task_id=task_id,
        repo_id=repo_id,
        audit_history=True,
    )
    if problems or artifact is None:
        messages = "; ".join(problem.message for problem in problems[:3]) or "receipt is missing"
        _unavailable(
            CompletionCatalogueUnavailableReason.SOURCE_AUDIT_FAILED,
            f"exact completion receipt validation failed: {messages}",
            root=root,
            path=root / str(match["receipt_path"]),
        )
    if (
        _digest_data(artifact.receipt) != match["receipt_canonical_sha256"]
        or artifact.receipt_path != match["receipt_path"]
        or artifact.receipt_sha256 != match["receipt_sha256"]
        or artifact.resolved_path != match["artifact_path"]
        or artifact.content_sha256 != match["artifact_sha256"]
    ):
        _unavailable(
            CompletionCatalogueUnavailableReason.SOURCE_MISMATCH,
            "exact completion receipt no longer matches its cold catalogue entry",
            root=root,
            path=root / str(match["receipt_path"]),
        )
    return CompletionCatalogueRecord(_json_copy(match), _json_copy(artifact.receipt))


def search_completion_history(
    root: Path,
    repo_id: str,
    *,
    query_terms: Iterable[str],
    task_ids: Iterable[str] = (),
    limit: int = 4,
    policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY,
) -> CompletionHistorySearch:
    """Explicitly scan cold catalogue history and return a bounded result.

    This is intentionally not an ordinary Context/Graph API. The caller must
    opt into historical lookup. Catalogue integrity is verified before any
    result is selected, and only selected receipt/artifact authorities are
    revalidated and returned.
    """

    if type(limit) is not int or limit < 1 or limit > policy.max_frontier_per_subject:
        raise ValueError(
            "completion history search limit must be between 1 and "
            f"{policy.max_frontier_per_subject}"
        )
    root = Path(root)
    repo_id = _normalize_repo_id(repo_id)
    normalized_terms = tuple(
        sorted(
            {
                str(value).casefold().strip()
                for value in query_terms
                if isinstance(value, str) and str(value).strip()
            }
        )
    )
    normalized_task_ids: set[str] = set()
    for task_id in task_ids:
        value = str(task_id).strip()
        if _TASK_ID_RE.fullmatch(value) is None:
            raise ValueError("completion history task_id is invalid")
        normalized_task_ids.add(value)
    if not normalized_terms and not normalized_task_ids:
        raise ValueError("completion history search requires query terms or task IDs")

    paths = completion_catalogue_paths(root, repo_id)
    checkpoint, projection = _load_committed_projection(
        paths,
        repo_id=repo_id,
        policy=policy,
        allow_empty=False,
    )
    assert checkpoint is not None and projection is not None
    events = _scan_catalogue(paths, repo_id=repo_id, policy=policy)
    _validate_scan_against_state(
        root,
        paths,
        checkpoint,
        projection,
        events,
        repo_id=repo_id,
        policy=policy,
        verify_sidecars=False,
    )

    ranked: list[tuple[int, int, int, str, dict[str, Any], tuple[str, ...]]] = []
    for event in events:
        task_id = str(event["task_id"])
        exact_task = task_id in normalized_task_ids
        search_terms = {
            str(value)
            for value in event.get("search_terms", [])
            if isinstance(value, str)
        }
        matched_terms = tuple(term for term in normalized_terms if term in search_terms)
        if not exact_task and not matched_terms:
            continue
        ranked.append(
            (
                0 if exact_task else 1,
                -len(matched_terms),
                -int(event["sequence"]),
                task_id,
                event,
                matched_terms,
            )
        )
    ranked.sort(key=lambda item: item[:4])
    selected = ranked[:limit]

    from .tasks import completion_receipt_artifact_for_task

    matches: list[CompletionHistoryMatch] = []
    for exact_rank, _term_rank, _sequence_rank, task_id, event, matched_terms in selected:
        artifact, problems = completion_receipt_artifact_for_task(
            root,
            task_id=task_id,
            repo_id=repo_id,
            audit_history=True,
        )
        if problems or artifact is None:
            messages = "; ".join(problem.message for problem in problems[:3]) or "receipt is missing"
            _unavailable(
                CompletionCatalogueUnavailableReason.SOURCE_AUDIT_FAILED,
                f"completion history authority validation failed: {messages}",
                root=root,
                path=root / str(event["receipt_path"]),
            )
        if (
            _digest_data(artifact.receipt) != event["receipt_canonical_sha256"]
            or artifact.receipt_path != event["receipt_path"]
            or artifact.receipt_sha256 != event["receipt_sha256"]
            or artifact.resolved_path != event["artifact_path"]
            or artifact.content_sha256 != event["artifact_sha256"]
        ):
            _unavailable(
                CompletionCatalogueUnavailableReason.SOURCE_MISMATCH,
                "completion history authority no longer matches its catalogue entry",
                root=root,
                path=root / str(event["receipt_path"]),
            )
        matches.append(
            CompletionHistoryMatch(
                record=CompletionCatalogueRecord(
                    _json_copy(event),
                    _json_copy(artifact.receipt),
                ),
                receipt_text=artifact.receipt_text,
                artifact_text=artifact.artifact_text,
                matched_terms=matched_terms,
                exact_task=exact_rank == 0,
            )
        )
    return CompletionHistorySearch(
        repo_id=repo_id,
        records=tuple(matches),
        scanned_event_count=len(events),
        matched_event_count=len(ranked),
        truncated=len(ranked) > len(matches),
        search_terms_truncated_event_count=sum(
            1 for event in events if event.get("search_terms_truncated") is True
        ),
        checkpoint_sequence=int(checkpoint["last_sequence"]),
        prefix_digest=str(checkpoint["prefix_digest"]),
    )


def audit_completion_catalogue(
    root: Path,
    repo_id: str = "",
    *,
    receipt_artifacts: Iterable[CompletionReceiptInput | Mapping[str, Any] | Any] | None = None,
    policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY,
) -> CompletionCatalogueAudit:
    """Explicit full audit of JSONL, sidecars, checkpoint, and hot replay.

    When validated receipt artifacts are supplied, their task set and exact
    receipt/artifact identities must also match the derived catalogue.  The
    caller owns cold receipt enumeration so a multi-repository audit can scan
    that authority once and partition it by repository.
    """

    root = Path(root)
    repo_id = _normalize_repo_id(repo_id)
    paths = completion_catalogue_paths(root, repo_id)
    checkpoint, projection = _load_committed_projection(paths, repo_id=repo_id, policy=policy, allow_empty=False)
    assert checkpoint is not None and projection is not None
    events = _scan_catalogue(paths, repo_id=repo_id, policy=policy)
    _validate_scan_against_state(root, paths, checkpoint, projection, events, repo_id=repo_id, policy=policy, verify_sidecars=True)
    if receipt_artifacts is not None:
        _validate_catalogue_sources(
            root,
            repo_id=repo_id,
            events=events,
            receipt_artifacts=receipt_artifacts,
        )
    return CompletionCatalogueAudit(
        repo_id=repo_id,
        event_count=len(events),
        last_sequence=int(checkpoint["last_sequence"]),
        last_event_id=str(checkpoint["last_event_id"]),
        prefix_digest=str(checkpoint["prefix_digest"]),
        task_ids=tuple(str(event["task_id"]) for event in events),
        source_checked=receipt_artifacts is not None,
    )


def _validate_catalogue_sources(
    root: Path,
    *,
    repo_id: str,
    events: list[dict[str, Any]],
    receipt_artifacts: Iterable[CompletionReceiptInput | Mapping[str, Any] | Any],
) -> None:
    inputs = _explicit_receipt_inputs(root, repo_id, receipt_artifacts)
    by_task: dict[str, CompletionReceiptInput] = {}
    for item in inputs:
        receipt = _receipt_dict(item.receipt)
        task_id = str(receipt["task_id"])
        if task_id in by_task:
            _unavailable(
                CompletionCatalogueUnavailableReason.DUPLICATE_TASK,
                f"duplicate source completion task: {task_id}",
                root=root,
            )
        if _receipt_repo_id(receipt) != repo_id:
            _unavailable(
                CompletionCatalogueUnavailableReason.REPOSITORY_MISMATCH,
                f"source completion belongs to another repository: {task_id}",
                root=root,
            )
        by_task[task_id] = item

    event_tasks = {str(event["task_id"]) for event in events}
    source_tasks = set(by_task)
    if event_tasks != source_tasks:
        missing = sorted(source_tasks - event_tasks)
        extra = sorted(event_tasks - source_tasks)
        detail = "; ".join(
            part
            for part in (
                f"missing catalogue tasks: {', '.join(missing[:3])}" if missing else "",
                f"orphan catalogue tasks: {', '.join(extra[:3])}" if extra else "",
            )
            if part
        )
        _unavailable(
            CompletionCatalogueUnavailableReason.SOURCE_MISMATCH,
            f"completion receipt authority and catalogue task sets differ ({detail})",
            root=root,
            path=completion_catalogue_paths(root, repo_id).catalogue,
        )

    for event in events:
        task_id = str(event["task_id"])
        item = by_task[task_id]
        receipt = _receipt_dict(item.receipt)
        receipt_text = str(item.receipt_text or _pretty_json(receipt))
        receipt_path = _workspace_relative(root, item.receipt_path)
        artifact_path = _workspace_relative(
            root,
            item.artifact_path or str(receipt.get("task_path_at_completion") or ""),
        )
        if (
            _digest_data(receipt) != event["receipt_canonical_sha256"]
            or _digest_text(receipt_text) != event["receipt_sha256"]
            or receipt_path != event["receipt_path"]
            or artifact_path != event["artifact_path"]
            or str(receipt.get("content_sha256") or "") != event["artifact_sha256"]
        ):
            _unavailable(
                CompletionCatalogueUnavailableReason.SOURCE_MISMATCH,
                f"completion receipt authority no longer matches catalogue task: {task_id}",
                root=root,
                path=root / receipt_path,
            )


def rebuild_completion_catalogue(
    root: Path,
    repo_id: str = "",
    *,
    receipt_artifacts: Iterable[CompletionReceiptInput | Mapping[str, Any] | Any] | None = None,
    policy: CompletionCataloguePolicy = DEFAULT_COMPLETION_CATALOGUE_POLICY,
) -> CompletionCatalogueRefresh:
    """Explicit O(N) rebuild. Only this default path enumerates receipt history."""

    root = Path(root)
    repo_id = _normalize_repo_id(repo_id)
    inputs = _explicit_receipt_inputs(root, repo_id, receipt_artifacts)
    ordered = sorted(
        inputs,
        key=lambda item: (
            str(item.receipt.get("completed_event_at") or item.receipt.get("completed_at") or ""),
            str(item.receipt.get("task_id") or ""),
        ),
    )
    events: list[dict[str, Any]] = []
    seen_tasks: set[str] = set()
    previous_event_id = ""
    previous_prefix_digest = EMPTY_PREFIX_DIGEST
    for sequence, item in enumerate(ordered, start=1):
        task_id = str(item.receipt.get("task_id") or "")
        if task_id in seen_tasks:
            _unavailable(CompletionCatalogueUnavailableReason.DUPLICATE_TASK, f"duplicate source completion task: {task_id}", root=root)
        seen_tasks.add(task_id)
        if _receipt_repo_id(_receipt_dict(item.receipt)) != repo_id:
            _unavailable(CompletionCatalogueUnavailableReason.REPOSITORY_MISMATCH, f"source completion belongs to another repository: {task_id}", root=root)
        event = _build_event(
            root,
            receipt=_receipt_dict(item.receipt),
            receipt_path=item.receipt_path,
            receipt_text=item.receipt_text,
            artifact_path=item.artifact_path,
            artifact_text=item.artifact_text,
            policy=policy,
            sequence=sequence,
            previous_event_id=previous_event_id,
            previous_prefix_digest=previous_prefix_digest,
        )
        events.append(event)
        previous_event_id = str(event["event_id"])
        previous_prefix_digest = _event_prefix_digest(event)

    paths = completion_catalogue_paths(root, repo_id)
    projection = _empty_projection(repo_id, policy)
    for event in events:
        projection = _apply_event(projection, event, policy=policy)
    catalogue_text = "".join(_canonical_line(event) for event in events)
    for event in events:
        atomic_write(_event_path(paths, str(event["event_id"])), _canonical_line(event))
    atomic_write(paths.catalogue, catalogue_text)
    projection_slot = 0
    atomic_write(paths.projection_slots[projection_slot], _pretty_json(projection))
    catalogue_bytes = catalogue_text.encode("utf-8")
    checkpoint = _checkpoint_data(
        repo_id,
        policy,
        sequence=len(events),
        event_id=previous_event_id,
        prefix_digest=previous_prefix_digest,
        catalogue_size=len(catalogue_bytes),
        prefix_window_digest=_digest_bytes(catalogue_bytes[-PREFIX_WINDOW_BYTES:]),
        projection_slot=projection_slot,
        projection_digest=_digest_data(projection),
    )
    atomic_write(paths.checkpoint, _pretty_json(checkpoint))
    if events:
        atomic_write(paths.head, _pretty_json(_head_for_event(repo_id, policy, events[-1])))
    elif paths.head.exists() and paths.head.is_file() and not paths.head.is_symlink():
        paths.head.unlink()
    return _refresh_result(root, paths, checkpoint, mode="rebuild", changed=True, ingested_count=len(events))


def _normalize_repo_id(repo_id: str) -> str:
    if not isinstance(repo_id, str) or (repo_id and _REPO_ID_RE.fullmatch(repo_id) is None):
        raise ValueError("completion catalogue repo_id is invalid")
    return repo_id


def _canonical_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _canonical_line(value: Any) -> str:
    return _canonical_json(value) + "\n"


def _pretty_json(value: Any) -> str:
    return json.dumps(value, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _json_copy(value: Any) -> Any:
    return json.loads(_canonical_json(value))


def _digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _digest_text(value: str) -> str:
    return _digest_bytes(value.encode("utf-8"))


def _digest_data(value: Any) -> str:
    return _digest_text(_canonical_json(value))


def _is_digest(value: Any) -> bool:
    return isinstance(value, str) and _DIGEST_RE.fullmatch(value) is not None


def _relative_label(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _unavailable(
    reason: CompletionCatalogueUnavailableReason,
    message: str,
    *,
    root: Path,
    path: Path | None = None,
) -> None:
    raise CompletionCatalogueUnavailable(reason, message, path=_relative_label(root, path) if path is not None else None)


def _workspace_relative(root: Path, value: str | Path) -> str:
    candidate = Path(value)
    if candidate.is_absolute():
        try:
            candidate = candidate.relative_to(root)
        except ValueError as exc:
            raise ValueError("completion catalogue path is outside the workspace") from exc
    normalized = candidate.as_posix()
    pure = PurePosixPath(normalized)
    if not normalized or normalized != str(pure) or normalized.startswith("/") or any(part in {"", ".", ".."} for part in pure.parts):
        raise ValueError("completion catalogue path is not canonical workspace-relative data")
    return normalized


def _normalize_repo_path(value: str) -> str:
    if not isinstance(value, str) or not value or "\\" in value or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("completion subject path is invalid")
    path = PurePosixPath(value)
    normalized = str(path)
    if normalized != value or normalized.startswith("/") or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError("completion subject path is not canonical repository-relative data")
    return normalized


def _normalize_subject_key(value: str, *, policy: CompletionCataloguePolicy) -> str:
    if not isinstance(value, str) or ":" not in value or "\x00" in value or "\n" in value or "\r" in value:
        raise ValueError("completion subject key is invalid")
    kind, identity = value.split(":", 1)
    if _SUBJECT_KIND_RE.fullmatch(kind) is None or not identity or len(value.encode("utf-8")) > policy.max_subject_key_bytes:
        raise ValueError("completion subject key is not canonical finite data")
    if kind == "file":
        identity = _normalize_repo_path(identity)
    return f"{kind}:{identity}"


def _receipt_dict(receipt: Mapping[str, Any]) -> dict[str, Any]:
    if not isinstance(receipt, Mapping):
        raise ValueError("completion receipt must be an object")
    data = _json_copy(dict(receipt))
    if data.get("schema") != "repoctl.task.completion":
        raise ValueError("completion receipt schema is invalid")
    if type(data.get("schema_version")) is not int:
        raise ValueError("completion receipt schema_version is invalid")
    task_id = data.get("task_id")
    if not isinstance(task_id, str) or _TASK_ID_RE.fullmatch(task_id) is None:
        raise ValueError("completion receipt task_id is invalid")
    _receipt_repo_id(data)
    if data.get("status") != "done" or not isinstance(data.get("changed_entries"), list):
        raise ValueError("completion receipt is not a completed task envelope")
    return data


def _receipt_repo_id(receipt: Mapping[str, Any]) -> str:
    repo_id = receipt.get("repo_id")
    if not isinstance(repo_id, str):
        raise ValueError("completion receipt repo_id is invalid")
    return _normalize_repo_id(repo_id)


def _event_subjects(receipt: Mapping[str, Any], *, policy: CompletionCataloguePolicy) -> tuple[list[str], int]:
    subjects: set[str] = set()
    outcome = receipt.get("discovery_outcome")
    versioned_outcome_paths: set[str] = set()
    if isinstance(outcome, Mapping):
        for raw in outcome.get("subjects", []):
            if not isinstance(raw, Mapping) or raw.get("kind") != "file":
                continue
            identity = raw.get("identity")
            if not isinstance(identity, Mapping) or not isinstance(identity.get("path"), str):
                continue
            try:
                if isinstance(raw.get("key"), str) and isinstance(raw.get("version_digest"), str):
                    subjects.add(
                        versioned_completion_subject_key(
                            str(raw["key"]),
                            str(raw["version_digest"]),
                            policy=policy,
                        )
                    )
                    versioned_outcome_paths.add(str(identity["path"]))
            except ValueError:
                continue
    for raw in receipt.get("changed_entries", []):
        if not isinstance(raw, Mapping):
            continue
        for field in ("path", "old_path"):
            value = raw.get(field)
            if not isinstance(value, str) or not value:
                continue
            try:
                if value not in versioned_outcome_paths:
                    subjects.add(file_completion_subject_key(value, policy=policy))
            except ValueError:
                continue
    ordered = sorted(subjects)
    return ordered[: policy.max_subjects_per_event], len(ordered)


def _event_search_terms(
    receipt: Mapping[str, Any],
    *,
    artifact_text: str,
) -> tuple[list[str], bool]:
    """Project a bounded lexical vocabulary during trusted catalogue ingress."""

    searchable = "\n".join(
        (
            str(receipt.get("task_id") or ""),
            str(receipt.get("task_path_at_completion") or ""),
            _canonical_json(receipt),
            artifact_text,
        )
    )
    terms: set[str] = set()
    for token in _SEARCH_TOKEN_RE.findall(searchable):
        raw = token.casefold().strip("`'\"")
        if len(raw) >= 2 and len(raw.encode("utf-8")) <= MAX_HISTORY_SEARCH_TERM_BYTES:
            terms.add(raw)
        for part in _IDENTIFIER_PART_RE.findall(token):
            value = part.casefold()
            if len(value) >= 2 and len(value.encode("utf-8")) <= MAX_HISTORY_SEARCH_TERM_BYTES:
                terms.add(value)
    ordered = sorted(terms)
    return ordered[:MAX_HISTORY_SEARCH_TERMS], len(ordered) > MAX_HISTORY_SEARCH_TERMS


def _build_event(
    root: Path,
    *,
    receipt: dict[str, Any],
    receipt_path: str | Path,
    receipt_text: str,
    artifact_path: str | Path,
    artifact_text: str,
    policy: CompletionCataloguePolicy,
    sequence: int,
    previous_event_id: str,
    previous_prefix_digest: str,
) -> dict[str, Any]:
    task_id = str(receipt["task_id"])
    repo_id = _receipt_repo_id(receipt)
    if type(sequence) is not int or sequence < 1:
        raise ValueError("completion catalogue sequence is invalid")
    if sequence == 1:
        if previous_event_id or previous_prefix_digest != EMPTY_PREFIX_DIGEST:
            raise ValueError("first completion catalogue event has invalid ancestry")
    elif not _is_digest(previous_event_id) or not _is_digest(previous_prefix_digest):
        raise ValueError("completion catalogue event ancestry is invalid")
    canonical_receipt_text = receipt_text or _pretty_json(receipt)
    try:
        parsed_receipt_text = json.loads(canonical_receipt_text)
    except json.JSONDecodeError as exc:
        raise ValueError("completion receipt text is invalid JSON") from exc
    if parsed_receipt_text != receipt:
        raise ValueError("completion receipt text does not match receipt data")
    receipt_rel = _workspace_relative(root, receipt_path)
    artifact_value = artifact_path or str(receipt.get("task_path_at_completion") or "")
    artifact_rel = _workspace_relative(root, artifact_value)
    artifact_digest = str(receipt.get("content_sha256") or "")
    if not _is_digest(artifact_digest):
        raise ValueError("completion receipt artifact digest is invalid")
    if artifact_text and _digest_text(artifact_text) != artifact_digest:
        raise ValueError("completion artifact text does not match receipt content digest")
    subject_keys, total_subjects = _event_subjects(receipt, policy=policy)
    hot_witnesses = {
        subject_key: {
            "graph": _graph_subject_witness(receipt, subject_key=subject_key),
            **(
                {"outcome": outcome_summary}
                if (
                    outcome_summary := _outcome_summary(
                        receipt.get("discovery_outcome")
                        if isinstance(receipt.get("discovery_outcome"), Mapping)
                        else {},
                        subject_key=subject_key,
                    )
                )
                else {}
            ),
        }
        for subject_key in subject_keys
    }
    search_terms, search_terms_truncated = _event_search_terms(
        receipt,
        artifact_text=artifact_text,
    )
    base: dict[str, Any] = {
        "schema": "repoctl.completion_catalogue.event",
        "schema_version": CATALOGUE_SCHEMA_VERSION,
        "projector_version": CATALOGUE_PROJECTOR_VERSION,
        "policy_version": policy.version,
        "policy_digest": policy.digest,
        "repo_id": repo_id,
        "sequence": sequence,
        "previous_event_id": previous_event_id,
        "previous_prefix_digest": previous_prefix_digest,
        "task_id": task_id,
        "completed_at": str(receipt.get("completed_event_at") or receipt.get("completed_at") or ""),
        "receipt_path": receipt_rel,
        "receipt_sha256": _digest_text(canonical_receipt_text),
        "receipt_canonical_sha256": _digest_data(receipt),
        "artifact_path": artifact_rel,
        "artifact_sha256": artifact_digest,
        "subject_keys": subject_keys,
        "subject_count": total_subjects,
        "subjects_truncated": total_subjects > len(subject_keys),
        "search_terms": search_terms,
        "search_terms_truncated": search_terms_truncated,
        "status": str(receipt.get("status") or ""),
        "changed_count": len(receipt.get("changed_entries", [])),
        "hot_witnesses": hot_witnesses,
    }
    if len(_canonical_json(base).encode("utf-8")) > policy.max_catalogue_event_bytes:
        raise ValueError("completion catalogue event exceeds finite byte policy")
    return {**base, "event_id": _digest_data(base)}


def _event_prefix_digest(event: Mapping[str, Any]) -> str:
    return _digest_data(
        {
            "previous_prefix_digest": event["previous_prefix_digest"],
            "event_id": event["event_id"],
        }
    )


def _event_path(paths: CompletionCataloguePaths, event_id: str) -> Path:
    if not _is_digest(event_id):
        raise ValueError("completion catalogue event id is invalid")
    return paths.events_directory / f"{event_id.removeprefix('sha256:')}.json"


def _head_for_event(repo_id: str, policy: CompletionCataloguePolicy, event: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "schema": "repoctl.completion_catalogue.head",
        "schema_version": CATALOGUE_SCHEMA_VERSION,
        "projector_version": CATALOGUE_PROJECTOR_VERSION,
        "policy_version": policy.version,
        "policy_digest": policy.digest,
        "repo_id": repo_id,
        "last_sequence": event["sequence"],
        "last_event_id": event["event_id"],
        "prefix_digest": _event_prefix_digest(event),
    }


def _require_regular_file(root: Path, path: Path) -> None:
    if path.is_symlink() or (path.exists() and not path.is_file()):
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue state path is not a regular file", root=root, path=path)


def _read_json_state(root: Path, path: Path) -> dict[str, Any]:
    _require_regular_file(root, path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompletionCatalogueUnavailable(
            CompletionCatalogueUnavailableReason.CORRUPT,
            "completion catalogue state is unreadable",
            path=_relative_label(root, path),
        ) from exc
    if not isinstance(data, dict):
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue state must be an object", root=root, path=path)
    return data


def _validate_common_state(
    root: Path,
    path: Path,
    data: Mapping[str, Any],
    *,
    schema: str,
    repo_id: str,
    policy: CompletionCataloguePolicy,
) -> None:
    if data.get("schema") != schema or data.get("schema_version") != CATALOGUE_SCHEMA_VERSION:
        _unavailable(CompletionCatalogueUnavailableReason.SCHEMA_MISMATCH, "completion catalogue state schema is unsupported", root=root, path=path)
    if data.get("projector_version") != CATALOGUE_PROJECTOR_VERSION:
        _unavailable(CompletionCatalogueUnavailableReason.PROJECTOR_MISMATCH, "completion catalogue projector version is unsupported", root=root, path=path)
    if data.get("policy_version") != policy.version or data.get("policy_digest") != policy.digest:
        _unavailable(CompletionCatalogueUnavailableReason.POLICY_MISMATCH, "completion catalogue bounded policy does not match", root=root, path=path)
    if data.get("repo_id") != repo_id:
        _unavailable(CompletionCatalogueUnavailableReason.REPOSITORY_MISMATCH, "completion catalogue repository identity does not match", root=root, path=path)


def _load_head(
    paths: CompletionCataloguePaths,
    *,
    repo_id: str,
    policy: CompletionCataloguePolicy,
    required: bool,
) -> dict[str, Any] | None:
    root = paths.directory.parents[3]
    if not paths.head.exists() and not paths.head.is_symlink():
        if required:
            _unavailable(CompletionCatalogueUnavailableReason.MISSING, "completion catalogue head is missing", root=root, path=paths.head)
        return None
    data = _read_json_state(root, paths.head)
    _validate_common_state(root, paths.head, data, schema="repoctl.completion_catalogue.head", repo_id=repo_id, policy=policy)
    if type(data.get("last_sequence")) is not int or int(data["last_sequence"]) < 1:
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue head sequence is invalid", root=root, path=paths.head)
    if not _is_digest(data.get("last_event_id")) or not _is_digest(data.get("prefix_digest")):
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue head digest is invalid", root=root, path=paths.head)
    return data


def _validate_head_sidecar(paths: CompletionCataloguePaths, head: Mapping[str, Any], *, repo_id: str, policy: CompletionCataloguePolicy) -> None:
    root = paths.directory.parents[3]
    event = _load_event_sidecar(paths, str(head["last_event_id"]), repo_id=repo_id, policy=policy)
    if int(event["sequence"]) != int(head["last_sequence"]):
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue head does not match its event sequence", root=root, path=paths.head)
    if _event_prefix_digest(event) != head["prefix_digest"]:
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue head prefix does not match its event", root=root, path=paths.head)


def _require_no_or_empty_materialization(
    paths: CompletionCataloguePaths,
    *,
    repo_id: str,
    policy: CompletionCataloguePolicy,
) -> None:
    root = paths.directory.parents[3]
    materialized = [paths.catalogue, paths.checkpoint, *paths.projection_slots]
    if not any(path.exists() or path.is_symlink() for path in materialized):
        return
    checkpoint, projection = _load_committed_projection(
        paths,
        repo_id=repo_id,
        policy=policy,
        allow_empty=False,
    )
    expected_projection = _empty_projection(repo_id, policy)
    expected_checkpoint = _checkpoint_data(
        repo_id,
        policy,
        sequence=0,
        event_id="",
        prefix_digest=EMPTY_PREFIX_DIGEST,
        catalogue_size=0,
        prefix_window_digest=_digest_bytes(b""),
        projection_slot=0,
        projection_digest=_digest_data(expected_projection),
    )
    if (
        checkpoint == expected_checkpoint
        and projection == expected_projection
        and paths.catalogue.is_file()
        and not paths.catalogue.is_symlink()
        and paths.catalogue.stat().st_size == 0
    ):
        return
    _unavailable(
        CompletionCatalogueUnavailableReason.GAP,
        "completion catalogue materialization exists without an ingress head; run explicit rebuild",
        root=root,
        path=paths.directory,
    )


def _validate_event(
    root: Path,
    path: Path,
    event: Mapping[str, Any],
    *,
    repo_id: str,
    policy: CompletionCataloguePolicy,
) -> dict[str, Any]:
    expected_keys = {
        "schema",
        "schema_version",
        "projector_version",
        "policy_version",
        "policy_digest",
        "repo_id",
        "sequence",
        "previous_event_id",
        "previous_prefix_digest",
        "task_id",
        "completed_at",
        "receipt_path",
        "receipt_sha256",
        "receipt_canonical_sha256",
        "artifact_path",
        "artifact_sha256",
        "subject_keys",
        "subject_count",
        "subjects_truncated",
        "search_terms",
        "search_terms_truncated",
        "status",
        "changed_count",
        "hot_witnesses",
        "event_id",
    }
    if set(event) != expected_keys:
        _unavailable(CompletionCatalogueUnavailableReason.SCHEMA_MISMATCH, "completion catalogue event fields are unsupported", root=root, path=path)
    _validate_common_state(root, path, event, schema="repoctl.completion_catalogue.event", repo_id=repo_id, policy=policy)
    sequence = event.get("sequence")
    if type(sequence) is not int or sequence < 1:
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue event sequence is invalid", root=root, path=path)
    previous_event_id = event.get("previous_event_id")
    previous_prefix_digest = event.get("previous_prefix_digest")
    if sequence == 1:
        if previous_event_id != "" or previous_prefix_digest != EMPTY_PREFIX_DIGEST:
            _unavailable(CompletionCatalogueUnavailableReason.GAP, "first completion catalogue event ancestry is invalid", root=root, path=path)
    elif not _is_digest(previous_event_id) or not _is_digest(previous_prefix_digest):
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue event ancestry is invalid", root=root, path=path)
    task_id = event.get("task_id")
    if not isinstance(task_id, str) or _TASK_ID_RE.fullmatch(task_id) is None:
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event task identity is invalid", root=root, path=path)
    if not all(_is_digest(event.get(field)) for field in ("receipt_sha256", "receipt_canonical_sha256", "artifact_sha256", "event_id")):
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event digest is invalid", root=root, path=path)
    if event.get("status") != "done" or type(event.get("changed_count")) is not int or int(event["changed_count"]) < 0:
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event receipt summary is invalid", root=root, path=path)
    subject_keys = event.get("subject_keys")
    subject_count = event.get("subject_count")
    if not isinstance(subject_keys, list) or subject_keys != sorted(set(subject_keys)) or len(subject_keys) > policy.max_subjects_per_event:
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event subjects are not canonical bounded data", root=root, path=path)
    try:
        normalized_subjects = [_normalize_subject_key(value, policy=policy) for value in subject_keys]
    except ValueError as exc:
        raise CompletionCatalogueUnavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event subject is invalid", path=_relative_label(root, path)) from exc
    if normalized_subjects != subject_keys or type(subject_count) is not int or subject_count < len(subject_keys):
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event subject counts are invalid", root=root, path=path)
    if event.get("subjects_truncated") is not (subject_count > len(subject_keys)):
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event truncation marker is invalid", root=root, path=path)
    hot_witnesses = event.get("hot_witnesses")
    if not isinstance(hot_witnesses, dict) or set(hot_witnesses) != set(subject_keys):
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event hot witnesses do not match subjects", root=root, path=path)
    for subject_key, witness in hot_witnesses.items():
        if not isinstance(witness, dict) or not isinstance(witness.get("graph"), dict):
            _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event hot witness is invalid", root=root, path=path)
        outcome = witness.get("outcome")
        if outcome is not None:
            roles = outcome.get("subject_roles") if isinstance(outcome, dict) else None
            if not _subject_local_roles(roles, cell_key=subject_key, policy=policy):
                _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event outcome witness is not subject-local", root=root, path=path)
    search_terms = event.get("search_terms")
    if (
        not isinstance(search_terms, list)
        or search_terms != sorted(set(search_terms))
        or len(search_terms) > MAX_HISTORY_SEARCH_TERMS
        or any(
            not isinstance(term, str)
            or not term
            or term != term.casefold()
            or len(term.encode("utf-8")) > MAX_HISTORY_SEARCH_TERM_BYTES
            for term in search_terms
        )
        or type(event.get("search_terms_truncated")) is not bool
    ):
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue search terms are invalid", root=root, path=path)
    base = {key: value for key, value in event.items() if key != "event_id"}
    if len(_canonical_json(base).encode("utf-8")) > policy.max_catalogue_event_bytes:
        _unavailable(
            CompletionCatalogueUnavailableReason.CORRUPT,
            "completion catalogue event exceeds finite byte policy",
            root=root,
            path=path,
        )
    if _digest_data(base) != event["event_id"]:
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue event identity digest does not match", root=root, path=path)
    return _json_copy(event)


def _load_event_sidecar(
    paths: CompletionCataloguePaths,
    event_id: str,
    *,
    repo_id: str,
    policy: CompletionCataloguePolicy,
) -> dict[str, Any]:
    root = paths.directory.parents[3]
    path = _event_path(paths, event_id)
    if not path.is_file() or path.is_symlink():
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue event sidecar is missing", root=root, path=path)
    try:
        text = path.read_text(encoding="utf-8")
        data = json.loads(text)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise CompletionCatalogueUnavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event sidecar is unreadable", path=_relative_label(root, path)) from exc
    if not isinstance(data, dict) or text != _canonical_line(data):
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue event sidecar is not canonical JSON", root=root, path=path)
    event = _validate_event(root, path, data, repo_id=repo_id, policy=policy)
    if event["event_id"] != event_id:
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue event path identity does not match", root=root, path=path)
    return event


def _empty_projection(repo_id: str, policy: CompletionCataloguePolicy) -> dict[str, Any]:
    next_projection = {
        "schema": "repoctl.completion_catalogue.projection",
        "schema_version": CATALOGUE_SCHEMA_VERSION,
        "projector_version": CATALOGUE_PROJECTOR_VERSION,
        "policy_version": policy.version,
        "policy_digest": policy.digest,
        "repo_id": repo_id,
        "last_sequence": 0,
        "last_event_id": "",
        "prefix_digest": EMPTY_PREFIX_DIGEST,
        "eviction_count": 0,
        "truncated_event_count": 0,
        "subjects": [],
    }
    return next_projection


def _projection_record(
    event: Mapping[str, Any],
    *,
    subject_key: str,
    policy: CompletionCataloguePolicy,
) -> dict[str, Any]:
    witnesses = event.get("hot_witnesses") if isinstance(event.get("hot_witnesses"), Mapping) else {}
    witness = witnesses.get(subject_key) if isinstance(witnesses.get(subject_key), Mapping) else {}
    graph = witness.get("graph") if isinstance(witness.get("graph"), Mapping) else {}
    outcome_summary = witness.get("outcome") if isinstance(witness.get("outcome"), Mapping) else {}
    for path_value in (
        event["receipt_path"],
        event["artifact_path"],
        graph.get("task_path_at_completion") or "",
    ):
        if not isinstance(path_value, str) or len(path_value.encode("utf-8")) > policy.max_hot_path_bytes:
            raise ValueError("completion catalogue hot path exceeds finite byte policy")
    record = {
        "event_id": event["event_id"],
        "sequence": event["sequence"],
        "task_id": event["task_id"],
        "completed_at": event["completed_at"],
        "receipt_path": event["receipt_path"],
        "receipt_sha256": event["receipt_sha256"],
        "artifact_path": event["artifact_path"],
        "artifact_sha256": event["artifact_sha256"],
        "status": str(event.get("status") or ""),
        "changed_count": int(event.get("changed_count") or 0),
        "subject_count": event["subject_count"],
        "subjects_truncated": event["subjects_truncated"],
        "graph": _json_copy(graph),
        **({"outcome": outcome_summary} if outcome_summary else {}),
    }
    if len(_canonical_json(record).encode("utf-8")) > policy.max_hot_record_bytes:
        raise ValueError("completion catalogue hot record exceeds finite byte policy")
    return record


def _subject_local_roles(
    roles: object,
    *,
    cell_key: str,
    policy: CompletionCataloguePolicy,
) -> bool:
    if not isinstance(roles, Mapping) or len(roles) != 1:
        return False
    canonical_key, role = next(iter(roles.items()))
    if not isinstance(canonical_key, str) or not isinstance(role, Mapping):
        return False
    if role.get("key") != canonical_key:
        return False
    try:
        return versioned_completion_subject_key(
            canonical_key,
            str(role.get("version_digest") or ""),
            policy=policy,
        ) == cell_key
    except ValueError:
        return False


def _projection_event_ids(projection: Mapping[str, Any]) -> list[str]:
    return sorted(
        {
            str(record.get("event_id") or "")
            for subject in projection.get("subjects", [])
            if isinstance(subject, Mapping)
            for record in subject.get("frontier", [])
            if isinstance(record, Mapping) and _is_digest(record.get("event_id"))
        }
    )


def _outcome_summary(outcome: Mapping[str, Any], *, subject_key: str) -> dict[str, Any]:
    subjects = {
        str(item.get("id") or ""): item
        for item in outcome.get("subjects", [])
        if isinstance(item, Mapping) and isinstance(item.get("id"), str)
    }
    if not subjects:
        return {}
    verification_by_subject: dict[str, list[str]] = {}
    for record in outcome.get("verification_records", []):
        if not isinstance(record, Mapping):
            continue
        status = str(record.get("status") or "")
        for subject_id in record.get("subject_ids", []):
            if str(subject_id) in subjects:
                verification_by_subject.setdefault(str(subject_id), []).append(status)
    role: dict[str, Any] | None = None
    chosen = {str(value) for value in outcome.get("active_chosen", [])}
    for episode in outcome.get("episodes", []):
        if not isinstance(episode, Mapping):
            continue
        reviewed = {str(value) for value in episode.get("reviewed", [])}
        excluded = {str(value) for value in episode.get("excluded", [])}
        outside = {str(value) for value in episode.get("outside_candidate_set", [])}
        for subject_id in reviewed | excluded | outside | chosen:
            subject = subjects.get(subject_id)
            if subject is None:
                continue
            try:
                cell_key = versioned_completion_subject_key(
                    str(subject.get("key") or ""),
                    str(subject.get("version_digest") or ""),
                )
            except ValueError:
                continue
            if cell_key != subject_key:
                continue
            if role is None:
                role = {
                    "kind": str(subject.get("kind") or ""),
                    "key": str(subject.get("key") or ""),
                    "identity": _json_copy(subject.get("identity") or {}),
                    "version_digest": str(subject.get("version_digest") or ""),
                    "reviewed": False,
                    "excluded": False,
                    "chosen": False,
                    "outside_candidate_set": False,
                    "verification_statuses": [],
                }
            entry = role
            entry["reviewed"] = bool(entry["reviewed"] or subject_id in reviewed)
            entry["excluded"] = bool(entry["excluded"] or subject_id in excluded)
            entry["chosen"] = bool(entry["chosen"] or subject_id in chosen)
            entry["outside_candidate_set"] = bool(entry["outside_candidate_set"] or subject_id in outside)
            entry["verification_statuses"] = sorted(
                set(entry["verification_statuses"]) | set(verification_by_subject.get(subject_id, []))
            )
    return ({
        "outcome_digest": str(outcome.get("outcome_digest") or ""),
        "subject_roles": {str(role["key"]): role},
    } if role is not None else {})


def _graph_subject_witness(receipt: Mapping[str, Any], *, subject_key: str) -> dict[str, Any]:
    path = subject_key.removeprefix("file:") if subject_key.startswith("file:") else ""
    outcome = receipt.get("discovery_outcome")
    if not path and isinstance(outcome, Mapping):
        for subject in outcome.get("subjects", []):
            if not isinstance(subject, Mapping):
                continue
            try:
                matches = versioned_completion_subject_key(
                    str(subject.get("key") or ""),
                    str(subject.get("version_digest") or ""),
                ) == subject_key
            except ValueError:
                matches = False
            identity = subject.get("identity")
            if matches and isinstance(identity, Mapping) and isinstance(identity.get("path"), str):
                path = str(identity["path"])
                break
    changed_entry: dict[str, str] | None = None
    if path:
        for raw in receipt.get("changed_entries", []):
            if not isinstance(raw, Mapping) or path not in {raw.get("path"), raw.get("old_path")}:
                continue
            changed_entry = {
                key: str(raw[key])
                for key in ("change", "path", "old_path")
                if isinstance(raw.get(key), str) and raw[key]
            }
            break
    repo_evidence = receipt.get("repo_evidence") if isinstance(receipt.get("repo_evidence"), Mapping) else {}
    verification = receipt.get("verification") if isinstance(receipt.get("verification"), Mapping) else {}
    return {
        "task_path_at_completion": str(receipt.get("task_path_at_completion") or ""),
        "repo_evidence": {
            "attribution": str(repo_evidence.get("attribution") or "none"),
        },
        "verification": {
            key: verification[key]
            for key in ("source_sha256", "normalized_sha256", "stored_sha256", "truncated")
            if key in verification and isinstance(verification[key], (str, bool))
        },
        **({"changed_entry": changed_entry} if changed_entry is not None else {}),
    }


def _apply_event(projection: Mapping[str, Any], event: Mapping[str, Any], *, policy: CompletionCataloguePolicy) -> dict[str, Any]:
    subjects = {str(item["subject_key"]): _json_copy(item) for item in projection["subjects"]}
    for subject_key in event["subject_keys"]:
        record = _projection_record(event, subject_key=subject_key, policy=policy)
        current = subjects.get(
            subject_key,
            {
                "subject_key": subject_key,
                "last_touched_sequence": 0,
                "overflow_count": 0,
                "frontier": [],
            },
        )
        frontier = [item for item in current["frontier"] if item.get("task_id") != event["task_id"]]
        frontier.insert(0, _json_copy(record))
        current["overflow_count"] = int(current["overflow_count"]) + max(
            0,
            len(frontier) - policy.max_frontier_per_subject,
        )
        current["frontier"] = frontier[: policy.max_frontier_per_subject]
        current["last_touched_sequence"] = event["sequence"]
        subjects[subject_key] = current
    evicted = 0
    if len(subjects) > policy.max_subjects:
        ranked = sorted(subjects.values(), key=lambda item: (-int(item["last_touched_sequence"]), str(item["subject_key"])))
        evicted = len(ranked) - policy.max_subjects
        subjects = {str(item["subject_key"]): item for item in ranked[: policy.max_subjects]}
    next_projection = {
        "schema": "repoctl.completion_catalogue.projection",
        "schema_version": CATALOGUE_SCHEMA_VERSION,
        "projector_version": CATALOGUE_PROJECTOR_VERSION,
        "policy_version": policy.version,
        "policy_digest": policy.digest,
        "repo_id": projection["repo_id"],
        "last_sequence": event["sequence"],
        "last_event_id": event["event_id"],
        "prefix_digest": _event_prefix_digest(event),
        "eviction_count": int(projection["eviction_count"]) + evicted,
        "truncated_event_count": int(projection["truncated_event_count"])
        + (1 if event["subjects_truncated"] else 0),
        "subjects": sorted(subjects.values(), key=lambda item: str(item["subject_key"])),
    }
    if len(_canonical_json(next_projection).encode("utf-8")) > policy.max_hot_projection_bytes:
        raise ValueError("completion catalogue hot projection exceeds finite byte policy")
    return next_projection


def _checkpoint_data(
    repo_id: str,
    policy: CompletionCataloguePolicy,
    *,
    sequence: int,
    event_id: str,
    prefix_digest: str,
    catalogue_size: int,
    prefix_window_digest: str,
    projection_slot: int,
    projection_digest: str,
) -> dict[str, Any]:
    return {
        "schema": "repoctl.completion_catalogue.checkpoint",
        "schema_version": CATALOGUE_SCHEMA_VERSION,
        "projector_version": CATALOGUE_PROJECTOR_VERSION,
        "policy_version": policy.version,
        "policy_digest": policy.digest,
        "repo_id": repo_id,
        "source_identity": _digest_data({"receipt_directory": "docs/tasks/.repoctl-state/completions", "repo_id": repo_id}),
        "last_sequence": sequence,
        "last_event_id": event_id,
        "prefix_digest": prefix_digest,
        "catalogue_size_bytes": catalogue_size,
        "prefix_window_bytes": PREFIX_WINDOW_BYTES,
        "prefix_window_digest": prefix_window_digest,
        "projection_slot": projection_slot,
        "projection_digest": projection_digest,
    }


def _load_committed_projection(
    paths: CompletionCataloguePaths,
    *,
    repo_id: str,
    policy: CompletionCataloguePolicy,
    allow_empty: bool,
) -> tuple[dict[str, Any] | None, dict[str, Any] | None]:
    root = paths.directory.parents[3]
    if not paths.checkpoint.exists() and not paths.checkpoint.is_symlink():
        if any(path.exists() or path.is_symlink() for path in (paths.catalogue, *paths.projection_slots)):
            _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue state exists without a checkpoint", root=root, path=paths.directory)
        if allow_empty:
            return None, None
        _unavailable(CompletionCatalogueUnavailableReason.MISSING, "completion catalogue has not been materialized", root=root, path=paths.checkpoint)
    checkpoint = _read_json_state(root, paths.checkpoint)
    _validate_common_state(root, paths.checkpoint, checkpoint, schema="repoctl.completion_catalogue.checkpoint", repo_id=repo_id, policy=policy)
    sequence = checkpoint.get("last_sequence")
    size = checkpoint.get("catalogue_size_bytes")
    slot = checkpoint.get("projection_slot")
    if type(sequence) is not int or sequence < 0 or type(size) is not int or size < 0 or type(slot) is not int or slot not in {0, 1}:
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue checkpoint counters are invalid", root=root, path=paths.checkpoint)
    if not _is_digest(checkpoint.get("prefix_digest")) or not _is_digest(checkpoint.get("prefix_window_digest")) or not _is_digest(checkpoint.get("projection_digest")):
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue checkpoint digest is invalid", root=root, path=paths.checkpoint)
    if sequence == 0:
        if checkpoint.get("last_event_id") != "" or checkpoint.get("prefix_digest") != EMPTY_PREFIX_DIGEST or size != 0:
            _unavailable(CompletionCatalogueUnavailableReason.GAP, "empty completion catalogue checkpoint is inconsistent", root=root, path=paths.checkpoint)
    elif not _is_digest(checkpoint.get("last_event_id")):
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue checkpoint event identity is invalid", root=root, path=paths.checkpoint)
    projection_path = paths.projection_slots[slot]
    if not projection_path.is_file() or projection_path.is_symlink():
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue projection generation is missing", root=root, path=projection_path)
    projection = _read_json_state(root, projection_path)
    _validate_common_state(root, projection_path, projection, schema="repoctl.completion_catalogue.projection", repo_id=repo_id, policy=policy)
    _validate_projection(root, projection_path, projection, policy=policy)
    if _digest_data(projection) != checkpoint["projection_digest"]:
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue projection binding does not match", root=root, path=projection_path)
    for field in ("last_sequence", "last_event_id", "prefix_digest"):
        if projection.get(field) != checkpoint.get(field):
            _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, f"completion catalogue projection {field} does not match checkpoint", root=root, path=projection_path)
    return checkpoint, projection


def _validate_projection(root: Path, path: Path, projection: Mapping[str, Any], *, policy: CompletionCataloguePolicy) -> None:
    if len(_canonical_json(projection).encode("utf-8")) > policy.max_hot_projection_bytes:
        _unavailable(
            CompletionCatalogueUnavailableReason.CORRUPT,
            "completion catalogue projection exceeds finite byte policy",
            root=root,
            path=path,
        )
    subjects = projection.get("subjects")
    if not isinstance(subjects, list) or len(subjects) > policy.max_subjects:
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue projection subjects exceed policy", root=root, path=path)
    keys: list[str] = []
    for subject in subjects:
        if not isinstance(subject, dict) or set(subject) != {
            "subject_key",
            "last_touched_sequence",
            "overflow_count",
            "frontier",
        }:
            _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue projection subject is invalid", root=root, path=path)
        try:
            key = _normalize_subject_key(subject.get("subject_key"), policy=policy)
        except (TypeError, ValueError) as exc:
            raise CompletionCatalogueUnavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue projection subject key is invalid", path=_relative_label(root, path)) from exc
        frontier = subject.get("frontier")
        if (
            type(subject.get("last_touched_sequence")) is not int
            or type(subject.get("overflow_count")) is not int
            or int(subject["overflow_count"]) < 0
            or not isinstance(frontier, list)
            or len(frontier) > policy.max_frontier_per_subject
        ):
            _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue projection frontier exceeds policy", root=root, path=path)
        if any(
            not isinstance(record, Mapping)
            or not _is_digest(record.get("event_id"))
            or type(record.get("sequence")) is not int
            or len(_canonical_json(record).encode("utf-8")) > policy.max_hot_record_bytes
            for record in frontier
        ):
            _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue projection frontier record is invalid", root=root, path=path)
        for record in frontier:
            outcome = record.get("outcome") if isinstance(record.get("outcome"), Mapping) else None
            if outcome is not None:
                roles = outcome.get("subject_roles")
                if not _subject_local_roles(roles, cell_key=key, policy=policy):
                    _unavailable(
                        CompletionCatalogueUnavailableReason.CORRUPT,
                        "completion catalogue projection outcome is not subject-local",
                        root=root,
                        path=path,
                    )
            graph = record.get("graph")
            if not isinstance(graph, Mapping):
                _unavailable(
                    CompletionCatalogueUnavailableReason.CORRUPT,
                    "completion catalogue projection Graph witness is invalid",
                    root=root,
                    path=path,
                )
            for graph_path in (
                record.get("receipt_path"),
                record.get("artifact_path"),
                graph.get("task_path_at_completion"),
            ):
                if not isinstance(graph_path, str) or len(graph_path.encode("utf-8")) > policy.max_hot_path_bytes:
                    _unavailable(
                        CompletionCatalogueUnavailableReason.CORRUPT,
                        "completion catalogue projection path exceeds finite byte policy",
                        root=root,
                        path=path,
                    )
        keys.append(key)
    if (
        keys != sorted(set(keys))
        or type(projection.get("eviction_count")) is not int
        or int(projection["eviction_count"]) < 0
        or type(projection.get("truncated_event_count")) is not int
        or int(projection["truncated_event_count"]) < 0
    ):
        _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue projection is not canonical bounded data", root=root, path=path)


def _validate_catalogue_prefix(paths: CompletionCataloguePaths, checkpoint: Mapping[str, Any] | None, *, root: Path) -> None:
    if checkpoint is None:
        return
    size = int(checkpoint["catalogue_size_bytes"])
    if size == 0:
        if paths.catalogue.exists() and (not paths.catalogue.is_file() or paths.catalogue.is_symlink()):
            _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue path is invalid", root=root, path=paths.catalogue)
        return
    if not paths.catalogue.is_file() or paths.catalogue.is_symlink():
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue cold log is missing", root=root, path=paths.catalogue)
    actual_size = paths.catalogue.stat().st_size
    if actual_size < size:
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue cold log was truncated before checkpoint", root=root, path=paths.catalogue)
    if _prefix_window_digest(paths.catalogue, size) != checkpoint["prefix_window_digest"]:
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue checkpoint prefix window does not match cold log", root=root, path=paths.catalogue)


def _prefix_window_digest(path: Path, end: int) -> str:
    if end == 0:
        return _digest_bytes(b"")
    with path.open("rb") as handle:
        handle.seek(max(0, end - PREFIX_WINDOW_BYTES))
        return _digest_bytes(handle.read(min(end, PREFIX_WINDOW_BYTES)))


def _read_catalogue_tail(
    paths: CompletionCataloguePaths,
    *,
    offset: int,
    repo_id: str,
    policy: CompletionCataloguePolicy,
    previous_sequence: int,
    previous_event_id: str,
    previous_prefix_digest: str,
) -> list[dict[str, Any]]:
    root = paths.directory.parents[3]
    if not paths.catalogue.exists():
        return []
    _require_regular_file(root, paths.catalogue)
    try:
        with paths.catalogue.open("rb") as handle:
            if offset:
                handle.seek(offset - 1)
                if handle.read(1) != b"\n":
                    _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue checkpoint is not at a line boundary", root=root, path=paths.catalogue)
            handle.seek(offset)
            tail_bytes = handle.read()
        tail_text = tail_bytes.decode("utf-8")
    except (OSError, UnicodeDecodeError) as exc:
        raise CompletionCatalogueUnavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue tail is unreadable", path=_relative_label(root, paths.catalogue)) from exc
    if not tail_text:
        return []
    if not tail_text.endswith("\n"):
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue tail contains a partial event", root=root, path=paths.catalogue)
    events: list[dict[str, Any]] = []
    sequence = previous_sequence
    event_id = previous_event_id
    prefix_digest = previous_prefix_digest
    for line in tail_text.splitlines(keepends=True):
        try:
            data = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CompletionCatalogueUnavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue JSONL event is unreadable", path=_relative_label(root, paths.catalogue)) from exc
        if not isinstance(data, dict) or line != _canonical_line(data):
            _unavailable(CompletionCatalogueUnavailableReason.CORRUPT, "completion catalogue JSONL is not canonical", root=root, path=paths.catalogue)
        event = _validate_event(root, paths.catalogue, data, repo_id=repo_id, policy=policy)
        _validate_event_link(root, paths.catalogue, event, sequence=sequence, event_id=event_id, prefix_digest=prefix_digest)
        events.append(event)
        sequence = int(event["sequence"])
        event_id = str(event["event_id"])
        prefix_digest = _event_prefix_digest(event)
    return events


def _validate_event_link(root: Path, path: Path, event: Mapping[str, Any], *, sequence: int, event_id: str, prefix_digest: str) -> None:
    if event["sequence"] != sequence + 1 or event["previous_event_id"] != event_id:
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue event sequence has a gap", root=root, path=path)
    if event["previous_prefix_digest"] != prefix_digest:
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue event prefix chain does not match", root=root, path=path)


def _pending_sidecar_events(
    paths: CompletionCataloguePaths,
    *,
    head: Mapping[str, Any] | None,
    repo_id: str,
    policy: CompletionCataloguePolicy,
    anchor_sequence: int,
    anchor_event_id: str,
    anchor_prefix_digest: str,
) -> list[dict[str, Any]]:
    root = paths.directory.parents[3]
    if head is None:
        return []
    head_sequence = int(head["last_sequence"])
    if head_sequence < anchor_sequence:
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue head is behind the cold log", root=root, path=paths.head)
    if head_sequence == anchor_sequence:
        if head["last_event_id"] != anchor_event_id or head["prefix_digest"] != anchor_prefix_digest:
            _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue head does not bind the cold log prefix", root=root, path=paths.head)
        return []
    reverse_events: list[dict[str, Any]] = []
    cursor = str(head["last_event_id"])
    remaining = head_sequence - anchor_sequence
    for _index in range(remaining):
        event = _load_event_sidecar(paths, cursor, repo_id=repo_id, policy=policy)
        reverse_events.append(event)
        cursor = str(event["previous_event_id"])
    pending = list(reversed(reverse_events))
    sequence = anchor_sequence
    event_id = anchor_event_id
    prefix_digest = anchor_prefix_digest
    for event in pending:
        _validate_event_link(root, _event_path(paths, str(event["event_id"])), event, sequence=sequence, event_id=event_id, prefix_digest=prefix_digest)
        sequence = int(event["sequence"])
        event_id = str(event["event_id"])
        prefix_digest = _event_prefix_digest(event)
    if sequence != head_sequence or event_id != head["last_event_id"] or prefix_digest != head["prefix_digest"]:
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue pending chain does not reach its head", root=root, path=paths.head)
    return pending


def _append_catalogue_events(paths: CompletionCataloguePaths, events: Iterable[Mapping[str, Any]], *, expected_size: int) -> None:
    paths.directory.mkdir(parents=True, exist_ok=True)
    current_size = paths.catalogue.stat().st_size if paths.catalogue.is_file() else 0
    if current_size != expected_size:
        _unavailable(CompletionCatalogueUnavailableReason.GAP, "completion catalogue changed during tail ingest", root=paths.directory.parents[3], path=paths.catalogue)
    payload = "".join(_canonical_line(event) for event in events).encode("utf-8")
    descriptor = os.open(paths.catalogue, os.O_WRONLY | os.O_APPEND | os.O_CREAT, 0o600)
    try:
        view = memoryview(payload)
        while view:
            written = os.write(descriptor, view)
            if written <= 0:
                raise OSError("short completion catalogue append")
            view = view[written:]
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _refresh_result(
    root: Path,
    paths: CompletionCataloguePaths,
    checkpoint: Mapping[str, Any],
    *,
    mode: str,
    changed: bool,
    ingested_count: int,
) -> CompletionCatalogueRefresh:
    slot = int(checkpoint["projection_slot"])
    return CompletionCatalogueRefresh(
        repo_id=str(checkpoint["repo_id"]),
        mode=mode,
        changed=changed,
        ingested_count=ingested_count,
        last_sequence=int(checkpoint["last_sequence"]),
        last_event_id=str(checkpoint["last_event_id"]),
        prefix_digest=str(checkpoint["prefix_digest"]),
        checkpoint_path=_relative_label(root, paths.checkpoint),
        projection_path=_relative_label(root, paths.projection_slots[slot]),
    )


def _scan_catalogue(paths: CompletionCataloguePaths, *, repo_id: str, policy: CompletionCataloguePolicy) -> list[dict[str, Any]]:
    root = paths.directory.parents[3]
    if not paths.catalogue.is_file() or paths.catalogue.is_symlink():
        _unavailable(CompletionCatalogueUnavailableReason.MISSING, "completion catalogue cold log is missing", root=root, path=paths.catalogue)
    return _read_catalogue_tail(
        paths,
        offset=0,
        repo_id=repo_id,
        policy=policy,
        previous_sequence=0,
        previous_event_id="",
        previous_prefix_digest=EMPTY_PREFIX_DIGEST,
    )


def _validate_scan_against_state(
    root: Path,
    paths: CompletionCataloguePaths,
    checkpoint: Mapping[str, Any],
    projection: Mapping[str, Any],
    events: list[dict[str, Any]],
    *,
    repo_id: str,
    policy: CompletionCataloguePolicy,
    verify_sidecars: bool,
) -> None:
    seen_tasks: set[str] = set()
    replay = _empty_projection(repo_id, policy)
    for event in events:
        task_id = str(event["task_id"])
        if task_id in seen_tasks:
            _unavailable(CompletionCatalogueUnavailableReason.DUPLICATE_TASK, f"duplicate completion task in catalogue: {task_id}", root=root, path=paths.catalogue)
        seen_tasks.add(task_id)
        replay = _apply_event(replay, event, policy=policy)
        if verify_sidecars:
            sidecar = _load_event_sidecar(paths, str(event["event_id"]), repo_id=repo_id, policy=policy)
            if sidecar != event:
                _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue sidecar differs from cold event", root=root, path=_event_path(paths, str(event["event_id"])))
    last = events[-1] if events else None
    expected_sequence = int(last["sequence"]) if last else 0
    expected_event_id = str(last["event_id"]) if last else ""
    expected_prefix = _event_prefix_digest(last) if last else EMPTY_PREFIX_DIGEST
    if (
        checkpoint["last_sequence"] != expected_sequence
        or checkpoint["last_event_id"] != expected_event_id
        or checkpoint["prefix_digest"] != expected_prefix
        or checkpoint["catalogue_size_bytes"] != paths.catalogue.stat().st_size
    ):
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue full prefix does not match checkpoint", root=root, path=paths.checkpoint)
    if replay != projection:
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue replay does not match hot projection", root=root, path=paths.projection_slots[int(checkpoint["projection_slot"])])
    head = _load_head(paths, repo_id=repo_id, policy=policy, required=bool(events))
    if not events:
        if head is not None:
            _unavailable(CompletionCatalogueUnavailableReason.GAP, "empty completion catalogue has a non-empty head", root=root, path=paths.head)
    elif head is None or head["last_sequence"] != expected_sequence or head["last_event_id"] != expected_event_id or head["prefix_digest"] != expected_prefix:
        _unavailable(CompletionCatalogueUnavailableReason.PREFIX_MISMATCH, "completion catalogue head does not match full prefix", root=root, path=paths.head)


def _explicit_receipt_inputs(
    root: Path,
    repo_id: str,
    receipt_artifacts: Iterable[CompletionReceiptInput | Mapping[str, Any] | Any] | None,
) -> list[CompletionReceiptInput]:
    if receipt_artifacts is None:
        # This is deliberately lazy: only explicit rebuild/full-audit paths may
        # invoke the legacy receipt collector and its O(N) directory scan.
        from .tasks import collect_completion_receipt_collection

        collection = collect_completion_receipt_collection(root, repo_id=repo_id)
        if collection.problems:
            messages = "; ".join(problem.message for problem in collection.problems[:3])
            _unavailable(CompletionCatalogueUnavailableReason.SOURCE_AUDIT_FAILED, f"validated completion receipt audit failed: {messages}", root=root)
        receipt_artifacts = collection.artifacts
    inputs: list[CompletionReceiptInput] = []
    for artifact in receipt_artifacts:
        if isinstance(artifact, CompletionReceiptInput):
            inputs.append(artifact)
            continue
        if isinstance(artifact, Mapping):
            receipt = artifact.get("receipt", artifact)
            receipt_path = artifact.get("receipt_path", "")
            receipt_text = artifact.get("receipt_text", "")
            artifact_path = artifact.get("artifact_path", artifact.get("resolved_path", ""))
            artifact_text = artifact.get("artifact_text", "")
        else:
            receipt = getattr(artifact, "receipt", None)
            receipt_path = getattr(artifact, "receipt_path", "")
            receipt_text = getattr(artifact, "receipt_text", "")
            artifact_path = getattr(artifact, "resolved_path", "")
            artifact_text = getattr(artifact, "artifact_text", "")
        if not isinstance(receipt, Mapping):
            _unavailable(CompletionCatalogueUnavailableReason.SOURCE_AUDIT_FAILED, "completion receipt audit input is invalid", root=root)
        normalized_receipt = _receipt_dict(receipt)
        inputs.append(
            CompletionReceiptInput(
                receipt=normalized_receipt,
                receipt_path=receipt_path,
                receipt_text=str(receipt_text or _pretty_json(normalized_receipt)),
                artifact_path=artifact_path or str(normalized_receipt.get("task_path_at_completion") or ""),
                artifact_text=str(artifact_text or ""),
            )
        )
    return inputs

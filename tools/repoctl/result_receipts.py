from __future__ import annotations

import json
import os
import re
import time
from dataclasses import dataclass
from enum import StrEnum
from pathlib import Path
from typing import Any, Iterable

from .context_model import CONTEXT_SOURCE_KIND_VALUES, ContextResultMode
from .graph_model import (
    GRAPH_QUERY_SELECTOR_SCHEMAS,
    GraphQuerySelectorKind,
    canonical_graph_query_selector,
    digest_data,
)
from .io import RepoctlError, atomic_write, decode_schema_version, repoctl_lock, write_temporary_text
from .repositories import RepoTarget


RESULT_RECEIPT_SCHEMA = "repoctl.repository-understanding.result-receipt"
RESULT_RECEIPT_SCHEMA_VERSION = 2
RESULT_RECEIPT_PROJECTION_SCHEMA = "repoctl.repository-understanding.result-receipt-projection"
RESULT_RECEIPT_PROJECTION_SCHEMA_VERSION = 1
RESULT_RECEIPT_ROOT = Path(".repoctl-state/result-receipts")
RESULT_CACHE_INDEX = RESULT_RECEIPT_ROOT / "index.json"
RESULT_CACHE_INDEX_SCHEMA = "repoctl.repository-understanding.result-receipt-cache-index"
RESULT_CACHE_INDEX_SCHEMA_VERSION = 1
RESULT_CACHE_MAX_ENTRIES = 256
RESULT_CACHE_MAX_BYTES = 8 * 1024 * 1024
RESULT_CACHE_MAX_AGE_SECONDS = 7 * 24 * 60 * 60
_DIGEST_RE = re.compile(r"sha256:[0-9a-f]{64}")


class ResultProducer(StrEnum):
    CONTEXT = "context"
    GRAPH = "graph"


class ResultAuthority(StrEnum):
    SOURCE = "source"
    GRAPH = "graph"
    DOCUMENT = "document"
    TASK_HISTORY = "task_history"
    KNOWLEDGE = "knowledge"


@dataclass(frozen=True)
class ContextResultRequest:
    query: str
    mode: str

    def __post_init__(self) -> None:
        if not isinstance(self.query, str) or not isinstance(self.mode, str):
            raise ValueError("context result request fields must be strings")
        if not self.query.strip() or self.query != self.query.strip():
            raise ValueError("context result request query must be a canonical non-empty value")
        ContextResultMode(self.mode)

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "context_query", "query": self.query, "mode": self.mode}

    @property
    def seed_query(self) -> str:
        return self.query


@dataclass(frozen=True)
class GraphResultRequest:
    selector: dict[str, Any]

    def __post_init__(self) -> None:
        canonical = canonical_graph_query_selector(self.selector)
        if self.selector != canonical:
            raise ValueError("graph result request selector must be canonical")

    @classmethod
    def from_query(cls, query: dict[str, Any]) -> "GraphResultRequest":
        return cls(selector=canonical_graph_query_selector(query))

    def to_dict(self) -> dict[str, Any]:
        return {"kind": "graph_query", "selector": dict(self.selector)}

    @property
    def seed_query(self) -> str:
        kind = GraphQuerySelectorKind(self.selector["type"])
        return self.selector[GRAPH_QUERY_SELECTOR_SCHEMAS[kind].value_field]


ResultRequest = ContextResultRequest | GraphResultRequest


@dataclass(frozen=True)
class ResultEpisode:
    id: str
    seed_query: str


@dataclass(frozen=True, order=True)
class ResultSelection:
    authority: ResultAuthority
    ref: str

    def __post_init__(self) -> None:
        if not isinstance(self.ref, str):
            raise ValueError("result selection reference must be a string")
        if not self.ref.strip() or self.ref != self.ref.strip():
            raise ValueError("result selection reference must be a non-empty canonical value")

    def to_dict(self) -> dict[str, str]:
        return {"authority": self.authority.value, "ref": self.ref}


def write_result_receipt(
    root: Path,
    *,
    target: RepoTarget,
    producer: ResultProducer,
    result_id: str,
    request: ResultRequest,
    selections: Iterable[ResultSelection],
) -> dict[str, Any]:
    _require_digest(result_id, field="result_id")
    _require_request_producer(producer, request)
    canonical_selections = sorted(set(selections))
    basis: dict[str, Any] = {
        "schema": RESULT_RECEIPT_SCHEMA,
        "schema_version": RESULT_RECEIPT_SCHEMA_VERSION,
        "producer": producer.value,
        "repository": target.to_dict(),
        "result_id": result_id,
        "request": request.to_dict(),
        "selectable": [selection.to_dict() for selection in canonical_selections],
    }
    receipt = {**basis, "receipt_digest": digest_data(basis)}
    path = result_receipt_path(root, target=target, producer=producer, result_id=result_id)
    receipt_text = _canonical_json(receipt)
    receipt_path = _validate_result_receipt_path(root, path)
    if len(receipt_text.encode("utf-8")) > RESULT_CACHE_MAX_BYTES:
        raise RepoctlError(
            "result receipt exceeds the finite result-cache byte limit",
            code="result_receipt_too_large",
            path=receipt_path,
        )
    with repoctl_lock(root):
        index = _reconciled_cache_index(root)
        _validate_result_receipt_path(root, path)
        path.parent.mkdir(parents=True, exist_ok=True)
        _validate_result_receipt_path(root, path)
        candidate = write_temporary_text(path, receipt_text, suffix=".candidate")
        try:
            try:
                os.link(candidate, path)
                stored = receipt
            except FileExistsError:
                stored = read_result_receipt(root, path)
                if stored != receipt:
                    raise RepoctlError(
                        "result receipt identity already exists with different selectable content",
                        code="result_receipt_conflict",
                        path=path.relative_to(root).as_posix(),
                    )
            protected_key = _index_receipt(root, index=index, path=path, receipt=stored)
            _collect_result_receipt_cache(
                root,
                index=index,
                max_entries=RESULT_CACHE_MAX_ENTRIES,
                max_bytes=RESULT_CACHE_MAX_BYTES,
                max_age_seconds=RESULT_CACHE_MAX_AGE_SECONDS,
                now=None,
                protected_keys={protected_key},
            )
            return stored
        finally:
            try:
                candidate.unlink()
            except FileNotFoundError:
                pass


def collect_result_receipt_cache(
    root: Path,
    *,
    max_entries: int = RESULT_CACHE_MAX_ENTRIES,
    max_bytes: int = RESULT_CACHE_MAX_BYTES,
    max_age_seconds: int = RESULT_CACHE_MAX_AGE_SECONDS,
    now: float | None = None,
) -> dict[str, int]:
    """Apply finite deterministic retention to the regenerable result cache."""

    limits = (max_entries, max_bytes, max_age_seconds)
    if any(type(value) is not int or value <= 0 for value in limits):
        raise RepoctlError(
            "result receipt retention limits must be positive finite integers",
            code="result_receipt_retention_invalid",
            path=RESULT_RECEIPT_ROOT.as_posix(),
        )
    with repoctl_lock(root):
        return _collect_result_receipt_cache(
            root,
            index=_reconciled_cache_index(root),
            max_entries=max_entries,
            max_bytes=max_bytes,
            max_age_seconds=max_age_seconds,
            now=now,
            protected_keys=set(),
        )


def _collect_result_receipt_cache(
    root: Path,
    *,
    index: dict[str, Any],
    max_entries: int,
    max_bytes: int,
    max_age_seconds: int,
    now: float | None,
    protected_keys: set[str],
) -> dict[str, int]:
    cache_root = root / RESULT_RECEIPT_ROOT
    if not cache_root.exists():
        return {"entries": 0, "bytes": 0, "removed": 0}
    _validate_result_receipt_path(root, cache_root)
    current_time = time.time() if now is None else float(now)
    entries: list[tuple[int, str, str, Path, int, float]] = []
    removed = 0
    indexed_entries = index["entries"]
    for key, item in list(indexed_entries.items()):
        path = cache_root / key
        _validate_result_receipt_path(root, path)
        try:
            stat_result = path.stat()
        except OSError:
            indexed_entries.pop(key, None)
            continue
        entries.append(
            (
                int(item["sequence"]),
                str(item["receipt_digest"]),
                key,
                path,
                stat_result.st_size,
                stat_result.st_mtime,
            )
        )
    retained: list[tuple[int, str, str, Path, int, float]] = []
    for entry in sorted(entries, key=lambda item: (item[0], item[1], item[2])):
        if entry[2] not in protected_keys and current_time - entry[5] > max_age_seconds:
            try:
                entry[3].unlink()
                removed += 1
            except FileNotFoundError:
                pass
            indexed_entries.pop(entry[2], None)
            continue
        retained.append(entry)
    total_bytes = sum(entry[4] for entry in retained)
    while retained and (len(retained) > max_entries or total_bytes > max_bytes):
        eviction_index = next(
            (index for index, entry in enumerate(retained) if entry[2] not in protected_keys),
            None,
        )
        if eviction_index is None:
            break
        _sequence, _digest, key, path, size, _mtime = retained.pop(eviction_index)
        try:
            path.unlink()
            removed += 1
        except FileNotFoundError:
            pass
        indexed_entries.pop(key, None)
        total_bytes -= size
    _write_cache_index(root, index)
    return {"entries": len(retained), "bytes": max(0, total_bytes), "removed": removed}


def _reconciled_cache_index(root: Path) -> dict[str, Any]:
    """Load the bounded insertion index and deterministically adopt orphan receipts."""

    cache_root = root / RESULT_RECEIPT_ROOT
    index = _read_cache_index(root)
    known: dict[str, dict[str, Any]] = index["entries"]
    valid_receipts: dict[str, dict[str, Any]] = {}
    if cache_root.exists():
        _validate_result_receipt_path(root, cache_root)
        for path in cache_root.glob("*/*/*.json"):
            _validate_result_receipt_path(root, path)
            try:
                receipt = read_result_receipt(root, path)
            except RepoctlError:
                try:
                    path.unlink()
                except FileNotFoundError:
                    pass
                continue
            valid_receipts[_cache_receipt_key(cache_root, path)] = receipt

    reconciled: dict[str, dict[str, Any]] = {}
    used_sequences: set[int] = set()
    for key, item in sorted(known.items()):
        receipt = valid_receipts.get(key)
        sequence = item.get("sequence")
        if (
            receipt is None
            or type(sequence) is not int
            or sequence < 1
            or sequence in used_sequences
            or item.get("receipt_digest") != receipt.get("receipt_digest")
        ):
            continue
        reconciled[key] = dict(item)
        used_sequences.add(sequence)

    next_sequence = max(int(index["next_sequence"]), max(used_sequences, default=0) + 1)
    orphans = [
        (str(receipt.get("receipt_digest") or ""), key)
        for key, receipt in valid_receipts.items()
        if key not in reconciled
    ]
    for receipt_digest, key in sorted(orphans):
        while next_sequence in used_sequences:
            next_sequence += 1
        reconciled[key] = {
            "sequence": next_sequence,
            "receipt_digest": receipt_digest,
        }
        used_sequences.add(next_sequence)
        next_sequence += 1
    return {
        "schema": RESULT_CACHE_INDEX_SCHEMA,
        "schema_version": RESULT_CACHE_INDEX_SCHEMA_VERSION,
        "next_sequence": next_sequence,
        "entries": reconciled,
    }


def _read_cache_index(root: Path) -> dict[str, Any]:
    path = root / RESULT_CACHE_INDEX
    empty = {
        "schema": RESULT_CACHE_INDEX_SCHEMA,
        "schema_version": RESULT_CACHE_INDEX_SCHEMA_VERSION,
        "next_sequence": 1,
        "entries": {},
    }
    _validate_result_receipt_path(root, path)
    if not path.exists():
        return empty
    if not path.is_file():
        raise RepoctlError(
            "result receipt cache index must be a regular file",
            code="result_receipt_invalid",
            path=RESULT_CACHE_INDEX.as_posix(),
        )
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError):
        return empty
    if (
        not isinstance(data, dict)
        or set(data) != {"schema", "schema_version", "next_sequence", "entries", "index_digest"}
        or data.get("schema") != RESULT_CACHE_INDEX_SCHEMA
        or data.get("schema_version") != RESULT_CACHE_INDEX_SCHEMA_VERSION
        or type(data.get("next_sequence")) is not int
        or data["next_sequence"] < 1
        or not isinstance(data.get("entries"), dict)
    ):
        return empty
    basis = {key: value for key, value in data.items() if key != "index_digest"}
    if data.get("index_digest") != digest_data(basis):
        return empty
    entries: dict[str, dict[str, Any]] = {}
    for key, item in data["entries"].items():
        if (
            not isinstance(key, str)
            or not isinstance(item, dict)
            or set(item) != {"sequence", "receipt_digest"}
            or type(item.get("sequence")) is not int
            or item["sequence"] < 1
            or not isinstance(item.get("receipt_digest"), str)
            or not _DIGEST_RE.fullmatch(item["receipt_digest"])
            or not _canonical_cache_key(key)
        ):
            return empty
        entries[key] = dict(item)
    return {**basis, "entries": entries}


def _index_receipt(root: Path, *, index: dict[str, Any], path: Path, receipt: dict[str, Any]) -> str:
    cache_root = root / RESULT_RECEIPT_ROOT
    key = _cache_receipt_key(cache_root, path)
    existing = index["entries"].get(key)
    if existing is not None and existing.get("receipt_digest") == receipt.get("receipt_digest"):
        return key
    sequence = int(index["next_sequence"])
    index["entries"][key] = {
        "sequence": sequence,
        "receipt_digest": str(receipt["receipt_digest"]),
    }
    index["next_sequence"] = sequence + 1
    return key


def _write_cache_index(root: Path, index: dict[str, Any]) -> None:
    path = root / RESULT_CACHE_INDEX
    _validate_result_receipt_path(root, path)
    basis = {
        "schema": RESULT_CACHE_INDEX_SCHEMA,
        "schema_version": RESULT_CACHE_INDEX_SCHEMA_VERSION,
        "next_sequence": int(index["next_sequence"]),
        "entries": {key: dict(value) for key, value in sorted(index["entries"].items())},
    }
    atomic_write(path, _canonical_json({**basis, "index_digest": digest_data(basis)}))


def _cache_receipt_key(cache_root: Path, path: Path) -> str:
    try:
        key = path.relative_to(cache_root).as_posix()
    except ValueError as exc:
        raise RepoctlError(
            "result receipt cache entry escapes its cache root",
            code="result_receipt_invalid",
            path=path.as_posix(),
        ) from exc
    if not _canonical_cache_key(key):
        raise RepoctlError(
            "result receipt cache entry has a non-canonical path",
            code="result_receipt_invalid",
            path=key,
        )
    return key


def _canonical_cache_key(value: str) -> bool:
    path = Path(value)
    return (
        value == path.as_posix()
        and not path.is_absolute()
        and len(path.parts) == 3
        and ".." not in path.parts
        and path.suffix == ".json"
        and all(part not in {"", "."} for part in path.parts)
    )


def verify_result_selections(
    root: Path,
    *,
    target: RepoTarget,
    producer: ResultProducer,
    result_id: str,
    selections: Iterable[ResultSelection],
) -> dict[str, Any]:
    _require_digest(result_id, field="result_id")
    requested = sorted(set(selections))
    path = result_receipt_path(root, target=target, producer=producer, result_id=result_id)
    _validate_result_receipt_path(root, path)
    if not path.is_file():
        raise RepoctlError(
            "selected result has no producer-owned receipt for this repository",
            code="result_receipt_missing",
            path=path.relative_to(root).as_posix(),
        )
    receipt = read_result_receipt(root, path)
    if receipt["repository"] != target.to_dict() or receipt["producer"] != producer.value or receipt["result_id"] != result_id:
        raise RepoctlError(
            "selected result receipt identity does not match the task repository and producer",
            code="result_receipt_identity_mismatch",
            path=path.relative_to(root).as_posix(),
        )
    available = {
        ResultSelection(ResultAuthority(item["authority"]), item["ref"])
        for item in receipt["selectable"]
    }
    missing = [selection for selection in requested if selection not in available]
    if missing:
        first = missing[0]
        raise RepoctlError(
            f"selected result reference is not part of the producer evidence manifest: {first.authority.value}:{first.ref}",
            code="result_selection_not_in_receipt",
            path=first.ref,
        )
    return receipt


def result_receipt_path(
    root: Path,
    *,
    target: RepoTarget,
    producer: ResultProducer,
    result_id: str,
) -> Path:
    _require_digest(result_id, field="result_id")
    return root / RESULT_RECEIPT_ROOT / target.id / producer.value / f"{result_id.removeprefix('sha256:')}.json"


def read_result_receipt(root: Path, path: Path) -> dict[str, Any]:
    rel = _validate_result_receipt_path(root, path)
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepoctlError("result receipt is unreadable or invalid JSON", code="result_receipt_invalid", path=rel) from exc
    if not isinstance(data, dict) or set(data) != {
        "schema",
        "schema_version",
        "producer",
        "repository",
        "result_id",
        "request",
        "selectable",
        "receipt_digest",
    }:
        raise RepoctlError("result receipt has an invalid object schema", code="result_receipt_invalid", path=path.as_posix())
    try:
        schema_version = decode_schema_version(
            data.get("schema_version"),
            supported=(RESULT_RECEIPT_SCHEMA_VERSION,),
        )
    except ValueError as exc:
        raise RepoctlError("result receipt has an unsupported schema", code="result_receipt_invalid", path=path.as_posix()) from exc
    if data.get("schema") != RESULT_RECEIPT_SCHEMA:
        raise RepoctlError("result receipt has an unsupported schema", code="result_receipt_invalid", path=path.as_posix())
    try:
        if not all(isinstance(data.get(key), str) for key in ("producer", "result_id", "receipt_digest")):
            raise ValueError("result receipt identity fields must be strings")
        producer = ResultProducer(data["producer"])
        _require_digest(data["result_id"], field="result_id")
        _require_digest(data["receipt_digest"], field="receipt_digest")
    except (ValueError, RepoctlError) as exc:
        raise RepoctlError("result receipt identity is invalid", code="result_receipt_invalid", path=path.as_posix()) from exc
    repository = data.get("repository")
    if (
        not isinstance(repository, dict)
        or set(repository) != {"id", "path", "identity_source"}
        or any(not isinstance(repository.get(key), str) or not repository.get(key) for key in repository)
    ):
        raise RepoctlError("result receipt repository identity is invalid", code="result_receipt_invalid", path=path.as_posix())
    try:
        request = _result_request_from_dict(producer, data.get("request"))
    except (TypeError, ValueError) as exc:
        raise RepoctlError("result receipt producer request is invalid", code="result_receipt_invalid", path=path.as_posix()) from exc
    selectable = data.get("selectable")
    if not isinstance(selectable, list):
        raise RepoctlError("result receipt selectable surface must be a list", code="result_receipt_invalid", path=path.as_posix())
    try:
        canonical_items = [
            item
            for item in selectable
            if (
                isinstance(item, dict)
                and set(item) == {"authority", "ref"}
                and isinstance(item.get("authority"), str)
                and isinstance(item.get("ref"), str)
            )
        ]
        selections = [
            ResultSelection(ResultAuthority(item["authority"]), item["ref"])
            for item in canonical_items
        ]
    except (KeyError, TypeError, ValueError) as exc:
        raise RepoctlError("result receipt selectable entry is invalid", code="result_receipt_invalid", path=path.as_posix()) from exc
    if len(selections) != len(selectable) or selections != sorted(set(selections)):
        raise RepoctlError("result receipt selectable surface is not canonical", code="result_receipt_invalid", path=path.as_posix())
    basis = {key: data[key] for key in data if key != "receipt_digest"}
    if data["receipt_digest"] != digest_data(basis):
        raise RepoctlError("result receipt digest does not match its content", code="result_receipt_invalid", path=path.as_posix())
    return {
        **data,
        "schema_version": schema_version,
        "producer": producer.value,
        "request": request.to_dict(),
        "selectable": [selection.to_dict() for selection in selections],
    }


def _validate_result_receipt_path(root: Path, path: Path) -> str:
    root_path = Path(root).absolute()
    path_value = Path(path).absolute()
    try:
        relative = path_value.relative_to(root_path)
    except ValueError as exc:
        raise RepoctlError(
            "result receipt path escapes the workspace",
            code="result_receipt_invalid",
            path=path_value.as_posix(),
        ) from exc
    rel = relative.as_posix()
    current = root_path
    for index, part in enumerate(relative.parts):
        current = current / part
        if current.is_symlink():
            raise RepoctlError(
                "result receipt path must not contain symbolic links",
                code="result_receipt_invalid",
                path=rel,
            )
        if index < len(relative.parts) - 1 and current.exists() and not current.is_dir():
            raise RepoctlError(
                "result receipt parent must be a directory",
                code="result_receipt_invalid",
                path=rel,
            )
    try:
        path_value.resolve(strict=False).relative_to(root_path.resolve())
    except (OSError, ValueError) as exc:
        raise RepoctlError(
            "result receipt path escapes the workspace",
            code="result_receipt_invalid",
            path=rel,
        ) from exc
    return rel


def result_receipt_episode(receipt: dict[str, Any]) -> ResultEpisode:
    producer = ResultProducer(str(receipt.get("producer") or ""))
    request = _result_request_from_dict(producer, receipt.get("request"))
    if isinstance(request, ContextResultRequest):
        identity = {"producer": producer.value, "query": request.query}
    else:
        identity = {"producer": producer.value, "request": request.to_dict()}
    return ResultEpisode(
        id=digest_data(identity),
        seed_query=request.seed_query,
    )


def parse_result_request(producer: ResultProducer, value: Any) -> ResultRequest:
    return _result_request_from_dict(producer, value)


def context_result_citations(bundle: dict[str, Any]) -> list[ResultSelection]:
    """Return citable Context evidence independently of compact visibility."""

    selections: set[ResultSelection] = set()
    groups = bundle.get("groups") if isinstance(bundle.get("groups"), dict) else {}
    for group, raw_items in groups.items():
        if group == "warnings_and_completeness" or not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            authority, ref = _context_item_selection(str(group), item)
            if authority is not None and ref:
                selections.add(ResultSelection(authority, ref))
    for continuation in _context_continuations(bundle, groups=groups):
        if not isinstance(continuation, dict):
            continue
        ref = _selector_ref(continuation.get("selector"))
        if ref:
            selections.add(ResultSelection(ResultAuthority.GRAPH, ref))
    selections.update(
        ResultSelection(ResultAuthority.GRAPH, ref)
        for ref in _relationship_candidate_refs(bundle.get("relationship_candidates"))
    )
    for seed in bundle.get("graph_seed_refs", []):
        if not isinstance(seed, dict):
            continue
        if isinstance(seed.get("path"), str) and seed["path"].strip():
            selections.add(ResultSelection(ResultAuthority.GRAPH, seed["path"].strip()))
        continuation = seed.get("continuation")
        if not isinstance(continuation, dict):
            continue
        selector_ref = _selector_ref(continuation.get("selector"))
        if selector_ref:
            selections.add(ResultSelection(ResultAuthority.GRAPH, selector_ref))
    return sorted(selections)


def context_result_receipt_projection(
    receipt: dict[str, Any],
    *,
    compact_bundle: dict[str, Any],
    full: bool = False,
) -> dict[str, Any]:
    """Project one immutable Context receipt without redefining its manifest."""

    manifest = [
        ResultSelection(ResultAuthority(item["authority"]), item["ref"])
        for item in receipt.get("selectable", [])
        if isinstance(item, dict)
    ]
    manifest_members = set(manifest)
    representative: list[dict[str, Any]] = []
    visible_members: set[ResultSelection] = set()
    visible_item_count = 0
    groups = (
        compact_bundle.get("groups")
        if isinstance(compact_bundle.get("groups"), dict)
        else {}
    )
    for group, raw_items in groups.items():
        if group == "warnings_and_completeness" or not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            visible_item_count += 1
            authority, ref = _context_item_selection(str(group), item)
            if authority is None or not ref:
                continue
            selection = ResultSelection(authority, ref)
            if selection not in manifest_members:
                raise RepoctlError(
                    "compact Context citation is not a member of the immutable result manifest",
                    code="result_receipt_projection_invalid",
                    path=selection.ref,
                )
            entry: dict[str, Any] = {
                "group": str(group),
                "primary_citation": selection.to_dict(),
            }
            reason = str(item.get("selection_reason") or "").strip()
            if reason:
                entry["selection_reason"] = reason
            representative.append(entry)
            visible_members.add(selection)

    manifest_counts = _selection_counts_by_authority(manifest_members)
    visible_counts = _selection_counts_by_authority(visible_members)
    omitted_by_authority = {
        authority.value: max(
            0,
            manifest_counts.get(authority.value, 0)
            - visible_counts.get(authority.value, 0),
        )
        for authority in ResultAuthority
        if manifest_counts.get(authority.value, 0)
        - visible_counts.get(authority.value, 0)
        > 0
    }
    manifest_projection: dict[str, Any] = {
        "selectable_count": len(manifest_members),
        "omitted_count": max(0, len(manifest_members) - len(visible_members)),
        "omitted_by_authority": omitted_by_authority,
        "full_available": True,
    }
    if full:
        manifest_projection["items"] = [selection.to_dict() for selection in manifest]
    return {
        "schema": RESULT_RECEIPT_PROJECTION_SCHEMA,
        "schema_version": RESULT_RECEIPT_PROJECTION_SCHEMA_VERSION,
        "view": "full" if full else "compact",
        "producer": receipt["producer"],
        "result_id": receipt["result_id"],
        "receipt_digest": receipt["receipt_digest"],
        "request": receipt["request"],
        "compact": {
            "representative_citations": representative,
            "visible_item_count": visible_item_count,
            "cited_item_count": len(representative),
            "manifest_member_count": len(visible_members),
        },
        "manifest": manifest_projection,
    }


def _selection_counts_by_authority(
    selections: Iterable[ResultSelection],
) -> dict[str, int]:
    counts: dict[str, int] = {}
    for selection in selections:
        key = selection.authority.value
        counts[key] = counts.get(key, 0) + 1
    return counts


def _context_continuations(
    bundle: dict[str, Any],
    *,
    groups: dict[str, Any],
) -> list[dict[str, Any]]:
    values = [
        continuation
        for continuation in bundle.get("continuations", [])
        if isinstance(continuation, dict)
    ]
    for raw_items in groups.values():
        if not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            values.extend(
                continuation
                for continuation in item.get("continuations", [])
                if isinstance(continuation, dict)
            )
    return values


def graph_result_selections(compact: dict[str, Any]) -> list[ResultSelection]:
    refs: set[str] = set()
    for key in ("matches", "candidates"):
        for item in compact.get(key, []):
            if isinstance(item, dict):
                refs.update(_graph_identity_refs(item))
    for key in ("paths", "relations"):
        for relation in compact.get(key, []):
            if not isinstance(relation, dict):
                continue
            for endpoint_key in ("from", "to"):
                endpoint = relation.get(endpoint_key)
                if isinstance(endpoint, dict):
                    refs.update(_graph_identity_refs(endpoint))
    for continuation in compact.get("continuations", []):
        if not isinstance(continuation, dict):
            continue
        selector_ref = _selector_ref(continuation.get("selector"))
        if selector_ref:
            refs.add(selector_ref)
    refs.update(_relationship_candidate_refs(compact.get("relationship_candidates")))
    return sorted(ResultSelection(ResultAuthority.GRAPH, ref) for ref in refs)


def _relationship_candidate_refs(value: Any) -> set[str]:
    if not isinstance(value, list):
        return set()
    refs: set[str] = set()
    for candidate in value:
        if not isinstance(candidate, dict):
            continue
        source = candidate.get("source") if isinstance(candidate.get("source"), dict) else {}
        if isinstance(source.get("path"), str) and source["path"].strip():
            refs.add(source["path"].strip())
        targets = candidate.get("targets") if isinstance(candidate.get("targets"), list) else []
        refs.update(
            target["path"].strip()
            for target in targets
            if isinstance(target, dict) and isinstance(target.get("path"), str) and target["path"].strip()
        )
    return refs


def _context_item_selection(group: str, item: dict[str, Any]) -> tuple[ResultAuthority | None, str]:
    if group == "reviewed_knowledge" and isinstance(item.get("record_id"), str):
        return ResultAuthority.KNOWLEDGE, item["record_id"].strip()
    ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
    path = str(ref.get("path") or "").strip()
    if not path:
        return None, ""
    if group == "related_history":
        return ResultAuthority.TASK_HISTORY, path
    kind = str(ref.get("kind") or "")
    if kind == "graph_relation":
        return ResultAuthority.GRAPH, path
    if kind in {"document", "product_manifest", "verification_hint"}:
        return ResultAuthority.DOCUMENT, path
    if kind in {"completion_receipt", "task_artifact"}:
        return ResultAuthority.TASK_HISTORY, path
    if kind in CONTEXT_SOURCE_KIND_VALUES:
        return ResultAuthority.SOURCE, path
    return None, ""


def _graph_identity_refs(value: dict[str, Any]) -> set[str]:
    return {
        str(value[key]).strip()
        for key in ("path", "task_id", "raw_import", "topic", "qualified_name", "name", "record_id")
        if isinstance(value.get(key), str) and str(value[key]).strip()
    }


def _selector_ref(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    kind = value.get("kind")
    selector_value = value.get("value")
    in_file = value.get("in_file")
    if not isinstance(kind, str) or not kind or not isinstance(selector_value, str) or not selector_value:
        return ""
    selector = {"kind": kind, "value": selector_value}
    if isinstance(in_file, str) and in_file:
        selector["in_file"] = in_file
    return json.dumps(selector, ensure_ascii=True, separators=(",", ":"), sort_keys=True)


def _result_request_from_dict(producer: ResultProducer, value: Any) -> ResultRequest:
    if not isinstance(value, dict):
        raise ValueError("result request must be an object")
    if producer == ResultProducer.CONTEXT:
        if set(value) != {"kind", "query", "mode"} or value.get("kind") != "context_query":
            raise ValueError("context result request has an invalid schema")
        if not isinstance(value.get("query"), str) or not isinstance(value.get("mode"), str):
            raise ValueError("context result request fields must be strings")
        return ContextResultRequest(query=value["query"], mode=value["mode"])
    if set(value) != {"kind", "selector"} or value.get("kind") != "graph_query":
        raise ValueError("graph result request has an invalid schema")
    return GraphResultRequest.from_query(value.get("selector"))


def _require_request_producer(producer: ResultProducer, request: ResultRequest) -> None:
    if producer == ResultProducer.CONTEXT and isinstance(request, ContextResultRequest):
        return
    if producer == ResultProducer.GRAPH and isinstance(request, GraphResultRequest):
        return
    raise RepoctlError(
        "result receipt request type does not match its producer",
        code="result_receipt_invalid_request",
    )


def _require_digest(value: str, *, field: str) -> None:
    if not _DIGEST_RE.fullmatch(value):
        raise RepoctlError(f"{field} must be a sha256 digest", code="result_receipt_invalid_identity", path=value)


def _canonical_json(value: dict[str, Any]) -> str:
    return json.dumps(value, ensure_ascii=True, indent=2, sort_keys=True) + "\n"

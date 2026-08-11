from __future__ import annotations

import json
import os
import re
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
from .io import RepoctlError, decode_schema_version, write_temporary_text
from .repositories import RepoTarget


RESULT_RECEIPT_SCHEMA = "repoctl.repository-understanding.result-receipt"
RESULT_RECEIPT_SCHEMA_VERSION = 2
RESULT_RECEIPT_ROOT = Path(".repoctl-state/result-receipts")
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
    _validate_result_receipt_path(root, path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _validate_result_receipt_path(root, path)
    candidate = write_temporary_text(path, _canonical_json(receipt), suffix=".candidate")
    try:
        try:
            os.link(candidate, path)
            return receipt
        except FileExistsError:
            existing = read_result_receipt(root, path)
            if existing != receipt:
                raise RepoctlError(
                    "result receipt identity already exists with different selectable content",
                    code="result_receipt_conflict",
                    path=path.relative_to(root).as_posix(),
                )
            return existing
    finally:
        try:
            candidate.unlink()
        except FileNotFoundError:
            pass


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
            f"selected result reference is not part of the producer compact surface: {first.authority.value}:{first.ref}",
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


def context_result_selections(compact: dict[str, Any]) -> list[ResultSelection]:
    selections: set[ResultSelection] = set()
    groups = compact.get("groups") if isinstance(compact.get("groups"), dict) else {}
    for group, raw_items in groups.items():
        if group == "warnings_and_completeness" or not isinstance(raw_items, list):
            continue
        for item in raw_items:
            if not isinstance(item, dict):
                continue
            authority, ref = _context_item_selection(str(group), item)
            if authority is not None and ref:
                selections.add(ResultSelection(authority, ref))
    for continuation in compact.get("continuations", []):
        if not isinstance(continuation, dict):
            continue
        ref = _selector_ref(continuation.get("selector"))
        if ref:
            selections.add(ResultSelection(ResultAuthority.GRAPH, ref))
    selections.update(
        ResultSelection(ResultAuthority.GRAPH, ref)
        for ref in _relationship_candidate_refs(compact.get("relationship_candidates"))
    )
    for seed in compact.get("graph_seed_refs", []):
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

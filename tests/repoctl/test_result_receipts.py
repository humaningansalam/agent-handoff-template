from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tools.repoctl.context_model import ContextResultMode
from tools.repoctl.graph_model import GRAPH_QUERY_SELECTOR_SCHEMAS, GraphQuerySelectorKind, digest_data
from tools.repoctl.io import RepoctlError
from tools.repoctl.repositories import RepoTarget
from tools.repoctl.result_receipts import (
    ContextResultRequest,
    GraphResultRequest,
    ResultAuthority,
    ResultProducer,
    ResultSelection,
    context_result_selections,
    read_result_receipt,
    result_receipt_path,
    verify_result_selections,
    write_result_receipt,
)


def test_result_receipt_is_idempotent_and_conflicting_membership_does_not_overwrite(tmp_path: Path) -> None:
    target = RepoTarget("main", tmp_path / "repos", "repos", "reserved")
    result_id = digest_data({"query": "owner"})
    selection = ResultSelection(ResultAuthority.SOURCE, "repos/src/owner.py")

    first = write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
        request=ContextResultRequest(query="owner", mode="auto"),
        selections=[selection],
    )
    path = result_receipt_path(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
    )
    original = path.read_bytes()

    assert write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
        request=ContextResultRequest(query="owner", mode="auto"),
        selections=[selection],
    ) == first
    assert path.read_bytes() == original

    with ThreadPoolExecutor(max_workers=8) as pool:
        concurrent = list(
            pool.map(
                lambda _index: write_result_receipt(
                    tmp_path,
                    target=target,
                    producer=ResultProducer.CONTEXT,
                    result_id=result_id,
                    request=ContextResultRequest(query="owner", mode="auto"),
                    selections=[selection],
                ),
                range(32),
            )
        )
    assert concurrent == [first] * 32
    assert path.read_bytes() == original

    with pytest.raises(RepoctlError) as exc_info:
        write_result_receipt(
            tmp_path,
            target=target,
            producer=ResultProducer.CONTEXT,
            result_id=result_id,
            request=ContextResultRequest(query="owner", mode="auto"),
            selections=[ResultSelection(ResultAuthority.SOURCE, "repos/src/other.py")],
        )

    assert exc_info.value.code == "result_receipt_conflict"
    assert path.read_bytes() == original


@pytest.mark.parametrize("mode", tuple(ContextResultMode))
def test_context_result_request_accepts_every_query_owner_mode(mode: ContextResultMode) -> None:
    request = ContextResultRequest(query="owner", mode=mode.value)

    assert request.to_dict()["mode"] == mode.value


def test_context_result_request_rejects_non_contract_mode() -> None:
    with pytest.raises(ValueError):
        ContextResultRequest(query="owner", mode="future_mode")


def test_context_result_selections_preserves_graph_seed_symbol_continuations() -> None:
    compact = {
        "groups": {},
        "continuations": [
            {
                "selector": {
                    "kind": "symbol",
                    "value": "visibleRelation",
                    "in_file": "src/visible.ts",
                }
            }
        ],
        "graph_seed_refs": [
            {
                "path": "src/owner.ts",
                "continuation": {
                    "selector": {
                        "kind": "symbol",
                        "value": "resolveOwner",
                        "in_file": "src/owner.ts",
                    }
                },
            },
            {
                "path": "src/policy.ts",
                "continuation": {
                    "selector": {
                        "kind": "symbol",
                        "value": "enforcePolicy",
                        "in_file": "src/policy.ts",
                    }
                },
            },
        ],
    }

    assert set(context_result_selections(compact)) == {
        ResultSelection(ResultAuthority.GRAPH, "src/owner.ts"),
        ResultSelection(ResultAuthority.GRAPH, "src/policy.ts"),
        ResultSelection(
            ResultAuthority.GRAPH,
            '{"in_file":"src/visible.ts","kind":"symbol","value":"visibleRelation"}',
        ),
        ResultSelection(
            ResultAuthority.GRAPH,
            '{"in_file":"src/owner.ts","kind":"symbol","value":"resolveOwner"}',
        ),
        ResultSelection(
            ResultAuthority.GRAPH,
            '{"in_file":"src/policy.ts","kind":"symbol","value":"enforcePolicy"}',
        ),
    }


def test_graph_result_request_accepts_every_query_owner_selector_schema() -> None:
    values = {
        "path": "src/owner.py",
        "topic": "billing",
        "raw_import": "billing.owner",
        "symbol": "resolve_owner",
        "task_id": "T-20260811010101Z",
    }

    assert set(GRAPH_QUERY_SELECTOR_SCHEMAS) == set(GraphQuerySelectorKind)
    for kind, schema in GRAPH_QUERY_SELECTOR_SCHEMAS.items():
        selector = {"type": kind.value, schema.value_field: values[schema.value_field]}
        if "depth" in schema.required_fields:
            selector["depth"] = 2
        if "in_file" in schema.optional_fields:
            selector["in_file"] = "src/owner.py"

        request = GraphResultRequest.from_query(selector)

        assert request.to_dict() == {"kind": "graph_query", "selector": selector}

    with pytest.raises(ValueError):
        GraphResultRequest.from_query({"type": "future_selector", "path": "src/owner.py"})


@pytest.mark.parametrize("invalid_field", ("schema_version", "request_query", "selection_ref"))
def test_result_receipt_rejects_non_string_contract_fields(tmp_path: Path, invalid_field: str) -> None:
    target = RepoTarget("main", tmp_path / "repos", "repos", "reserved")
    result_id = digest_data({"query": "owner"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
        request=ContextResultRequest(query="owner", mode="auto"),
        selections=[ResultSelection(ResultAuthority.SOURCE, "repos/src/owner.py")],
    )
    path = result_receipt_path(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
    )
    data = json.loads(path.read_text(encoding="utf-8"))
    if invalid_field == "schema_version":
        data["schema_version"] = 2.0
    elif invalid_field == "request_query":
        data["request"]["query"] = 456
    else:
        data["selectable"][0]["ref"] = 456
    basis = {key: value for key, value in data.items() if key != "receipt_digest"}
    data["receipt_digest"] = digest_data(basis)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    with pytest.raises(RepoctlError) as exc_info:
        read_result_receipt(tmp_path, path)

    assert exc_info.value.code == "result_receipt_invalid"


def test_result_receipt_rejects_parent_symlink_before_write_or_read(tmp_path: Path) -> None:
    target = RepoTarget("main", tmp_path / "repos", "repos", "reserved")
    result_id = digest_data({"query": "owner"})
    selection = ResultSelection(ResultAuthority.SOURCE, "repos/src/owner.py")
    outside = tmp_path.parent / f"{tmp_path.name}-receipt-outside"
    outside.mkdir()
    state_root = tmp_path / ".repoctl-state"
    state_root.mkdir()
    (state_root / "result-receipts").symlink_to(outside, target_is_directory=True)

    with pytest.raises(RepoctlError) as write_error:
        write_result_receipt(
            tmp_path,
            target=target,
            producer=ResultProducer.CONTEXT,
            result_id=result_id,
            request=ContextResultRequest(query="owner", mode="auto"),
            selections=[selection],
        )

    assert write_error.value.code == "result_receipt_invalid"
    assert list(outside.rglob("*")) == []

    with pytest.raises(RepoctlError) as read_error:
        verify_result_selections(
            tmp_path,
            target=target,
            producer=ResultProducer.CONTEXT,
            result_id=result_id,
            selections=[selection],
        )

    assert read_error.value.code == "result_receipt_invalid"

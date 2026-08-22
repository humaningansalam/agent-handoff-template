from __future__ import annotations

import json
import os
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tools.repoctl.graph_model import digest_data
from tools.repoctl.io import RepoctlError
from tools.repoctl.repositories import RepoTarget
from tools.repoctl.result_receipts import (
    ContextResultRequest,
    GraphResultRequest,
    RESULT_CACHE_MAX_BYTES,
    ResultAuthority,
    ResultProducer,
    ResultSelection,
    collect_result_receipt_cache,
    context_result_citations,
    context_result_receipt_projection,
    read_result_receipt,
    result_receipt_path,
    verify_result_selections,
    write_result_receipt,
)


def test_context_receipt_default_projection_size_is_independent_of_hidden_manifest_members() -> None:
    compact_bundle = {
        "groups": {
            "likely_change_surface": [
                {
                    "source_ref": {
                        "kind": "current_source",
                        "path": "repos/src/owner.py",
                    },
                    "selection_reason": "exact owner identity",
                }
            ],
            "warnings_and_completeness": [],
        }
    }

    def receipt(count: int) -> dict:
        selectable = [
            {"authority": "source", "ref": "repos/src/owner.py"},
            *(
                {"authority": "graph", "ref": f"src/hidden-{index}.py"}
                for index in range(count)
            ),
        ]
        return {
            "producer": "context",
            "result_id": digest_data({"count": count}),
            "receipt_digest": digest_data({"selectable": selectable}),
            "request": {
                "kind": "context_query",
                "query": "owner",
                "mode": "auto",
            },
            "selectable": selectable,
        }

    small = context_result_receipt_projection(
        receipt(2),
        compact_bundle=compact_bundle,
    )
    large_receipt = receipt(200)
    large = context_result_receipt_projection(
        large_receipt,
        compact_bundle=compact_bundle,
    )
    full = context_result_receipt_projection(
        large_receipt,
        compact_bundle=compact_bundle,
        full=True,
    )

    assert large["compact"]["representative_citations"] == [
        {
            "group": "likely_change_surface",
            "primary_citation": {
                "authority": "source",
                "ref": "repos/src/owner.py",
            },
            "selection_reason": "exact owner identity",
        }
    ]
    assert large["manifest"]["selectable_count"] == 201
    assert large["manifest"]["omitted_by_authority"] == {"graph": 200}
    assert "items" not in large["manifest"]
    assert full["manifest"]["items"] == large_receipt["selectable"]
    assert len(json.dumps(large, separators=(",", ":"))) - len(
        json.dumps(small, separators=(",", ":"))
    ) < 32
    assert len(json.dumps(full, separators=(",", ":"))) > 10 * len(
        json.dumps(large, separators=(",", ":"))
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


def test_context_result_request_rejects_non_contract_mode() -> None:
    with pytest.raises(ValueError):
        ContextResultRequest(query="owner", mode="future_mode")


def test_context_result_citations_preserves_graph_seed_symbol_continuations() -> None:
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

    assert set(context_result_citations(compact)) == {
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


def test_context_result_citations_include_evidence_omitted_from_compact_visibility() -> None:
    bundle = {
        "groups": {
            "likely_change_surface": [
                {
                    "source_ref": {
                        "kind": "current_source",
                        "path": "repos/src/visible.py",
                    },
                    "continuations": [
                        {
                            "selector": {"kind": "file", "value": "src/visible.py"},
                        }
                    ],
                },
                {
                    "source_ref": {
                        "kind": "current_source",
                        "path": "repos/src/compact-omitted.py",
                    },
                    "continuations": [
                        {
                            "selector": {
                                "kind": "file",
                                "value": "src/compact-omitted.py",
                            },
                        }
                    ],
                },
            ]
        },
        "graph_seed_refs": [],
        "relationship_candidates": [],
    }

    assert set(context_result_citations(bundle)) == {
        ResultSelection(ResultAuthority.SOURCE, "repos/src/visible.py"),
        ResultSelection(ResultAuthority.SOURCE, "repos/src/compact-omitted.py"),
        ResultSelection(
            ResultAuthority.GRAPH,
            '{"kind":"file","value":"src/visible.py"}',
        ),
        ResultSelection(
            ResultAuthority.GRAPH,
            '{"kind":"file","value":"src/compact-omitted.py"}',
        ),
    }

def test_graph_result_request_rejects_unknown_selector() -> None:
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


def _write_cached_context_result(
    root: Path,
    query: str,
) -> tuple[str, ContextResultRequest, list[ResultSelection], Path]:
    target = RepoTarget("main", root / "repos", "repos", "reserved")
    result_id = digest_data({"query": query})
    request = ContextResultRequest(query=query, mode="auto")
    selections = [ResultSelection(ResultAuthority.SOURCE, f"repos/src/{query}.py")]
    write_result_receipt(
        root,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
        request=request,
        selections=selections,
    )
    return (
        result_id,
        request,
        selections,
        result_receipt_path(
            root,
            target=target,
            producer=ResultProducer.CONTEXT,
            result_id=result_id,
        ),
    )


def test_result_receipt_cache_enforces_finite_count_bytes_and_age_limits(tmp_path: Path) -> None:
    now = 2_000_000_100

    count_root = tmp_path / "count"
    count_paths = [
        _write_cached_context_result(count_root, query)[3]
        for query in ("count-a", "count-b", "count-c")
    ]
    for path in count_paths:
        os.utime(path, (now, now))
    count_result = collect_result_receipt_cache(
        count_root,
        max_entries=2,
        max_bytes=10_000,
        max_age_seconds=10_000,
        now=now,
    )
    assert count_result["entries"] == 2
    assert count_result["removed"] == 1
    assert [path.exists() for path in count_paths] == [False, True, True]

    bytes_root = tmp_path / "bytes"
    bytes_paths = [
        _write_cached_context_result(bytes_root, query)[3]
        for query in ("bytes-a", "bytes-b")
    ]
    for path in bytes_paths:
        os.utime(path, (now, now))
    assert bytes_paths[0].stat().st_size == bytes_paths[1].stat().st_size
    byte_limit = bytes_paths[1].stat().st_size
    bytes_result = collect_result_receipt_cache(
        bytes_root,
        max_entries=10,
        max_bytes=byte_limit,
        max_age_seconds=10_000,
        now=now,
    )
    assert bytes_result == {"entries": 1, "bytes": byte_limit, "removed": 1}
    assert [path.exists() for path in bytes_paths] == [False, True]

    age_root = tmp_path / "age"
    age_paths = [
        _write_cached_context_result(age_root, query)[3]
        for query in ("age-old", "age-current")
    ]
    os.utime(age_paths[0], (now - 100, now - 100))
    os.utime(age_paths[1], (now - 5, now - 5))
    age_result = collect_result_receipt_cache(
        age_root,
        max_entries=10,
        max_bytes=10_000,
        max_age_seconds=10,
        now=now,
    )
    assert age_result["entries"] == 1
    assert age_result["removed"] == 1
    assert [path.exists() for path in age_paths] == [False, True]

    for limit in ("max_entries", "max_bytes", "max_age_seconds"):
        limits = {"max_entries": 1, "max_bytes": 1, "max_age_seconds": 1}
        limits[limit] = 0
        with pytest.raises(RepoctlError) as exc_info:
            collect_result_receipt_cache(tmp_path / f"invalid-{limit}", **limits)
        assert exc_info.value.code == "result_receipt_retention_invalid"

    oversized_root = tmp_path / "oversized"
    target = RepoTarget("main", oversized_root / "repos", "repos", "reserved")
    result_id = digest_data({"query": "oversized"})
    oversized = ResultSelection(
        ResultAuthority.SOURCE,
        "repos/" + ("x" * RESULT_CACHE_MAX_BYTES),
    )
    oversized_path = result_receipt_path(
        oversized_root,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
    )
    with pytest.raises(RepoctlError) as oversized_error:
        write_result_receipt(
            oversized_root,
            target=target,
            producer=ResultProducer.CONTEXT,
            result_id=result_id,
            request=ContextResultRequest(query="oversized", mode="auto"),
            selections=[oversized],
        )
    assert oversized_error.value.code == "result_receipt_too_large"
    assert not oversized_path.exists()
    assert list((oversized_root / ".repoctl-state/result-receipts").glob("*/*/*.json")) == []


def test_result_receipt_cache_uses_stable_insertion_sequence_not_mtime_query_or_reuse(
    tmp_path: Path,
) -> None:
    target = RepoTarget("main", tmp_path / "repos", "repos", "reserved")
    entries = [
        _write_cached_context_result(tmp_path, query)
        for query in ("zulu-900", "alpha-1", "middle-500")
    ]
    paths = [entry[3] for entry in entries]
    now = 2_000_000_100
    for path, timestamp in zip(paths, (now - 1, now - 90, now - 50), strict=True):
        os.utime(path, (timestamp, timestamp))

    collect_result_receipt_cache(
        tmp_path,
        max_entries=2,
        max_bytes=10_000,
        max_age_seconds=10_000,
        now=now,
    )
    assert [path.exists() for path in paths] == [False, True, True]

    reused_id, reused_request, reused_selections, reused_path = entries[1]
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=reused_id,
        request=reused_request,
        selections=reused_selections,
    )
    collect_result_receipt_cache(
        tmp_path,
        max_entries=1,
        max_bytes=10_000,
        max_age_seconds=10_000,
        now=now,
    )
    assert not reused_path.exists()
    assert paths[2].exists()


def test_result_receipt_cache_deterministically_recovers_missing_and_corrupt_indexes(
    tmp_path: Path,
) -> None:
    outcomes: list[tuple[str, set[str]]] = []
    for variant in ("missing", "corrupt"):
        root = tmp_path / variant
        entries = {
            query: _write_cached_context_result(root, query)[3]
            for query in ("zeta", "alpha", "middle")
        }
        receipt_digests = {
            query: read_result_receipt(root, path)["receipt_digest"]
            for query, path in entries.items()
        }
        index_path = root / ".repoctl-state/result-receipts/index.json"
        if variant == "missing":
            index_path.unlink()
        else:
            index_path.write_text('{"schema":"corrupt"}\n', encoding="utf-8")

        now = 2_000_000_100
        timestamps = (now - 1, now - 90, now - 50)
        if variant == "corrupt":
            timestamps = tuple(reversed(timestamps))
        for path, timestamp in zip(entries.values(), timestamps, strict=True):
            os.utime(path, (timestamp, timestamp))

        first = collect_result_receipt_cache(
            root,
            max_entries=2,
            max_bytes=10_000,
            max_age_seconds=10_000,
            now=now,
        )
        expected_evicted = min(receipt_digests, key=receipt_digests.__getitem__)
        retained = {query for query, path in entries.items() if path.exists()}
        assert first["entries"] == 2
        assert first["removed"] == 1
        assert retained == set(entries) - {expected_evicted}

        second = collect_result_receipt_cache(
            root,
            max_entries=2,
            max_bytes=10_000,
            max_age_seconds=10_000,
            now=now,
        )
        assert second["entries"] == 2
        assert second["removed"] == 0
        assert {query for query, path in entries.items() if path.exists()} == retained
        outcomes.append((expected_evicted, retained))

    assert outcomes[0] == outcomes[1]

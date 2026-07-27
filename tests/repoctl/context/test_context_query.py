from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

from tools.repoctl import context as context_module
from tools.repoctl.cli import main
from tools.repoctl.context import compact_context_bundle
from tools.repoctl.context_model import ContextBundle, ContextCandidate, ContextSourceRef
from tools.repoctl.graph_model import GraphSnapshot, digest_data
from tools.repoctl.graph_store import load_materialized_graph, materialize_graph
from tools.repoctl.path_roles import PathRole, classify_path_role
from tools.repoctl.repositories import require_repo_target
from tests.repoctl.knowledge_test_helpers import _approve_knowledge_source
from tests.repoctl.context_test_helpers import (
    _write_completion_receipt,
    _write_context_benchmark_collection_corpus,
    _setup_context_multirepo_workspace,
    _setup_context_workspace,
)


def _materialize(root: Path) -> None:
    snapshot, problems, _meta = materialize_graph(root, target=require_repo_target(root, repo_id="main"))
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]


def _write_reviewed_knowledge_record(
    root: Path,
    *,
    record_id: str,
    claim: str,
    applies_to_paths: list[str] | None = None,
    source_paths: list[str] | None = None,
    repo_id: str = "main",
    status: str = "reviewed",
) -> None:
    candidate_id = "KC" + record_id[1:]
    source_paths = source_paths or ["docs/contracts/repoctl-context-contract.md"]
    source_refs = []
    for path in source_paths:
        text = (root / path).read_text(encoding="utf-8")
        source_refs.append(
            {
                "kind": "current_source" if path.startswith("repos/") else "document",
                "path": path,
                "content_sha256": "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest(),
            }
        )
    record = {
        "schema": "repoctl.knowledge.record",
        "schema_version": 1,
        "id": record_id,
        "repo_id": repo_id,
        "kind": "decision",
        "status": status,
        "title": "Project-specific routing decision",
        "claim": claim,
        "summary": claim,
        "source_refs": source_refs,
        "applies_to": {"paths": applies_to_paths or []},
        "supersedes": [],
        "created_from": {
            "candidate_id": candidate_id,
            "candidate_digest": "sha256:" + "c" * 64,
            "candidate_check": {"passed": True, "warning_codes": [], "related_records": []},
        },
        "review": {"status": "reviewed", "reviewed_by": "fixture"},
        "authoritative": True,
    }
    record["record_digest"] = digest_data(record)
    path = root / "docs/knowledge/records" / f"{record_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    event = {
        "schema": "repoctl.knowledge.event",
        "schema_version": 1,
        "id": f"E{record_id[1:]}--approved",
        "type": "approved",
        "repo_id": repo_id,
        "record_id": record_id,
        "candidate_id": candidate_id,
        "record_digest": record["record_digest"],
        "supersedes": [],
    }
    event["event_digest"] = digest_data(event)
    event_path = root / "docs/knowledge/events" / f"{event['id']}.json"
    event_path.parent.mkdir(parents=True, exist_ok=True)
    event_path.write_text(json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _compact_evidence_item(kind: str, path: str, selector_kind: str, selector_value: str, actions: list[str], **extra: object) -> dict:
    return {
        "source_ref": {"kind": kind, "path": path, "content_sha256": f"sha256:{selector_value}"},
        "continuations": [
            {
                "selector": {"kind": selector_kind, "value": selector_value},
                "actions": actions,
            }
        ],
        **extra,
    }


def test_context_path_roles_are_repository_relative() -> None:
    assert classify_path_role("parser_test.mjs") == PathRole.TEST
    assert classify_path_role("parser_test.mts") == PathRole.TEST
    assert classify_path_role(".github/workflows/release.yml") == PathRole.WORKFLOW
    assert classify_path_role("repos/.github/workflows/release.yml", repository_path="repos") == PathRole.WORKFLOW
    assert classify_path_role("repos/web/docs/workflows/release.md", repository_path="repos/web") == PathRole.WORKFLOW
    assert classify_path_role("src/docs/workflows/release.md") == PathRole.SOURCE
    assert classify_path_role("repos/lib/.github/workflows/ci.yml", repository_path="repos") == PathRole.SOURCE


def test_compact_context_bounds_groups_and_omits_repository_wide_diagnostics() -> None:
    evidence = [
        ContextCandidate(
            source_ref=ContextSourceRef(
                kind="document",
                path=f"docs/contracts/source-{index}.md",
                section=f"Decision {index}",
                line_start=1,
                line_end=2,
                content_sha256=f"sha256:{index}",
            ),
            text=f"source {index}",
            score=100.0 - index,
            score_breakdown={"test": 1.0},
        )
        for index in range(10)
    ]
    bundle = ContextBundle(
        repository={"id": "main", "path": "repos", "identity_source": "reserved"},
        query={"text": "test"},
        source_snapshots={},
        completeness={
            "graph_available": True,
            "graph_completeness": {
                "status": "partial",
                "capabilities": {"imports": "complete", "calls": "partial"},
                "provider_coverage": {
                    "imports": {
                        "status": "complete",
                        "evidence_level": "conservative",
                        "analyzed_paths": [f"src/module-{index}.py" for index in range(500)],
                    }
                },
                "code_facts_complete": True,
                "receipt_set_complete": True,
            },
        },
        evidence=evidence,
        selection={"evidence_count": 10},
        groups={
            "must_read": [
                {
                    "source_ref": {
                        "kind": candidate.source_ref.kind,
                        "path": candidate.source_ref.path,
                        "content_sha256": candidate.source_ref.content_sha256,
                    },
                    "sections": [
                        {
                            "kind": candidate.source_ref.kind,
                            "section": candidate.source_ref.section,
                            "line_start": candidate.source_ref.line_start,
                            "line_end": candidate.source_ref.line_end,
                        }
                    ],
                    "evidence_role": "authority_document",
                    "excerpt": candidate.text,
                    "continuations": [
                        {
                            "selector": {"kind": "document", "value": candidate.source_ref.path},
                            "actions": ["workspace.open"],
                        }
                    ],
                }
                for candidate in evidence
            ]
        },
    ).with_digest()

    compact = compact_context_bundle(bundle, max_group_items=2)

    assert len(compact["groups"]["must_read"]) == 2
    assert [item["sections"][0]["section"] for item in compact["groups"]["must_read"]] == ["Decision 0", "Decision 1"]
    assert "selection" not in compact
    assert "provider_coverage" not in compact["completeness"]
    assert "selected_source_refs" not in compact
    assert "source_snapshots" not in compact
    assert "analyzed_paths" not in json.dumps(compact)


def test_compact_context_projects_items_with_their_primary_continuations() -> None:
    must_read = [
        _compact_evidence_item("document", f"docs/contract-{index}.md", "document", f"docs/contract-{index}.md", ["workspace.open"])
        for index in range(3)
    ]
    source_item = _compact_evidence_item("current_source", "repos/src/module.py", "file", "src/module.py", ["workspace.open", "graph.file"])
    test_item = _compact_evidence_item("current_source", "repos/tests/test_module.py", "file", "tests/test_module.py", ["workspace.open", "graph.file"])
    record = {
        "id": "K-1",
        "source_refs": [
            {"path": f"docs/sources/source-{index}.md"}
            for index in range(4)
        ],
    }
    knowledge_item = {
        "record_id": "K-1",
        "source_ref": {"kind": "knowledge_record", "path": "docs/knowledge/records/K-1.json", "content_sha256": "sha256:K-1"},
        "continuations": [
            {"selector": {"kind": "knowledge_record", "value": "K-1"}, "actions": ["knowledge.show"]},
            *[
                {"selector": {"kind": "document", "value": ref["path"]}, "actions": ["workspace.open"]}
                for ref in record["source_refs"]
            ],
        ],
    }
    invalid_knowledge_item = {
        "record_id": "K-invalid",
        "source_ref": {"kind": "knowledge_record", "path": "docs/knowledge/records/K-invalid.json", "content_sha256": "sha256:K-invalid"},
        "continuations": [
            {"selector": {"kind": "knowledge_record", "value": ""}, "actions": ["knowledge.show"]},
            {"selector": {"kind": "document", "value": "docs/sources/invalid.md"}, "actions": ["workspace.open"]},
        ],
    }
    groups = {
        "reviewed_knowledge": [invalid_knowledge_item, knowledge_item],
        "tests_and_verification": [test_item],
        "likely_change_surface": [source_item],
        "must_read": must_read,
    }
    bundle = ContextBundle(
        repository={"id": "main", "path": "repos", "identity_source": "reserved"},
        query={"text": "module behavior"},
        source_snapshots={},
        completeness={},
        evidence=[],
        selection={},
        groups=groups,
    ).with_digest()

    compact = compact_context_bundle(bundle)

    assert [item["record_id"] for item in compact["groups"]["reviewed_knowledge"]] == ["K-1"]
    continuations = {
        (item["selector"]["kind"], item["selector"]["value"]): item["actions"]
        for item in compact["continuations"]
    }
    assert continuations[("knowledge_record", "K-1")] == ["knowledge.show"]
    for path in ("src/module.py", "tests/test_module.py"):
        assert continuations[("file", path)] == ["workspace.open", "graph.file"]
    assert ("document", "docs/sources/source-0.md") in continuations
    assert ("document", "docs/sources/source-1.md") in continuations
    assert ("document", "docs/sources/source-2.md") in continuations
    assert ("document", "docs/sources/source-3.md") not in continuations
    assert ("document", "docs/sources/invalid.md") not in continuations


def test_compact_context_scans_until_group_limits_and_keeps_warnings() -> None:
    must_read = [
        _compact_evidence_item("document", f"docs/contract-{index}.md", "document", f"docs/contract-{index}.md", ["workspace.open"])
        for index in range(5)
    ]
    source_items = [
        _compact_evidence_item("current_source", f"repos/src/module-{index}.py", "file", f"src/module-{index}.py", ["workspace.open", "graph.file"])
        for index in range(3)
    ]
    relations = [
        _compact_evidence_item("graph_relation", "<new-symbol-0>", "symbol", "new_symbol_0", ["graph.symbol"]),
        _compact_evidence_item("graph_relation", "<new-symbol-1>", "symbol", "new_symbol_1", ["graph.symbol"]),
        _compact_evidence_item("graph_relation", "<shared-file-0>", "file", "src/module-0.py", ["graph.impact_file"]),
        _compact_evidence_item("graph_relation", "<shared-file-1>", "file", "src/module-1.py", ["graph.impact_file"]),
    ]
    groups = {
        "must_read": must_read,
        "likely_change_surface": source_items,
        "callers_and_dependents": relations,
        "reviewed_knowledge": [
            _compact_evidence_item("knowledge_record", "docs/knowledge/records/K-2.json", "knowledge_record", "K-2", ["knowledge.show"], record_id="K-2")
        ],
        "supporting_evidence": [{"source_ref": {"kind": "document", "path": "docs/malformed.md"}}],
        "warnings_and_completeness": [{"code": "graph_partial", "status": "warning"}],
    }
    bundle = ContextBundle(
        repository={"id": "main", "path": "repos", "identity_source": "reserved"},
        query={"text": "module impact"},
        source_snapshots={},
        completeness={},
        evidence=[],
        selection={},
        groups=groups,
    ).with_digest()

    compact = compact_context_bundle(bundle)

    assert compact == compact_context_bundle(bundle)
    assert [item["source_ref"]["path"] for item in compact["groups"]["callers_and_dependents"]] == ["<new-symbol-0>"]
    assert [item["record_id"] for item in compact["groups"]["reviewed_knowledge"]] == ["K-2"]
    assert compact["groups"]["supporting_evidence"] == []
    assert compact["groups"]["warnings_and_completeness"] == []
    assert sum(len(items) for group, items in compact["groups"].items() if group != "warnings_and_completeness") <= 8
    continuations = {
        (item["selector"]["kind"], item["selector"]["value"]): item["actions"]
        for item in compact["continuations"]
    }
    assert continuations[("file", "src/module-0.py")] == ["workspace.open", "graph.file"]
    assert continuations[("file", "src/module-1.py")] == ["workspace.open", "graph.file"]
    assert ("symbol", "new_symbol_0") in continuations


def test_context_query_returns_source_bundle(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    (tmp_path / "docs/contracts/repoctl-graph-contract.md").write_text(
        "# repoctl Graph contract\n\nGraph is non-authoritative derived evidence.\n\n## Source authority\n\nGraph remains non-authoritative; source files own the truth.\n",
        encoding="utf-8",
    )

    assert main(["context", "query", "Why is Graph non-authoritative?", "--mode", "authority", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    bundle = payload["data"]["bundle"]
    assert bundle["schema"] == "repoctl.context.bundle"
    assert bundle["authoritative"] is False
    assert bundle["repository"] == {"id": "main", "path": "repos", "identity_source": "reserved"}
    assert bundle["schema_version"] == 12
    assert bundle["view"] == "compact"
    grouped_items = [item for items in bundle["groups"].values() for item in items if isinstance(item.get("source_ref"), dict)]
    refs = [item["source_ref"] for item in grouped_items]
    graph_contracts = [item for item in grouped_items if item["source_ref"]["path"] == "docs/contracts/repoctl-graph-contract.md"]
    assert len(graph_contracts) == 1
    assert {section["section"] for section in graph_contracts[0]["sections"]} == {"repoctl Graph contract", "Source authority"}
    assert all(ref["content_sha256"].startswith("sha256:") for ref in refs)
    assert "graph" not in payload["data"]
    assert payload["warnings"][0]["code"] == "context_not_authoritative"


def test_context_query_ranks_provider_section_owner_over_repeated_body_noise(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "owner.py").write_text(
        "def reconcileSettlement():\n    return 'owner'\n",
        encoding="utf-8",
    )
    (repo / "noise.py").write_text(
        "def unrelated_helper():\n"
        "    return 'reconcile settlement reconcile settlement reconcile settlement'\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(["context", "query", "reconcile settlement", "--mode", "code-location", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    change_surface = bundle["groups"]["likely_change_surface"]
    assert [item["source_ref"]["path"] for item in change_surface[:2]] == ["repos/owner.py", "repos/noise.py"]
    owner = next(item for item in bundle["evidence"] if item["source_ref"]["path"] == "repos/owner.py")
    noise = next(item for item in bundle["evidence"] if item["source_ref"]["path"] == "repos/noise.py")
    assert owner["score_breakdown"]["section"] == 1.0
    assert noise["score_breakdown"]["section"] == 0.0
    assert owner["anchor_strength"] == "exact"
    assert noise["anchor_strength"] == "weak"


def test_context_query_preserves_exact_owner_beyond_body_noise_cutoff(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    noisy_functions = "\n\n".join(
        f"def noise_{index}():\n    return 'locate owner'"
        for index in range(180)
    )
    (repo / "a_big.py").write_text(noisy_functions + "\n", encoding="utf-8")
    (repo / "z_owner.py").write_text("def locate_owner():\n    return 'owner'\n", encoding="utf-8")
    _materialize(tmp_path)

    assert main(["context", "query", "locate owner", "--mode", "code-location", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    paths = [
        item["source_ref"]["path"]
        for item in bundle["evidence"]
        if item["source_ref"]["kind"] == "current_source"
    ]
    assert "repos/z_owner.py" in paths
    assert bundle["groups"]["likely_change_surface"][0]["source_ref"]["path"] == "repos/z_owner.py"


def test_context_query_does_not_promote_generic_natural_language_token_to_exact_symbol(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "types.py").write_text("class Evidence:\n    pass\n", encoding="utf-8")
    (repo / "activity.py").write_text(
        "def related_activity_detail():\n    return 'activity detail'\n",
        encoding="utf-8",
    )
    (repo / "render.py").write_text(
        "from activity import related_activity_detail\n\n"
        "def render_public_search_results():\n"
        "    return {'route': 'detail', 'activity': related_activity_detail()}\n",
        encoding="utf-8",
    )
    (repo / "test_render.py").write_text(
        "from render import render_public_search_results\n\n"
        "def test_public_search_detail_route():\n"
        "    assert render_public_search_results()['route'] == 'detail'\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    query = "public search results evidence related activity detail route dead end"
    assert main(["context", "query", query, "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    resolution = bundle["selection"]["graph_anchor"]
    assert resolution["status"] == "resolved"
    assert "render.py" in {item["anchor"]["path"] for item in resolution["anchors"]}
    assert all(item["anchor_provenance"] != "exact_identity" for item in resolution["anchors"])
    evidence_symbol = next(
        item
        for item in bundle["evidence"]
        if item["source_ref"]["path"] == "repos/types.py"
        and item["source_ref"].get("section") == "Evidence"
    )
    assert "exact_symbol" not in evidence_symbol["evidence_kinds"]
    assert any(
        relation.get("edge") == "TESTS_FILE"
        and {relation.get("from_path"), relation.get("to_path")} == {"render.py", "test_render.py"}
        for item in bundle["evidence"]
        for relation in item.get("graph_path", [])
    )

    assert main(["context", "query", query, "--repo-id", "main", "--json"]) == 0
    compact = json.loads(capsys.readouterr().out)["data"]["bundle"]
    compact_source_paths = {
        item["source_ref"]["path"]
        for item in compact["groups"]["likely_change_surface"]
    }
    assert "repos/render.py" in compact_source_paths
    assert "repos/types.py" not in compact_source_paths

    for punctuated_query in (
        "public search results evidence.",
        "public search results Evidence:",
    ):
        assert main(
            [
                "context",
                "query",
                punctuated_query,
                "--repo-id",
                "main",
                "--full",
                "--json",
            ]
        ) == 0
        punctuated = json.loads(capsys.readouterr().out)["data"]["bundle"]
        punctuated_resolution = punctuated["selection"]["graph_anchor"]
        assert "render.py" in {
            item["anchor"]["path"] for item in punctuated_resolution["anchors"]
        }
        assert all(
            item["anchor_provenance"] != "exact_identity"
            for item in punctuated_resolution["anchors"]
        )


def test_context_query_prefers_connected_test_over_weak_lexical_test_seed(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    billing = repo / "billing"
    billing.mkdir()
    tests = repo / "tests"
    tests.mkdir()
    (billing / "invoice_service.py").write_text(
        "def reconcile_invoice_failure():\n"
        "    return 'reconcile invoice failure handling'\n",
        encoding="utf-8",
    )
    (billing / "route.py").write_text(
        "from billing.invoice_service import reconcile_invoice_failure\n\n"
        "def render_error():\n"
        "    return reconcile_invoice_failure()\n",
        encoding="utf-8",
    )
    (tests / "test_contract.py").write_text(
        "from billing.invoice_service import reconcile_invoice_failure\n\n"
        "def test_contract_is_enforced():\n"
        "    assert reconcile_invoice_failure()\n",
        encoding="utf-8",
    )
    (repo / "unrelated.py").write_text(
        "def unrelated_dependency():\n"
        "    return True\n",
        encoding="utf-8",
    )
    (tests / "test_reconcile_invoice_failure_handling.py").write_text(
        "from unrelated import unrelated_dependency\n\n"
        "def test_unrelated_copy():\n"
        "    assert unrelated_dependency()\n"
        "    assert 'reconcile invoice failure handling'\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    query = "reconcile invoice failure handling"
    assert main(
        ["context", "query", query, "--repo-id", "main", "--full", "--json"]
    ) == 0
    full = json.loads(capsys.readouterr().out)["data"]["bundle"]
    seed_paths = {
        item["anchor"]["path"] for item in full["selection"]["graph_anchor"]["anchors"]
    }
    assert "billing/invoice_service.py" in seed_paths
    assert "tests/test_reconcile_invoice_failure_handling.py" in seed_paths
    assert "tests/test_contract.py" not in seed_paths
    assert "billing/route.py" not in seed_paths
    tests_by_path = {
        item["source_ref"]["path"]: item
        for item in full["groups"]["tests_and_verification"]
        if item["source_ref"]["kind"] == "current_source"
    }
    assert "anchor_connected_test" in tests_by_path["repos/tests/test_contract.py"][
        "evidence_roles"
    ]
    assert "anchor_connected_test" not in tests_by_path[
        "repos/tests/test_reconcile_invoice_failure_handling.py"
    ]["evidence_roles"]

    assert main(["context", "query", query, "--repo-id", "main", "--json"]) == 0

    compact = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert compact["groups"]["tests_and_verification"][0]["source_ref"]["path"] == (
        "repos/tests/test_contract.py"
    )
    assert any(
        item["source_ref"]["path"] == "repos/billing/route.py"
        for item in compact["groups"]["likely_change_surface"]
    )


def test_context_query_keeps_stronger_test_anchor_when_another_lane_matches_directory_scope(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    backend = repo / "backend/offline/listing"
    backend.mkdir(parents=True)
    web_source = repo / "web/src"
    web_source.mkdir(parents=True)
    web_tests = repo / "web/test"
    web_tests.mkdir(parents=True)
    app_tests = repo / "app/test"
    app_tests.mkdir(parents=True)

    (backend / "verified_sample_handler.py").write_text(
        "def render_verified_sample_error():\n"
        "    return 'buyer offline listing verified sample load error'\n",
        encoding="utf-8",
    )
    (web_source / "gate_policy.py").write_text(
        "def choose_verified_sample_availability():\n"
        "    marker = 'offline listing verified sample error'\n"
        "    return 'blocked'\n",
        encoding="utf-8",
    )
    (web_source / "order_panel.py").write_text(
        "from web.src.gate_policy import choose_verified_sample_availability\n\n"
        "def render_order_panel():\n"
        "    return choose_verified_sample_availability()\n",
        encoding="utf-8",
    )
    (web_tests / "test_render.py").write_text(
        "from web.src.gate_policy import choose_verified_sample_availability\n\n"
        "def test_render():\n"
        "    marker = 'buyer offline listing verified sample load error recovery'\n"
        "    assert choose_verified_sample_availability() == 'blocked'\n",
        encoding="utf-8",
    )
    (app_tests / "test_buyer_offline_listing.py").write_text(
        "def test_buyer_offline_listing():\n"
        "    assert 'buyer offline listing verified sample load error recovery'\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    query = "buyer offline listing verified sample load error recovery"
    assert main(
        ["context", "query", query, "--repo-id", "main", "--full", "--json"]
    ) == 0

    full = json.loads(capsys.readouterr().out)["data"]["bundle"]
    anchors = full["selection"]["graph_anchor"]["anchors"]
    anchor_paths = {item["anchor"]["path"] for item in anchors}
    assert "web/test/test_render.py" in anchor_paths
    assert anchors[0]["anchor"]["path"] == "web/test/test_render.py"
    assert anchors[1]["anchor"]["path"] == "web/src/gate_policy.py"
    assert any(
        relation.get("edge") == "TESTS_FILE"
        and relation.get("from_path") == "web/test/test_render.py"
        and relation.get("to_path") == "web/src/gate_policy.py"
        for item in full["evidence"]
        for relation in item.get("graph_path", [])
    )

    assert main(["context", "query", query, "--repo-id", "main", "--json"]) == 0
    compact = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert any(
        item["source_ref"]["path"] == "repos/web/src/gate_policy.py"
        for item in compact["groups"]["likely_change_surface"]
    )
    assert any(
        continuation["selector"] == {
            "kind": "file",
            "value": "web/src/gate_policy.py",
        }
        and "graph.impact_file" in continuation["actions"]
        for continuation in compact["continuations"]
    )


def test_context_query_fts_recall_is_path_diverse_before_candidate_limit(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    noisy_functions = "\n\n".join(
        f"def noise_{index}():\n    return 'shared retrieval phrase shared retrieval phrase'"
        for index in range(180)
    )
    (repo / "a_big.py").write_text(noisy_functions + "\n", encoding="utf-8")
    (repo / "z_secondary.py").write_text(
        "def unrelated():\n    return 'shared retrieval phrase'\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(["context", "query", "shared retrieval phrase", "--mode", "code-location", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    source_paths = {
        item["source_ref"]["path"]
        for item in bundle["evidence"]
        if item["source_ref"]["kind"] == "current_source"
    }
    assert {"repos/a_big.py", "repos/z_secondary.py"}.issubset(source_paths)


def test_context_query_ranks_index_and_dirty_overlay_in_one_candidate_corpus(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "z_owner.py").write_text(
        "def reconcile_settlement_ledger():\n    return 'owner'\n",
        encoding="utf-8",
    )
    (repo / "a_noise.py").write_text(
        "def unrelated_helper():\n    return 'reconcile settlement ledger'\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    (repo / "a_noise.py").write_text(
        "def unrelated_helper():\n    return 'reconcile settlement ledger current overlay'\n",
        encoding="utf-8",
    )

    assert main(["context", "query", "reconcile settlement ledger", "--mode", "code-location", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["groups"]["likely_change_surface"][0]["source_ref"]["path"] == "repos/z_owner.py"
    assert all(
        not (
            item["source_ref"]["path"] == "repos/a_noise.py"
            and item["source_ref"].get("section") == "unrelated_helper"
        )
        for item in bundle["evidence"]
    )


def test_context_query_does_not_inject_unmatched_project_files(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "README.md").write_text("# Product Architecture\n\nRuntime product architecture and current decisions live here.\n", encoding="utf-8")
    (repo / "package.json").write_text('{"name": "product-runtime", "scripts": {"test": "pytest"}}\n', encoding="utf-8")
    (repo / "config.json").write_text('{"private_config_token": "fallback-must-not-index-this"}\n', encoding="utf-8")

    assert main(["context", "query", "current project architecture and recent decisions", "--repo-id", "main", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    packed_paths = [
        item["source_ref"]["path"]
        for items in bundle["groups"].values()
        for item in items
        if isinstance(item.get("source_ref"), dict)
    ]
    assert "repos/README.md" in packed_paths
    assert "repos/package.json" not in packed_paths
    assert "evidence" not in bundle

    assert main(["context", "query", "fallback-must-not-index-this", "--repo-id", "main", "--json"]) == 0
    fallback_bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    fallback_paths = {
        item["source_ref"]["path"]
        for items in fallback_bundle["groups"].values()
        for item in items
        if isinstance(item.get("source_ref"), dict)
    }
    assert "repos/config.json" in fallback_paths
    config_item = next(
        item
        for items in fallback_bundle["groups"].values()
        for item in items
        if item.get("source_ref", {}).get("path") == "repos/config.json"
    )
    assert config_item["source_ref"]["kind"] == "config"


def test_context_query_exactly_matches_workflow_and_dotfile_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    workflow = repo / ".github/workflows/release.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text("name: release\non: push\n", encoding="utf-8")
    (repo / ".tool-versions").write_text("python 3.13.0\n", encoding="utf-8")
    (repo / "Dockerfile.dev").write_text("FROM python:3.13-slim\n", encoding="utf-8")
    (repo / "supabase").mkdir()
    (repo / "supabase/seed.sql").write_text("INSERT INTO public.jobs (id) VALUES (1);\n", encoding="utf-8")
    _materialize(tmp_path)

    for query, expected, kind in (
        (".github/workflows/release.yml", "repos/.github/workflows/release.yml", "config"),
        (".tool-versions", "repos/.tool-versions", "config"),
        ("Dockerfile.dev", "repos/Dockerfile.dev", "config"),
        ("supabase/seed.sql", "repos/supabase/seed.sql", "current_source"),
    ):
        assert main(["context", "query", query, "--repo-id", "main", "--json"]) == 0
        bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
        item = bundle["groups"]["likely_change_surface"][0]
        assert item["source_ref"] == {
            "kind": kind,
            "path": expected,
            "content_sha256": item["source_ref"]["content_sha256"],
        }
        assert item["evidence_role"] in {"change_candidate", "configuration"}
        assert "exact" in item["selection_reason"]


def test_context_query_rejects_unknown_mode(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["context", "query", "validate_token", "--mode", "autority", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"] == [
        {
            "severity": "error",
            "code": "invalid_context_mode",
            "message": "unsupported context mode: autority",
            "path": "autority",
        }
    ]


def test_context_query_read_first_populates_must_read(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "README.md").write_text("# Product\n\nRead this product overview first.\n", encoding="utf-8")
    (repo / "pyproject.toml").write_text("[project]\nname = \"read-first-product\"\n", encoding="utf-8")

    assert main(["context", "query", "이 프로젝트에서 다음 개발을 시작하려면 무엇을 먼저 읽어야 하나?", "--repo-id", "main", "--mode", "startup-reading", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["query"]["mode"] == "startup_reading"
    must_read_paths = [item["source_ref"]["path"] for item in bundle["groups"]["must_read"]]
    assert "repos/README.md" in must_read_paths
    assert "repos/pyproject.toml" in must_read_paths
    assert "docs/PRD.md" in must_read_paths
    assert "AGENTS.md" in must_read_paths
    reviewed = bundle["completeness"]["project_knowledge"]["reviewed_records"]
    assert reviewed == {
        "queried": False,
        "available_record_count": None,
        "result_count": None,
        "lifecycle": None,
    }


def test_context_query_auto_retrieves_root_project_documents_from_materialized_index(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _materialize(tmp_path)

    assert main(["context", "query", "Evidence And Context", "--repo-id", "main", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["completeness"]["project_knowledge"]["documents"]["result_count"] > 0
    assert any(
        item.get("source_ref", {}).get("path") == "docs/PRD.md"
        for items in bundle["groups"].values()
        for item in items
    )


def test_context_query_preserves_document_meaning_across_index_and_live_fallback(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    (tmp_path / "docs/PRD.md").unlink()
    (tmp_path / "docs/prd").mkdir()
    (tmp_path / "docs/prd/repository-understanding.md").write_text(
        "# Repository Understanding\n\nSafely update repository metadata through the applicable project authority.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/prd/metadata-policy.md").write_text(
        "# Metadata Policy\n\nRepository metadata should be updated safely under current product authority.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/README.md").write_text(
        "# Documentation Index\n\nReference links for repository metadata updates.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/workflows/INDEX.md").write_text(
        "# Workflow Index\n\nRepository metadata should be updated safely through the linked procedures.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/workflows/TEMPLATE.md").write_text(
        "# Workflow Template\n\nSafely update repository metadata with this reusable placeholder.\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/workflows/repo-metadata.md").write_text(
        "# Repository Metadata Procedure\n\nSafely update repository metadata by inspecting the target file before applying repoctl meta changes.\n",
        encoding="utf-8",
    )
    generated = tmp_path / "docs/knowledge/generated/repository-metadata.md"
    generated.parent.mkdir(parents=True)
    generated.write_text(
        "# Generated Repository Metadata\n\nSafely update repository metadata from this rendered view.\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    query = "How should repository metadata be updated safely?"

    def run_full_query() -> dict:
        assert main(["context", "query", query, "--repo-id", "main", "--full", "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    def document_projection(bundle: dict) -> dict[str, tuple[str, str, str]]:
        return {
            item["source_ref"]["path"]: (
                group,
                item.get("document_role", ""),
                item.get("evidence_role", ""),
            )
            for group, items in bundle["groups"].items()
            for item in items
            if item.get("source_ref", {}).get("kind") == "document"
        }

    indexed_payload = run_full_query()
    indexed = indexed_payload["data"]["bundle"]
    indexed_roles = {
        item["source_ref"]["path"]: item.get("document_role")
        for item in indexed["evidence"]
    }
    assert indexed_roles["docs/prd/repository-understanding.md"] == "product_authority"
    assert indexed_roles["docs/prd/metadata-policy.md"] == "product_authority"
    assert indexed_roles["docs/workflows/repo-metadata.md"] == "procedure"
    assert indexed_roles["docs/README.md"] == "reference"
    assert "docs/workflows/TEMPLATE.md" not in indexed_roles
    assert "docs/knowledge/generated/repository-metadata.md" not in indexed_roles
    indexed_projection = document_projection(indexed)
    assert indexed_projection["docs/prd/repository-understanding.md"] == (
        "must_read",
        "product_authority",
        "authority_document",
    )
    assert indexed_projection["docs/prd/metadata-policy.md"] == (
        "must_read",
        "product_authority",
        "authority_document",
    )
    assert indexed_projection["docs/workflows/repo-metadata.md"] == (
        "must_read",
        "procedure",
        "procedure_document",
    )
    assert indexed_projection["docs/README.md"] == (
        "supporting_evidence",
        "reference",
        "reference_document",
    )
    assert indexed_projection["docs/workflows/INDEX.md"] == (
        "supporting_evidence",
        "reference",
        "reference_document",
    )

    assert main(["context", "query", query, "--repo-id", "main", "--json"]) == 0
    compact = json.loads(capsys.readouterr().out)["data"]["bundle"]
    must_read = {
        item["source_ref"]["path"]: item.get("document_role")
        for item in compact["groups"]["must_read"]
    }
    assert len(must_read) == 2
    assert list(must_read.values()).count("product_authority") == 1
    assert must_read["docs/workflows/repo-metadata.md"] == "procedure"
    assert compact["groups"]["supporting_evidence"][0]["source_ref"]["path"] == "docs/workflows/INDEX.md"

    assert main(["context", "query", "docs/workflows/TEMPLATE.md", "--repo-id", "main", "--full", "--json"]) == 0
    exact = json.loads(capsys.readouterr().out)["data"]["bundle"]
    template = next(
        item
        for item in exact["evidence"]
        if item["source_ref"]["path"] == "docs/workflows/TEMPLATE.md"
    )
    assert template["document_role"] == "template"

    index_path = tmp_path / ".repoctl-state/graph/main/evidence.sqlite3"
    saved_index = index_path.with_suffix(".sqlite3.saved")
    index_path.rename(saved_index)
    try:
        fallback_payload = run_full_query()
    finally:
        saved_index.rename(index_path)
    fallback = fallback_payload["data"]["bundle"]
    fallback_roles = {
        item["source_ref"]["path"]: item.get("document_role")
        for item in fallback["evidence"]
    }
    for path in (
        "docs/prd/repository-understanding.md",
        "docs/prd/metadata-policy.md",
        "docs/workflows/repo-metadata.md",
        "docs/README.md",
    ):
        assert fallback_roles[path] == indexed_roles[path]
    assert document_projection(fallback) == indexed_projection
    assert "docs/workflows/TEMPLATE.md" not in fallback_roles
    assert "docs/knowledge/generated/repository-metadata.md" not in fallback_roles
    assert any(
        problem["code"] == "context_graph_unavailable"
        and problem.get("cause_code") == "evidence_index_missing"
        for problem in fallback_payload["problems"]
    )


def test_context_query_keeps_product_reference_documents_out_of_authority(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    token = "unique_product_reference_navigation_token"
    (repo / "README.md").write_text(
        f"# Product Reference\n\n{token}\n",
        encoding="utf-8",
    )
    (repo / "docs").mkdir()
    (repo / "docs/PRD.md").write_text(
        f"# Product Authority\n\n{token}\n",
        encoding="utf-8",
    )
    (repo / "docs/workflows").mkdir()
    (repo / "docs/workflows/repository-metadata.md").write_text(
        f"# Product Procedure\n\n{token}\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(["context", "query", token, "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    located = {
        item["source_ref"]["path"]: (group, item)
        for group, items in bundle["groups"].items()
        for item in items
        if item.get("source_ref", {}).get("path")
        in {
            "repos/README.md",
            "repos/docs/PRD.md",
            "repos/docs/workflows/repository-metadata.md",
        }
    }
    assert set(located) == {
        "repos/README.md",
        "repos/docs/PRD.md",
        "repos/docs/workflows/repository-metadata.md",
    }
    group, item = located["repos/README.md"]
    assert group == "supporting_evidence"
    assert item["document_role"] == "reference"
    assert item["evidence_role"] == "reference_document"
    group, item = located["repos/docs/PRD.md"]
    assert group == "must_read"
    assert item["document_role"] == "product_authority"
    assert item["evidence_role"] == "authority_document"
    group, item = located["repos/docs/workflows/repository-metadata.md"]
    assert group == "must_read"
    assert item["document_role"] == "procedure"
    assert item["evidence_role"] == "procedure_document"


def test_context_query_auto_balances_product_source_tests_and_project_documents(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    query = "marketplace listing detail result retention policy nullable public RPC Flutter Next"
    (repo / "owner.py").write_text(
        f"def resolve_listing_detail():\n    return {query!r}\n",
        encoding="utf-8",
    )
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_owner.py").write_text(
        f"def test_listing_detail_contract():\n    assert {query!r}\n",
        encoding="utf-8",
    )
    prd = tmp_path / "docs/PRD.md"
    prd.write_text(prd.read_text(encoding="utf-8") + f"\n## Listing contract\n\n{query}\n", encoding="utf-8")
    for index in range(32):
        (tmp_path / "docs/contracts" / f"onboarding-{index}.md").write_text(
            f"# Operational onboarding {index}\n\n{query}\n\n{query}\n",
            encoding="utf-8",
        )
    _materialize(tmp_path)

    assert main(["context", "query", query, "--repo-id", "main", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert any(item["source_ref"]["path"] == "repos/owner.py" for item in bundle["groups"]["likely_change_surface"])
    assert any(item["source_ref"]["path"] == "repos/tests/test_owner.py" for item in bundle["groups"]["tests_and_verification"])
    assert any(item["source_ref"]["path"] == "docs/PRD.md" for item in bundle["groups"]["must_read"])


def test_context_query_indexes_text_sources_without_claiming_semantic_graph_support(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "index.html").write_text(
        "<main aria-label=\"Noise Receiver first-use UX prompt canvas safety overlay privacy terms\"></main>\n",
        encoding="utf-8",
    )
    styles = repo / "css"
    styles.mkdir()
    (styles / "style.css").write_text(
        ".frequency-dial { cursor: grab; touch-action: none; } /* mobile zoom reduced motion overlay contrast */\n",
        encoding="utf-8",
    )
    scripts = repo / "js"
    scripts.mkdir()
    (scripts / "main.js").write_text(
        "export const uxCopy = 'Noise Receiver first-use UX prompt canvas dial cursor mobile zoom safety overlay privacy terms';\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    query = "Noise Receiver first-use UX prompt canvas dial cursor mobile zoom reduced motion safety overlay contrast privacy terms"
    assert main(["context", "query", query, "--repo-id", "main", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    paths = {item["source_ref"]["path"] for item in bundle["groups"]["likely_change_surface"]}
    assert {"repos/index.html", "repos/css/style.css"}.issubset(paths)


def test_context_query_returns_actionable_groups_for_call_impact(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "auth").mkdir()
    (repo / "auth/flow.py").write_text(
        'def validate_token(token: str) -> bool:\n    return token == "ok"\n\n\ndef login(token: str) -> str:\n    if validate_token(token):\n        return "ok"\n    return "denied"\n',
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(["context", "query", "validate_token", "--mode", "call-impact", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["query"]["mode"] == "call_impact"
    groups = bundle["groups"]
    assert any(item["source_ref"]["kind"] == "graph_relation" for item in groups["callers_and_dependents"])
    assert any("login --CALLS--> validate_token" in item["excerpt"] for item in groups["callers_and_dependents"])
    assert any(item["selector"] == {"kind": "file", "value": "auth/flow.py"} for item in bundle["continuations"])
    assert all("continuations" not in item for items in groups.values() for item in items)
    assert bundle["repository"]["id"] == "main"
    assert all("repo_id" not in item for items in groups.values() for item in items)

    assert main(["context", "query", "validate_token", "--mode", "call-impact"]) == 0
    text_output = capsys.readouterr().out
    assert "login --CALLS--> validate_token" in text_output
    assert "symbol=validate_token in_file=auth/flow.py actions=graph.symbol" in text_output
    assert "file=auth/flow.py actions=workspace.open,graph.file,graph.impact_file" in text_output


def test_context_query_preserves_ambiguous_exact_symbols_without_graph_expansion(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "alpha.py").write_text("def reconcile_settlement():\n    return 'alpha'\n", encoding="utf-8")
    (repo / "beta.py").write_text("def reconcile_settlement():\n    return 'beta'\n", encoding="utf-8")
    (repo / "alpha_caller.py").write_text(
        "from alpha import reconcile_settlement\n\ndef run_alpha():\n    return reconcile_settlement()\n",
        encoding="utf-8",
    )
    (repo / "beta_caller.py").write_text(
        "from beta import reconcile_settlement\n\ndef run_beta():\n    return reconcile_settlement()\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(["context", "query", "reconcile settlement", "--mode", "code-location", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    resolution = bundle["selection"]["graph_anchor"]
    assert resolution["status"] == "ambiguous"
    assert resolution["code"] == "context_graph_anchor_ambiguous"
    assert resolution["anchors"] == []
    assert [item["anchor"]["path"] for item in resolution["candidates"]] == ["alpha.py", "beta.py"]
    assert not any(item["source_ref"]["kind"] == "graph_relation" for item in bundle["evidence"])

    assert main(["context", "query", "reconcile settlement", "--mode", "code-location", "--repo-id", "main", "--json"]) == 0
    compact = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert compact["completeness"]["graph_anchor"] == {
        "status": "ambiguous",
        "code": "context_graph_anchor_ambiguous",
        "seed_anchors": [],
        "seed_paths": [],
        "candidate_paths": ["alpha.py", "beta.py"],
    }


def test_context_graph_modes_apply_bounded_directional_policies(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "dependency.py").write_text("def dependency():\n    return 'dependency'\n", encoding="utf-8")
    (repo / "other_dependency.py").write_text("def other_dependency():\n    return 'other'\n", encoding="utf-8")
    (repo / "owner.py").write_text(
        "from dependency import dependency\n"
        "from other_dependency import other_dependency\n\n"
        "def target_operation():\n    return dependency()\n\n"
        "def unrelated_operation():\n    return other_dependency()\n",
        encoding="utf-8",
    )
    (repo / "caller.py").write_text(
        "from owner import target_operation\n\ndef call_target():\n    return target_operation()\n",
        encoding="utf-8",
    )
    (repo / "outer.py").write_text(
        "from caller import call_target\n\ndef outer_call():\n    return call_target()\n",
        encoding="utf-8",
    )
    (repo / "test_owner.py").write_text(
        "from owner import target_operation\n\ndef test_target_operation():\n    assert target_operation() == 'dependency'\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    def query_relations(query: str, mode: str) -> set[tuple[str, str, str, int]]:
        assert main(["context", "query", query, "--mode", mode, "--repo-id", "main", "--full", "--json"]) == 0
        bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
        assert bundle["selection"]["graph_anchor"]["status"] == "resolved"
        return {
            (
                str(relation.get("from_path") or ""),
                str(relation.get("edge") or ""),
                str(relation.get("to_path") or ""),
                int(relation.get("distance") or 0),
            )
            for item in bundle["evidence"]
            for relation in item.get("graph_path", [])
            if item["source_ref"]["kind"] == "graph_relation" and isinstance(relation, dict)
        }

    code_location = query_relations("target operation", "code-location")
    assert ("owner.py", "CALLS", "dependency.py", 1) in code_location
    assert ("caller.py", "CALLS", "owner.py", 1) not in code_location
    assert ("owner.py", "CALLS", "other_dependency.py", 1) not in code_location
    assert ("test_owner.py", "TESTS_FILE", "owner.py", 1) in code_location
    assert not any(edge == "IMPORTS_FILE" for _from_path, edge, _to_path, _distance in code_location)

    call_impact = query_relations("target operation", "call-impact")
    assert ("owner.py", "CALLS", "dependency.py", 1) in call_impact
    assert ("caller.py", "CALLS", "owner.py", 1) in call_impact
    assert ("outer.py", "CALLS", "caller.py", 2) in call_impact
    assert ("owner.py", "CALLS", "other_dependency.py", 1) not in call_impact
    assert not any(edge == "IMPORTS_FILE" for _from_path, edge, _to_path, _distance in call_impact)

    automatic = query_relations("target operation", "auto")
    assert ("caller.py", "CALLS", "owner.py", 1) in automatic
    assert ("outer.py", "CALLS", "caller.py", 2) not in automatic
    assert not any(edge == "IMPORTS_FILE" for _from_path, edge, _to_path, _distance in automatic)

    file_impact = query_relations("owner.py", "file-impact")
    assert ("owner.py", "IMPORTS_FILE", "dependency.py", 1) in file_impact
    assert ("caller.py", "IMPORTS_FILE", "owner.py", 1) in file_impact
    assert ("outer.py", "IMPORTS_FILE", "caller.py", 2) in file_impact
    assert ("outer.py", "CALLS", "caller.py", 2) not in file_impact


def test_context_graph_preserves_all_lexical_seed_origins_for_shared_relation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "leaf.py").write_text("VALUE = 'leaf'\n", encoding="utf-8")
    (repo / "shared.py").write_text("from leaf import VALUE\n", encoding="utf-8")
    (repo / "settlement_router.py").write_text(
        "from shared import VALUE\n\n"
        "def route_payment():\n"
        "    marker = 'audit behavior'\n"
        "    return VALUE\n",
        encoding="utf-8",
    )
    (repo / "audit_router.py").write_text(
        "from shared import VALUE\n\n"
        "def record_event():\n"
        "    marker = 'settlement behavior'\n"
        "    return VALUE\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(
        [
            "context",
            "query",
            "settlement audit router behavior",
            "--mode",
            "file-impact",
            "--repo-id",
            "main",
            "--full",
            "--json",
        ]
    ) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    resolution = bundle["selection"]["graph_anchor"]
    assert resolution["status"] == "resolved"
    assert {item["anchor"]["path"] for item in resolution["anchors"]} == {
        "audit_router.py",
        "settlement_router.py",
    }
    assert {item["anchor_provenance"] for item in resolution["anchors"]} == {"lexical_file"}
    shared_relation = next(
        relation
        for item in bundle["evidence"]
        for relation in item.get("graph_path", [])
        if item["source_ref"]["kind"] == "graph_relation"
        and relation.get("from_path") == "shared.py"
        and relation.get("edge") == "IMPORTS_FILE"
        and relation.get("to_path") == "leaf.py"
    )
    assert shared_relation["origin_paths"] == ["audit_router.py", "settlement_router.py"]


def test_context_graph_propagates_each_origin_through_another_seed(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "leaf.py").write_text("VALUE = 'leaf'\n", encoding="utf-8")
    (repo / "audit_router.py").write_text(
        "from leaf import VALUE\n\n"
        "def record_event():\n"
        "    marker = 'settlement audit router behavior'\n"
        "    return VALUE\n",
        encoding="utf-8",
    )
    (repo / "settlement_router.py").write_text(
        "from audit_router import record_event\n\n"
        "def route_payment():\n"
        "    marker = 'settlement audit router behavior'\n"
        "    return record_event()\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(
        [
            "context",
            "query",
            "settlement audit router behavior",
            "--mode",
            "file-impact",
            "--repo-id",
            "main",
            "--full",
            "--json",
        ]
    ) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    relation = next(
        relation
        for item in bundle["evidence"]
        for relation in item.get("graph_path", [])
        if item["source_ref"]["kind"] == "graph_relation"
        and relation.get("from_path") == "audit_router.py"
        and relation.get("edge") == "IMPORTS_FILE"
        and relation.get("to_path") == "leaf.py"
    )
    assert relation["origin_paths"] == ["audit_router.py", "settlement_router.py"]
    assert relation["origin_distances"] == {
        "audit_router.py": 1,
        "settlement_router.py": 2,
    }


def test_context_graph_keeps_fresh_lexical_anchor_when_peer_is_stale(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "fresh_dependency.py").write_text("VALUE = 'fresh'\n", encoding="utf-8")
    (repo / "stale_dependency.py").write_text("VALUE = 'stale'\n", encoding="utf-8")
    (repo / "fresh_router.py").write_text(
        "from fresh_dependency import VALUE\n\n"
        "def route_fresh():\n"
        "    marker = 'settlement audit behavior'\n"
        "    return VALUE\n",
        encoding="utf-8",
    )
    (repo / "stale_router.py").write_text(
        "from stale_dependency import VALUE\n\n"
        "def route_stale():\n"
        "    marker = 'settlement audit behavior'\n"
        "    return VALUE\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)
    monkeypatch.setattr(context_module, "graph_stale_paths", lambda _freshness: {"stale_router.py"})

    assert main(
        [
            "context",
            "query",
            "settlement audit router behavior",
            "--mode",
            "file-impact",
            "--repo-id",
            "main",
            "--full",
            "--json",
        ]
    ) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    resolution = bundle["selection"]["graph_anchor"]
    assert resolution["status"] == "resolved"
    assert [item["anchor"]["path"] for item in resolution["anchors"]] == ["fresh_router.py"]
    assert {item["anchor"]["path"] for item in resolution["candidates"]} == {
        "fresh_router.py",
        "stale_router.py",
    }
    relations = {
        (relation.get("from_path"), relation.get("edge"), relation.get("to_path"))
        for item in bundle["evidence"]
        for relation in item.get("graph_path", [])
        if item["source_ref"]["kind"] == "graph_relation"
    }
    assert ("fresh_router.py", "IMPORTS_FILE", "fresh_dependency.py") in relations
    assert not any("stale_router.py" in {from_path, to_path} for from_path, _edge, to_path in relations)


def test_context_graph_prunes_fresh_descendant_behind_stale_bridge(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "workflow.py").write_text(
        "import middle\n\nVALUE = 'orchestration sentinel'\n",
        encoding="utf-8",
    )
    (repo / "middle.py").write_text(
        "import leaf\n\nVALUE = 'middle'\n",
        encoding="utf-8",
    )
    (repo / "leaf.py").write_text("VALUE = 'leaf'\n", encoding="utf-8")
    _materialize(tmp_path)
    (repo / "middle.py").write_text("VALUE = 'changed bridge'\n", encoding="utf-8")

    assert main(
        [
            "context",
            "query",
            "orchestration sentinel",
            "--mode",
            "file-impact",
            "--repo-id",
            "main",
            "--full",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    bundle = payload["data"]["bundle"]
    assert bundle["completeness"]["graph_freshness"]["stale_paths"] == ["middle.py"]
    change_surface = {
        item["source_ref"]["path"]
        for item in bundle["groups"]["likely_change_surface"]
    }
    assert "repos/workflow.py" in change_surface
    assert "repos/middle.py" not in change_surface
    assert "repos/leaf.py" not in change_surface
    assert not [
        relation
        for item in bundle["evidence"]
        for relation in item.get("graph_path", [])
    ]
    assert any(action.get("kind") == "graph_refresh" for action in payload["next_actions"])


def test_context_graph_expands_only_top_ranked_weak_lexical_file(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    for prefix in ("alpha", "beta", "gamma"):
        (repo / f"{prefix}_dependency.py").write_text(f"VALUE = '{prefix}'\n", encoding="utf-8")
        (repo / f"{prefix}.py").write_text(
            f"from {prefix}_dependency import VALUE\n\n"
            f"def run_{prefix}():\n"
            "    marker = 'cerulean handshake'\n"
            "    return VALUE\n",
            encoding="utf-8",
        )
    _materialize(tmp_path)

    assert main(
        [
            "context",
            "query",
            "cerulean handshake",
            "--mode",
            "file-impact",
            "--repo-id",
            "main",
            "--full",
            "--json",
        ]
    ) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    resolution = bundle["selection"]["graph_anchor"]
    assert resolution["status"] == "resolved"
    assert len(resolution["anchors"]) == 1
    selected_path = resolution["anchors"][0]["anchor"]["path"]
    assert selected_path == "alpha.py"
    assert resolution["anchors"][0]["anchor_provenance"] == "lexical_file"
    assert resolution["anchors"][0]["lexical_rank"] == 1
    relation_sources = {
        str(relation.get("from_path") or "")
        for item in bundle["evidence"]
        for relation in item.get("graph_path", [])
        if item["source_ref"]["kind"] == "graph_relation"
        and relation.get("edge") == "IMPORTS_FILE"
    }
    assert relation_sources == {selected_path}


def test_context_graph_uses_ranked_lexical_file_when_provider_symbol_is_partial(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "dependency.py").write_text("def dependency():\n    return 'dependency'\n", encoding="utf-8")
    (repo / "owner.py").write_text(
        "from dependency import dependency\n\n"
        "def reconcile_settlement_ledger():\n    marker = 'audit'\n    return dependency()\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(["context", "query", "reconcile audit", "--mode", "code-location", "--repo-id", "main", "--full", "--json"]) == 0
    partial = json.loads(capsys.readouterr().out)["data"]["bundle"]
    partial_resolution = partial["selection"]["graph_anchor"]
    assert partial_resolution["status"] == "resolved"
    assert partial_resolution["anchors"][0]["anchor"] == {"kind": "file", "path": "owner.py"}
    assert partial_resolution["anchors"][0]["anchor_provenance"] == "lexical_file"
    assert any(
        relation.get("from_path") == "owner.py"
        and relation.get("edge") == "CALLS"
        and relation.get("to_path") == "dependency.py"
        for item in partial["evidence"]
        for relation in item.get("graph_path", [])
        if item["source_ref"]["kind"] == "graph_relation"
    )

    assert main(["context", "query", "reconcile ledger", "--mode", "code-location", "--repo-id", "main", "--full", "--json"]) == 0
    complete = json.loads(capsys.readouterr().out)["data"]["bundle"]
    resolution = complete["selection"]["graph_anchor"]
    assert resolution["status"] == "resolved"
    assert resolution["anchors"][0]["anchor"]["kind"] == "symbol"
    assert resolution["anchors"][0]["anchor"]["symbol"] == "reconcile_settlement_ledger"
    assert resolution["anchors"][0]["anchor_provenance"] == "provider_symbol"
    assert any(
        relation.get("from_path") == "owner.py"
        and relation.get("edge") == "CALLS"
        and relation.get("to_path") == "dependency.py"
        for item in complete["evidence"]
        for relation in item.get("graph_path", [])
        if item["source_ref"]["kind"] == "graph_relation"
    )


def test_context_graph_keeps_symbol_specificity_with_compatible_exact_file_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "dependency.py").write_text("def dependency():\n    return 'dependency'\n", encoding="utf-8")
    (repo / "other_dependency.py").write_text("def other_dependency():\n    return 'other'\n", encoding="utf-8")
    (repo / "owner.py").write_text(
        "from dependency import dependency\n"
        "from other_dependency import other_dependency\n\n"
        "def target_operation():\n    return dependency()\n\n"
        "def unrelated_operation():\n    return other_dependency()\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(["context", "query", "owner.py target_operation", "--mode", "code-location", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    resolution = bundle["selection"]["graph_anchor"]
    assert resolution["status"] == "resolved"
    assert resolution["anchors"][0]["anchor"]["kind"] == "symbol"
    assert resolution["anchors"][0]["anchor"]["symbol"] == "target_operation"
    assert {"exact_filename", "exact_symbol"}.issubset(set(resolution["anchors"][0]["evidence_kinds"]))
    call_relations = {
        (str(relation.get("from_symbol", {}).get("name") or ""), str(relation.get("to_path") or ""))
        for item in bundle["evidence"]
        for relation in item.get("graph_path", [])
        if item["source_ref"]["kind"] == "graph_relation" and relation.get("edge") == "CALLS"
    }
    assert ("target_operation", "dependency.py") in call_relations
    assert ("unrelated_operation", "other_dependency.py") not in call_relations


def test_context_graph_marks_conflicting_exact_file_and_symbol_paths_ambiguous(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "alpha.py").write_text("def alpha_owner():\n    return 'alpha'\n", encoding="utf-8")
    (repo / "beta.py").write_text("def target_operation():\n    return 'beta'\n", encoding="utf-8")
    _materialize(tmp_path)

    assert main(["context", "query", "alpha.py target_operation", "--mode", "code-location", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    resolution = bundle["selection"]["graph_anchor"]
    assert resolution["status"] == "ambiguous"
    assert {item["anchor"]["path"] for item in resolution["candidates"]} == {"alpha.py", "beta.py"}
    assert {kind for item in resolution["candidates"] for kind in item["evidence_kinds"]} >= {"exact_filename", "exact_symbol"}
    assert not any(item["source_ref"]["kind"] == "graph_relation" for item in bundle["evidence"])


def test_context_graph_accounts_for_anchor_missing_from_snapshot(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "owner.py").write_text("def owner():\n    return 'owner'\n", encoding="utf-8")
    _materialize(tmp_path)
    target = require_repo_target(tmp_path, repo_id="main")
    snapshot, problems, meta = load_materialized_graph(tmp_path, target=target)
    assert snapshot is not None
    assert not problems
    missing_owner = GraphSnapshot(
        repository=snapshot.repository,
        sources=snapshot.sources,
        completeness=snapshot.completeness,
        nodes=[
            node
            for node in snapshot.nodes
            if not (node.kind == "file" and node.identity.get("path") == "owner.py")
        ],
        edges=snapshot.edges,
        schema=snapshot.schema,
        schema_version=snapshot.schema_version,
        authoritative=snapshot.authoritative,
        capabilities=snapshot.capabilities,
        snapshot_digest=snapshot.snapshot_digest,
    )
    monkeypatch.setattr(context_module, "load_materialized_graph", lambda *_args, **_kwargs: (missing_owner, [], meta))

    assert main(["context", "query", "owner.py", "--mode", "file-impact", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    resolution = bundle["selection"]["graph_anchor"]
    assert resolution["status"] == "unresolved"
    assert resolution["anchors"] == []
    assert [item["anchor"]["path"] for item in resolution["candidates"]] == ["owner.py"]
    assert not any(item["source_ref"]["kind"] == "graph_relation" for item in bundle["evidence"])


def test_context_compact_preserves_direct_anchor_and_expands_one_weak_owner(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "auth_callback.py").write_text(
        "def auth_callback():\n    return 'target'\n",
        encoding="utf-8",
    )
    (repo / "shared_auth.py").write_text(
        "def auth_helper():\n    return True\n",
        encoding="utf-8",
    )
    (repo / "board_flow.py").write_text(
        "from shared_auth import auth_helper\n\n"
        "def callback_board():\n    return auth_helper()\n",
        encoding="utf-8",
    )
    (repo / "entry.py").write_text(
        "from board_flow import callback_board\n\n"
        "def auth_entry():\n    return callback_board()\n",
        encoding="utf-8",
    )
    (repo / "handler.py").write_text(
        "def handle():\n    return 'oauth handshake'\n",
        encoding="utf-8",
    )
    (repo / "flow.py").write_text(
        "from handler import handle\n\n"
        "def run():\n    return handle()\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(["context", "query", "auth callback", "--repo-id", "main", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    change_surface = bundle["groups"]["likely_change_surface"]
    assert change_surface[0]["source_ref"]["path"] == "repos/auth_callback.py"
    assert change_surface[0]["evidence_role"] == "change_candidate"
    auth_anchor = bundle["completeness"]["graph_anchor"]
    assert auth_anchor["status"] == "resolved"
    assert auth_anchor["code"] == "context_graph_anchor_resolved"
    assert auth_anchor["seed_paths"] == ["auth_callback.py"]
    assert auth_anchor["candidate_paths"] == ["auth_callback.py"]
    assert auth_anchor["seed_anchors"][0]["path"] == "auth_callback.py"
    assert auth_anchor["seed_anchors"][0]["provenance"] == "exact_identity"
    assert not bundle["groups"]["callers_and_dependents"]
    assert any(
        continuation["selector"] == {"kind": "file", "value": "auth_callback.py"}
        for continuation in bundle["continuations"]
    )

    assert main(["context", "query", "oauth handshake", "--repo-id", "main", "--json"]) == 0

    related_bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    related_surface = related_bundle["groups"]["likely_change_surface"]
    related_target = related_surface[0]
    assert related_target["source_ref"]["path"] == "repos/handler.py"
    assert related_target["evidence_role"] == "change_candidate"
    assert any(item["source_ref"]["path"] == "repos/flow.py" for item in related_surface)
    related_anchor = related_bundle["completeness"]["graph_anchor"]
    assert related_anchor["status"] == "resolved"
    assert related_anchor["code"] == "context_graph_anchor_resolved"
    assert related_anchor["seed_paths"] == ["handler.py"]
    assert related_anchor["candidate_paths"] == ["handler.py"]
    assert related_anchor["seed_anchors"][0] == {
        "path": "handler.py",
        "provenance": "lexical_file",
        "anchor_strength": "weak",
        "kind": "file",
        "retrieval_lane": "product_source",
        "lexical_rank": 1,
    }


def test_context_query_indexes_semantic_source_without_precise_provider(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "worker.go").write_text(
        "package worker\n\nfunc refreshSettlementLedger() string { return \"ok\" }\n",
        encoding="utf-8",
    )
    (repo / "oversized.go").write_text("package oversized\n// " + "x" * (1024 * 1024), encoding="utf-8")
    _materialize(tmp_path)

    assert main(["context", "query", "refreshSettlementLedger", "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    bundle = payload["data"]["bundle"]
    assert any(item["source_ref"]["path"] == "repos/worker.go" for item in bundle["groups"]["likely_change_surface"])
    assert "evidence_problem_count" not in bundle["completeness"]
    assert any(problem["code"] == "context_current_source_too_large" for problem in payload["problems"])


def test_context_query_uses_materialized_index_with_dirty_path_overlay(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def run():\n    return 'old'\n", encoding="utf-8")
    _materialize(tmp_path)
    (repo / "app.py").write_text("def brand_new_overlay_token():\n    return 'new'\n", encoding="utf-8")
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- active task changed after Graph build\n", encoding="utf-8")
    original_collect_context_sources = context_module.collect_context_sources
    monkeypatch.setattr(
        "tools.repoctl.context.collect_context_sources",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("context performed a full source scan")),
    )

    assert main(["context", "query", "brand_new_overlay_token", "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    freshness = payload["data"]["bundle"]["completeness"]["graph_freshness"]
    assert freshness == {
        "status": "stale",
        "root_evidence_changed": True,
    }
    paths = {
        item["source_ref"]["path"]
        for item in payload["data"]["bundle"]["groups"]["likely_change_surface"]
    }
    assert "repos/app.py" in paths
    assert any(item["code"] == "context_graph_stale" for item in payload["data"]["bundle"]["groups"]["warnings_and_completeness"])
    stale_actions = {action.get("kind"): action for action in payload["next_actions"]}
    assert stale_actions["graph_refresh"]["command"] == "./scripts/repoctl graph build --repo-id main --json"
    assert stale_actions["context_resume"]["command"] == (
        "./scripts/repoctl context query brand_new_overlay_token --repo-id main --json"
    )

    assert main(["context", "query", "app.py", "--mode", "file-impact", "--repo-id", "main", "--json"]) == 0
    stale_anchor = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert stale_anchor["completeness"]["graph_anchor"] == {
        "status": "unresolved",
        "code": "context_graph_anchor_unresolved",
        "seed_anchors": [],
        "seed_paths": [],
        "candidate_paths": ["app.py"],
    }
    assert not stale_anchor["groups"]["callers_and_dependents"]

    assert main(["context", "query", "what should I read first", "--mode", "startup-reading", "--repo-id", "main", "--json"]) == 0
    startup = json.loads(capsys.readouterr().out)["data"]["bundle"]
    board = next(item for item in startup["groups"]["must_read"] if item["source_ref"]["path"] == "docs/BOARD.md")
    current_board = (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    assert board["source_ref"]["content_sha256"] == "sha256:" + hashlib.sha256(current_board.encode("utf-8")).hexdigest()
    assert "active task changed after Graph build" in board["excerpt"]
    monkeypatch.setattr("tools.repoctl.context.collect_context_sources", original_collect_context_sources)

    def assert_partial_fallback(payload: dict, dependency_code: str) -> None:
        assert payload["ok"] is True
        assert payload["data"]["bundle"] is not None
        assert payload["data"]["bundle"]["completeness"]["graph_available"] is False
        assert any(
            problem["code"] == "context_graph_unavailable" and problem.get("cause_code") == dependency_code
            for problem in payload["problems"]
        )
        assert any(
            item.get("source_ref", {}).get("path") == "repos/app.py"
            for items in payload["data"]["bundle"]["groups"].values()
            for item in items
        )
        actions = {action.get("kind"): action for action in payload["next_actions"]}
        assert actions["graph_rebuild"]["command"] == "./scripts/repoctl graph build --repo-id main --rebuild --json"
        assert actions["context_resume"]["command"] == (
            "./scripts/repoctl context query brand_new_overlay_token --repo-id main --json"
        )

    index_path = tmp_path / ".repoctl-state/graph/main/evidence.sqlite3"
    saved_index = index_path.with_suffix(".sqlite3.saved")
    index_path.rename(saved_index)
    assert main(["context", "query", "brand_new_overlay_token", "--repo-id", "main", "--json"]) == 0
    missing = json.loads(capsys.readouterr().out)
    assert_partial_fallback(missing, "evidence_index_missing")
    assert main(["graph", "build", "--repo-id", "main", "--json"]) == 1
    missing_build = json.loads(capsys.readouterr().out)
    assert [problem["code"] for problem in missing_build["problems"]] == ["evidence_index_missing"]
    saved_index.rename(index_path)

    for key, invalid_value in (
        ("schema_version", "broken"),
        ("schema", "foreign.evidence.index"),
    ):
        with sqlite3.connect(index_path) as connection:
            original_value = connection.execute("SELECT value FROM metadata WHERE key = ?", (key,)).fetchone()[0]
            connection.execute(
                "UPDATE metadata SET value = ? WHERE key = ?",
                (json.dumps(invalid_value), key),
            )
        assert main(["context", "query", "brand_new_overlay_token", "--repo-id", "main", "--json"]) == 0
        invalid_schema = json.loads(capsys.readouterr().out)
        assert_partial_fallback(invalid_schema, "evidence_index_schema_invalid")
        with sqlite3.connect(index_path) as connection:
            connection.execute("UPDATE metadata SET value = ? WHERE key = ?", (original_value, key))

    with sqlite3.connect(index_path) as connection:
        original_digest = connection.execute("SELECT value FROM metadata WHERE key = 'snapshot_digest'").fetchone()[0]
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'snapshot_digest'",
            (json.dumps("sha256:mismatched-snapshot"),),
        )
    assert main(["context", "query", "brand_new_overlay_token", "--repo-id", "main", "--json"]) == 0
    mismatched = json.loads(capsys.readouterr().out)
    assert_partial_fallback(mismatched, "evidence_index_snapshot_mismatch")
    with sqlite3.connect(index_path) as connection:
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'snapshot_digest'",
            (original_digest,),
        )

    with sqlite3.connect(index_path) as connection:
        original_input_digest = connection.execute("SELECT value FROM metadata WHERE key = 'graph_input_digest'").fetchone()[0]
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'graph_input_digest'",
            (json.dumps("sha256:mismatched-input"),),
        )
    assert main(["context", "query", "brand_new_overlay_token", "--repo-id", "main", "--json"]) == 0
    mismatched_input = json.loads(capsys.readouterr().out)
    assert_partial_fallback(mismatched_input, "evidence_index_input_mismatch")
    with sqlite3.connect(index_path) as connection:
        connection.execute(
            "UPDATE metadata SET value = ? WHERE key = 'graph_input_digest'",
            (original_input_digest,),
        )

    snapshot_path = tmp_path / ".repoctl-state/graph/main/snapshot.json"
    original_snapshot = snapshot_path.read_text(encoding="utf-8")
    snapshot_path.write_text("{not-json\n", encoding="utf-8")
    assert main(["graph", "build", "--repo-id", "main", "--json"]) == 1
    corrupt_build = json.loads(capsys.readouterr().out)
    assert [problem["code"] for problem in corrupt_build["problems"]] == ["graph_materialization_invalid"]
    assert corrupt_build["next_actions"] == missing_build["next_actions"]
    assert snapshot_path.read_text(encoding="utf-8") == "{not-json\n"
    assert main(["context", "query", "brand_new_overlay_token", "--repo-id", "main", "--json"]) == 0
    corrupt_snapshot = json.loads(capsys.readouterr().out)
    assert_partial_fallback(corrupt_snapshot, "graph_materialization_invalid")
    snapshot_path.write_text(original_snapshot, encoding="utf-8")


def test_context_query_missing_graph_returns_typed_build_and_resume_actions(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def initial_discovery_owner():\n    return True\n", encoding="utf-8")

    assert main(["context", "query", "initial discovery owner", "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    unavailable = next(problem for problem in payload["problems"] if problem["code"] == "context_graph_unavailable")
    assert unavailable["cause_code"] == "graph_snapshot_missing"
    actions = {action.get("kind"): action for action in payload["next_actions"]}
    assert actions["graph_build"]["command"] == "./scripts/repoctl graph build --repo-id main --json"
    assert actions["context_resume"]["command"] == (
        "./scripts/repoctl context query 'initial discovery owner' --repo-id main --json"
    )


def test_context_partial_fallback_keeps_source_history_and_reviewed_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    token = "partial_fallback_contract_token"
    (repo / "auth.py").write_text(
        f"def validate_token(token: str) -> bool:\n    # {token}\n    return bool(token)\n",
        encoding="utf-8",
    )
    _write_completion_receipt(tmp_path, changed_paths=["auth.py"])
    artifact = tmp_path / "docs/archive/tasks/T-20260625010101Z--knowledge-receipt.md"
    artifact.write_text(artifact.read_text(encoding="utf-8") + f"\n{token}\n", encoding="utf-8")
    receipt_path = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260625010101Z.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["content_sha256"] = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    contract = tmp_path / "docs/contracts/repoctl-context-contract.md"
    contract.write_text(contract.read_text(encoding="utf-8") + f"\n## Decision\n\n{token} remains reusable across tasks.\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--kind", "decision", "--claim", "Reviewed Context remains non-authoritative.", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    _materialize(tmp_path)
    snapshot_path = tmp_path / ".repoctl-state/graph/main/snapshot.json"
    snapshot_path.write_text("{broken\n", encoding="utf-8")

    assert main(["context", "query", token, "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    bundle = payload["data"]["bundle"]
    assert bundle["completeness"]["graph_available"] is False
    assert bundle["groups"]["likely_change_surface"][0]["source_ref"]["path"] == "repos/auth.py"
    assert bundle["groups"]["reviewed_knowledge"][0]["record_id"] == record_id
    assert bundle["groups"]["related_history"][0]["record_id"] == "T-20260625010101Z"
    assert any(problem["code"] == "context_graph_unavailable" for problem in payload["problems"])


def test_context_query_markdown_uses_same_grouped_sources(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "auth.py").write_text("def validate_token():\n    return True\n", encoding="utf-8")

    assert main(["context", "query", "Where is validate_token defined?", "--format", "markdown"]) == 0

    output = capsys.readouterr().out
    assert "# Context Bundle" in output
    assert "## Must Read" in output
    assert "## Likely Change Surface" in output
    assert "repos/auth.py" in output
    assert "validate_token" in output


def test_context_query_is_deterministic(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["context", "query", "source authorities", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert main(["context", "query", "source authorities", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)["data"]["bundle"]

    assert first["bundle_digest"] == second["bundle_digest"]
    assert first == second


def test_context_query_keeps_all_relevant_evidence_for_full_inspection(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    for index in range(12):
        (repo / f"search_surface_{index}.py").write_text(
            f"def shared_context_anchor_{index}():\n    return 'shared-context-anchor'\n" + "# implementation detail\n" * 100,
            encoding="utf-8",
        )

    assert main(["context", "query", "shared-context-anchor", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    matching_paths = {
        item["source_ref"]["path"]
        for item in bundle["evidence"]
        if item["source_ref"]["kind"] == "current_source" and "search_surface_" in item["source_ref"]["path"]
    }

    assert len(matching_paths) == 12
    assert bundle["selection"]["evidence_count"] == len(bundle["evidence"])

    assert main(["context", "query", "shared-context-anchor", "--json"]) == 0
    compact = json.loads(capsys.readouterr().out)["data"]["bundle"]
    full_item_count = sum(len(items) for items in bundle["groups"].values())
    compact_item_count = sum(len(items) for items in compact["groups"].values())
    compact_projection = bundle["selection"]["compact_projection"]
    assert compact_projection["items"] == {
        "total": full_item_count,
        "displayed": compact_item_count,
        "omitted": full_item_count - compact_item_count,
    }
    assert compact_projection["continuations"]["displayed"] == len(compact["continuations"])
    assert compact_projection["continuations"]["total"] >= compact_projection["continuations"]["displayed"]


def test_context_query_isolates_invalid_completion_receipts(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / "T-20260625010101Z.json").write_text(
        json.dumps({"broken": True}) + "\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(["context", "query", "What should I read first for this project?", "--repo-id", "main", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    warning_codes = {warning["code"] for warning in [*payload["warnings"], *payload["problems"]]}
    assert "context_graph_completion_receipt_invalid" in warning_codes
    bundle = payload["data"]["bundle"]
    assert bundle["completeness"]["receipt_problem_count"] == 1
    assert bundle["completeness"]["graph_completeness"]["receipt_set_complete"] is False


def test_default_context_query_keeps_related_completion_history_separate(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "auth.py").write_text("def validate_token(token: str) -> bool:\n    return bool(token)\n", encoding="utf-8")
    _write_completion_receipt(tmp_path)
    receipt_path = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260625010101Z.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["task_path_at_completion"] = "docs/tasks/T-20260625010101Z--knowledge-receipt.md"
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _materialize(tmp_path)

    assert main(["context", "query", "validate_token token validation", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    code_refs = [
        item.get("source_ref", {})
        for group, items in bundle["groups"].items()
        if group != "related_history"
        for item in items
        if isinstance(item, dict)
    ]
    assert any(ref.get("path") == "repos/auth.py" for ref in code_refs)
    assert all(ref.get("kind") not in {"completion_receipt", "task_artifact"} for ref in code_refs)
    history = bundle["groups"]["related_history"]
    assert history[0]["record_id"] == "T-20260625010101Z"
    assert history[0]["source_ref"]["kind"] == "task_artifact"
    assert history[0]["source_ref"]["path"] == "docs/archive/tasks/T-20260625010101Z--knowledge-receipt.md"
    assert any(
        continuation["selector"]
        == {"kind": "document", "value": "docs/archive/tasks/T-20260625010101Z--knowledge-receipt.md"}
        for continuation in history[0]["continuations"]
    )
    assert "auth.py" in history[0]["selection_reason"]


def test_context_query_reserves_task_history_when_other_lanes_are_saturated(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    token = "saturated_repository_history_signal"
    for index in range(12):
        (repo / f"owner_{index}.py").write_text(
            f"def owner_{index}():\n    return {token!r}\n",
            encoding="utf-8",
        )
    tests = repo / "tests"
    tests.mkdir()
    for index in range(3):
        (tests / f"test_owner_{index}.py").write_text(
            f"def test_owner_{index}():\n    assert {token!r}\n",
            encoding="utf-8",
        )
    (repo / "README.md").write_text(f"# Product\n\n{token}\n", encoding="utf-8")
    (repo / "docs").mkdir()
    (repo / "docs/architecture.md").write_text(f"# Architecture\n\n{token}\n", encoding="utf-8")
    for index in range(2):
        (tmp_path / "docs/contracts" / f"saturated-{index}.md").write_text(
            f"# Contract {index}\n\n{token}\n",
            encoding="utf-8",
        )
        (tmp_path / "docs/workflows" / f"saturated-{index}.md").write_text(
            f"# Procedure {index}\n\n{token}\n",
            encoding="utf-8",
        )
    (tmp_path / "docs/README.md").write_text(f"# Docs\n\n{token}\n", encoding="utf-8")
    (tmp_path / "docs/workflows/INDEX.md").write_text(f"# Workflow Index\n\n{token}\n", encoding="utf-8")
    prd = tmp_path / "docs/PRD.md"
    prd.write_text(prd.read_text(encoding="utf-8") + f"\n## Saturated Evidence\n\n{token}\n", encoding="utf-8")
    agents = tmp_path / "AGENTS.md"
    agents.write_text(agents.read_text(encoding="utf-8") + f"\n{token}\n", encoding="utf-8")
    _write_completion_receipt(tmp_path)
    artifact = tmp_path / "docs/archive/tasks/T-20260625010101Z--knowledge-receipt.md"
    artifact.write_text(artifact.read_text(encoding="utf-8") + f"\n{token}\n", encoding="utf-8")
    receipt_path = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260625010101Z.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["content_sha256"] = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _materialize(tmp_path)

    assert main(["context", "query", token, "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["selection"]["evidence_count"] == 24
    assert bundle["groups"]["related_history"]
    assert bundle["groups"]["related_history"][0]["record_id"] == "T-20260625010101Z"


def test_context_query_reserves_task_history_when_exact_source_matches_saturate_limit(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    for index in range(24):
        package = repo / f"package_{index}"
        package.mkdir()
        (package / "index.py").write_text(
            f"def package_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    _write_completion_receipt(tmp_path, changed_paths=["package_0/index.py"])
    _materialize(tmp_path)

    assert main(["context", "query", "index.py", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["selection"]["evidence_count"] == 24
    assert bundle["groups"]["related_history"]
    assert any(
        item["source_ref"]["kind"] in {"completion_receipt", "task_artifact"}
        for item in bundle["evidence"]
    )


def test_context_compact_reserves_authority_and_procedure_under_global_budget(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    token = "budget_pressure_anchor"
    (repo / "owner.py").write_text(
        f"def {token}():\n    return True\n",
        encoding="utf-8",
    )
    (repo / "caller.py").write_text(
        f"from owner import {token}\n\ndef call_owner():\n    return {token}()\n",
        encoding="utf-8",
    )
    for index in range(2):
        (repo / f"noise_{index}.py").write_text(
            f"def noise_{index}():\n    return {token!r}\n",
            encoding="utf-8",
        )
    tests = repo / "tests"
    tests.mkdir()
    (tests / "test_owner.py").write_text(
        f"from owner import {token}\n\ndef test_owner():\n    assert {token}()\n",
        encoding="utf-8",
    )
    prd = tmp_path / "docs/PRD.md"
    prd.write_text(prd.read_text(encoding="utf-8") + f"\n## Budget Authority\n\n{token}\n", encoding="utf-8")
    (tmp_path / "docs/workflows/budget-pressure.md").write_text(
        f"# Budget Pressure Procedure\n\n{token}\n",
        encoding="utf-8",
    )
    (tmp_path / "docs/README.md").write_text(f"# Reference\n\n{token}\n", encoding="utf-8")
    _write_completion_receipt(tmp_path)
    artifact = tmp_path / "docs/archive/tasks/T-20260625010101Z--knowledge-receipt.md"
    artifact.write_text(artifact.read_text(encoding="utf-8") + f"\n{token}\n", encoding="utf-8")
    receipt_path = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260625010101Z.json"
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    receipt["content_sha256"] = "sha256:" + hashlib.sha256(artifact.read_bytes()).hexdigest()
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    _write_reviewed_knowledge_record(
        tmp_path,
        record_id="K-20260722153000Z--budget-pressure",
        claim=f"{token} is the reusable repository routing decision.",
        applies_to_paths=["owner.py"],
    )
    _materialize(tmp_path)

    assert main(["context", "query", token, "--repo-id", "main", "--json"]) == 0

    groups = json.loads(capsys.readouterr().out)["data"]["bundle"]["groups"]
    displayed = sum(
        len(items)
        for group, items in groups.items()
        if group != "warnings_and_completeness"
    )
    assert displayed == 8
    for group in (
        "likely_change_surface",
        "tests_and_verification",
        "callers_and_dependents",
        "reviewed_knowledge",
        "related_history",
        "supporting_evidence",
    ):
        assert groups[group]
    must_read_roles = {item.get("document_role") for item in groups["must_read"]}
    assert "product_authority" in must_read_roles
    assert "procedure" in must_read_roles










def test_context_query_configured_multi_requires_repo_id(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_multirepo_workspace(tmp_path, monkeypatch)

    assert main(["context", "query", "Graph authority", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"


def test_context_multirepo_field_loop_keeps_context_and_knowledge_namespaced(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_multirepo_workspace(tmp_path, monkeypatch)
    fixture = Path("tests/fixtures/context-benchmark-multirepo").resolve()
    _write_context_benchmark_collection_corpus(tmp_path, fixture)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "web", "--claim", "Reviewed Context remains non-authoritative.", "--json"]) == 0
    web_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", web_candidate, "--repo-id", "web", "--json"]) == 0
    web_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "api", "--claim", "Reviewed Context remains non-authoritative.", "--json"]) == 0
    api_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", api_candidate, "--repo-id", "api", "--json"]) == 0
    api_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--require-fixture-corpus", "--require-no-cross-repo", "--require-no-forbidden", "--min-category-visible-recall", "multi-repo-isolation=1.0", "--json"]) == 0
    benchmark_payload = json.loads(capsys.readouterr().out)
    assert benchmark_payload["data"]["summary"]["cross_repo_ref_count"] == 0

    assert main(["knowledge", "query", "context returns source bundles", "--repo-id", "web", "--json"]) == 0
    web_query = json.loads(capsys.readouterr().out)
    assert web_query["data"]["results"][0]["record"]["id"] == web_record
    assert all(item["record"]["id"] != api_record for item in web_query["data"]["results"])









def test_context_query_bridges_reviewed_knowledge_path_to_code_relations_and_tests(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "dependency.py").write_text("def dependency():\n    return 'dependency'\n", encoding="utf-8")
    (repo / "service.py").write_text(
        "from dependency import dependency\n\ndef execute_route():\n    return dependency()\n",
        encoding="utf-8",
    )
    (repo / "caller.py").write_text(
        "from service import execute_route\n\ndef call_route():\n    return execute_route()\n",
        encoding="utf-8",
    )
    (repo / "test_service.py").write_text(
        "from service import execute_route\n\ndef test_execute_route():\n    assert execute_route() == 'dependency'\n",
        encoding="utf-8",
    )
    record_id = "K-20260719010101Z--blue-comet-routing"
    _write_reviewed_knowledge_record(
        tmp_path,
        record_id=record_id,
        claim="Blue comet routing owns settlement dispatch policy.",
        applies_to_paths=["service.py"],
    )
    _materialize(tmp_path)
    snapshot, graph_problems, _meta = load_materialized_graph(
        tmp_path,
        target=require_repo_target(tmp_path, repo_id="main"),
    )
    assert snapshot is not None
    assert not graph_problems
    nodes = {node.id: node for node in snapshot.nodes}
    assert any(
        edge.kind == "KNOWLEDGE_APPLIES_TO"
        and nodes[edge.from_id].identity.get("record_id") == record_id
        and nodes[edge.to_id].identity.get("path") == "service.py"
        for edge in snapshot.edges
    )

    assert main(["context", "query", "blue comet routing", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    knowledge = bundle["knowledge_results"][0]
    assert knowledge["record"]["id"] == record_id
    assert knowledge["resolved_code_paths"] == ["service.py"]
    assert knowledge["resolved_applicability_paths"] == ["service.py"]
    assert knowledge["code_path_resolutions"][0] == {
        "kind": "applies_to_path",
        "path": "service.py",
        "status": "resolved",
        "resolved_path": "service.py",
    }
    service = next(item for item in bundle["evidence"] if item["source_ref"]["path"] == "repos/service.py")
    assert "reviewed_knowledge_path" in service["evidence_kinds"]
    assert service["related_record_ids"] == [record_id]
    assert bundle["selection"]["graph_anchor"]["status"] == "resolved"
    assert bundle["selection"]["graph_anchor"]["anchors"][0]["anchor"] == {"kind": "file", "path": "service.py"}
    relations = {
        (str(relation.get("from_path") or ""), str(relation.get("edge") or ""), str(relation.get("to_path") or ""))
        for item in bundle["evidence"]
        for relation in item.get("graph_path", [])
        if item["source_ref"]["kind"] == "graph_relation"
    }
    assert ("service.py", "CALLS", "dependency.py") in relations
    assert ("caller.py", "CALLS", "service.py") in relations
    assert ("test_service.py", "TESTS_FILE", "service.py") in relations
    linked_item = next(item for item in bundle["groups"]["likely_change_surface"] if item["source_ref"]["path"] == "repos/service.py")
    assert linked_item["evidence_role"] == "knowledge_linked_source"


def test_context_query_bounds_reviewed_knowledge_graph_anchors(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    paths = [f"owner_{index}.py" for index in range(5)]
    for index, path in enumerate(paths):
        (repo / path).write_text(
            f"def owner_{index}():\n    return {index}\n",
            encoding="utf-8",
        )
    _write_reviewed_knowledge_record(
        tmp_path,
        record_id="K-20260719010109Z--amber-lattice-routing",
        claim="Amber lattice routing owns the bounded integration surfaces.",
        applies_to_paths=paths,
    )
    _materialize(tmp_path)

    assert main(
        [
            "context",
            "query",
            "amber lattice routing",
            "--repo-id",
            "main",
            "--full",
            "--json",
        ]
    ) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    resolution = bundle["selection"]["graph_anchor"]
    assert resolution["status"] == "resolved"
    assert len(resolution["anchors"]) == 3
    assert len(resolution["candidates"]) == 5
    assert {item["anchor_provenance"] for item in resolution["anchors"]} == {
        "reviewed_knowledge"
    }

    assert main(
        [
            "context",
            "query",
            "amber lattice routing",
            "--repo-id",
            "main",
            "--json",
        ]
    ) == 0
    compact = json.loads(capsys.readouterr().out)["data"]["bundle"]
    graph_anchor = compact["completeness"]["graph_anchor"]
    assert len(graph_anchor["seed_paths"]) == 3
    assert len(graph_anchor["seed_anchors"]) == 3
    assert all(
        item["provenance"] == "reviewed_knowledge"
        for item in graph_anchor["seed_anchors"]
    )


def test_context_query_uses_product_source_ref_but_not_root_provenance_as_code_anchor(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "service.py").write_text("def execute_route():\n    return 'ok'\n", encoding="utf-8")
    source_record = "K-20260719010102Z--source-linked-routing"
    _write_reviewed_knowledge_record(
        tmp_path,
        record_id=source_record,
        claim="Copper orbit source-link policy.",
        source_paths=["repos/service.py"],
    )
    provenance_record = "K-20260719010103Z--document-provenance"
    _write_reviewed_knowledge_record(
        tmp_path,
        record_id=provenance_record,
        claim="Ivory orbit document-only policy.",
    )
    _materialize(tmp_path)
    snapshot, graph_problems, _meta = load_materialized_graph(
        tmp_path,
        target=require_repo_target(tmp_path, repo_id="main"),
    )
    assert snapshot is not None
    assert not graph_problems
    nodes = {node.id: node for node in snapshot.nodes}
    assert any(
        edge.kind == "KNOWLEDGE_APPLIES_TO"
        and nodes[edge.from_id].identity.get("record_id") == source_record
        and nodes[edge.to_id].identity.get("path") == "service.py"
        for edge in snapshot.edges
    )
    assert not any(
        edge.kind == "KNOWLEDGE_APPLIES_TO"
        and nodes[edge.from_id].identity.get("record_id") == provenance_record
        for edge in snapshot.edges
    )

    assert main(["context", "query", "copper orbit source-link", "--repo-id", "main", "--full", "--json"]) == 0
    source_bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    source_result = source_bundle["knowledge_results"][0]
    assert source_result["resolved_code_paths"] == ["service.py"]
    assert source_result["code_path_resolutions"][0]["kind"] == "source_ref"
    assert source_result["record"]["explicit_path_refs"][0]["role"] == "code_anchor"
    assert any(item["source_ref"]["path"] == "repos/service.py" for item in source_bundle["evidence"])

    assert main(["context", "query", "ivory orbit document-only", "--repo-id", "main", "--full", "--json"]) == 0
    provenance_bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    provenance_result = provenance_bundle["knowledge_results"][0]
    assert provenance_result["resolved_code_paths"] == []
    assert provenance_result["code_path_resolutions"][0]["status"] == "provenance_only"
    assert provenance_result["record"]["explicit_path_refs"][0]["role"] == "provenance_only"
    assert not any(
        item["source_ref"].get("path") == "repos/service.py"
        and "reviewed_knowledge_path" in item.get("evidence_kinds", [])
        for item in provenance_bundle["evidence"]
    )

    (repo / "service.py").write_text("def execute_route():\n    return 'changed'\n", encoding="utf-8")
    _materialize(tmp_path)
    stale_snapshot, graph_problems, _meta = load_materialized_graph(
        tmp_path,
        target=require_repo_target(tmp_path, repo_id="main"),
    )
    assert stale_snapshot is not None
    assert not graph_problems
    stale_nodes = {node.id: node for node in stale_snapshot.nodes}
    stale_node_id = next(
        node.id
        for node in stale_snapshot.nodes
        if node.kind == "knowledge" and node.identity.get("record_id") == source_record
    )
    assert stale_nodes[stale_node_id].facts["record"]["status"] == "stale"
    assert any(edge.kind == "KNOWLEDGE_SOURCED_FROM" and edge.from_id == stale_node_id for edge in stale_snapshot.edges)
    assert not any(edge.kind == "KNOWLEDGE_APPLIES_TO" and edge.from_id == stale_node_id for edge in stale_snapshot.edges)


def test_context_query_keeps_weak_knowledge_match_visible_without_code_anchor(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "service.py").write_text("def execute_route():\n    return 'ok'\n", encoding="utf-8")
    _write_reviewed_knowledge_record(
        tmp_path,
        record_id="K-20260719010110Z--weak-routing-match",
        claim="Blue comet routing owns settlement dispatch policy.",
        applies_to_paths=["service.py"],
    )
    _materialize(tmp_path)

    assert main(["context", "query", "blue comet absent", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    result = bundle["knowledge_results"][0]
    assert result["query_match_strength"] == "weak"
    assert result["code_anchor_status"] == "ineligible_query_match"
    assert result["resolved_code_paths"] == []
    assert result["resolved_applicability_paths"] == ["service.py"]
    assert result["applicability_path_resolutions"] == [
        {
            "kind": "applies_to_path",
            "path": "service.py",
            "status": "resolved",
            "resolved_path": "service.py",
        }
    ]
    assert not any("reviewed_knowledge_path" in item.get("evidence_kinds", []) for item in bundle["evidence"])
    assert bundle["selection"]["graph_anchor"]["status"] == "unresolved"
    assert bundle["groups"]["likely_change_surface"] == []
    knowledge_continuations = bundle["groups"]["reviewed_knowledge"][0]["continuations"]
    assert {
        "selector": {"kind": "file", "value": "service.py"},
        "actions": ["workspace.open", "graph.file"],
    } in knowledge_continuations

    assert main(["context", "query", "blue comet absent", "--repo-id", "main", "--json"]) == 0
    compact = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert compact["groups"]["likely_change_surface"] == []
    assert {
        "selector": {"kind": "file", "value": "service.py"},
        "actions": ["workspace.open", "graph.file"],
    } in compact["continuations"]

    (repo / "service.py").unlink()
    assert main(["context", "query", "blue comet absent", "--repo-id", "main", "--json"]) == 0
    stale_compact = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert stale_compact["completeness"]["graph_freshness"]["status"] == "stale"
    assert not any(
        continuation["selector"] == {"kind": "file", "value": "service.py"}
        for continuation in stale_compact["continuations"]
    )

    assert main(
        [
            "context",
            "query",
            "Blue comet routing owns settlement dispatch policy.",
            "--repo-id",
            "main",
            "--full",
            "--json",
        ]
    ) == 0
    missing_payload = json.loads(capsys.readouterr().out)
    missing_result = missing_payload["data"]["bundle"]["knowledge_results"][0]
    assert missing_result["query_match_strength"] == "strong"
    assert missing_result["resolved_code_paths"] == []
    assert missing_result["resolved_applicability_paths"] == []
    assert missing_result["code_path_resolutions"][0]["status"] == "not_found"
    assert missing_result["applicability_path_resolutions"][0]["status"] == "not_found"
    assert any(problem["code"] == "context_knowledge_path_unresolved" for problem in missing_payload["problems"])


def test_context_query_fails_closed_on_ambiguous_knowledge_path_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "service.py").write_text("def root_service():\n    return 'root'\n", encoding="utf-8")
    nested = repo / "repos/service.py"
    nested.parent.mkdir(parents=True)
    nested.write_text("def nested_service():\n    return 'nested'\n", encoding="utf-8")
    _write_reviewed_knowledge_record(
        tmp_path,
        record_id="K-20260719010104Z--ambiguous-service-path",
        claim="Amber lattice routing policy.",
        applies_to_paths=["repos/service.py"],
    )
    target = require_repo_target(tmp_path, repo_id="main")
    snapshot, graph_problems, _meta = materialize_graph(tmp_path, target=target)
    assert snapshot is not None
    assert any(problem.code == "graph_knowledge_path_ambiguous" for problem in graph_problems)
    assert not any(edge.kind == "KNOWLEDGE_APPLIES_TO" for edge in snapshot.edges)

    assert main(["context", "query", "amber lattice routing", "--repo-id", "main", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    bundle = payload["data"]["bundle"]
    resolution = bundle["knowledge_results"][0]["code_path_resolutions"][0]
    assert resolution == {
        "kind": "applies_to_path",
        "path": "repos/service.py",
        "status": "ambiguous",
        "candidates": ["repos/service.py", "service.py"],
    }
    assert bundle["knowledge_results"][0]["resolved_code_paths"] == []
    assert bundle["knowledge_results"][0]["resolved_applicability_paths"] == []
    assert bundle["knowledge_results"][0]["applicability_path_resolutions"][0]["status"] == "ambiguous"
    assert not any("reviewed_knowledge_path" in item.get("evidence_kinds", []) for item in bundle["evidence"])
    assert any(problem["code"] == "context_knowledge_path_ambiguous" for problem in payload["problems"])


def test_context_query_does_not_cross_repository_for_knowledge_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_multirepo_workspace(tmp_path, monkeypatch)
    (tmp_path / "repos/web/service.py").write_text("def web_service():\n    return 'web'\n", encoding="utf-8")
    (tmp_path / "repos/api/service.py").write_text("def api_service():\n    return 'api'\n", encoding="utf-8")
    _write_reviewed_knowledge_record(
        tmp_path,
        record_id="K-20260719010105Z--cross-repo-path",
        claim="Silver lattice web policy.",
        applies_to_paths=["repos/api/service.py"],
        repo_id="web",
    )
    target = require_repo_target(tmp_path, repo_id="web")
    snapshot, problems, _meta = materialize_graph(tmp_path, target=target)
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]

    assert main(["context", "query", "silver lattice web", "--repo-id", "web", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["knowledge_results"][0]["resolved_code_paths"] == []
    assert bundle["knowledge_results"][0]["resolved_applicability_paths"] == []
    assert not any(item["source_ref"]["path"] == "repos/api/service.py" for item in bundle["evidence"])


@pytest.mark.parametrize(
    ("status", "record_id"),
    [
        ("stale", "K-20260719010107Z--stale-routing"),
        ("superseded", "K-20260719010108Z--superseded-routing"),
        ("deprecated", "K-20260719010109Z--deprecated-routing"),
    ],
)
def test_context_query_excludes_noncurrent_knowledge_paths_from_code_anchors(
    tmp_path: Path,
    monkeypatch,
    capsys,
    status: str,
    record_id: str,
) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "service.py").write_text("def execute_route():\n    return 'ok'\n", encoding="utf-8")
    _write_reviewed_knowledge_record(
        tmp_path,
        record_id=record_id,
        claim=f"{status} prism routing policy.",
        applies_to_paths=["service.py"],
        status=status,
    )
    _materialize(tmp_path)

    assert main(["context", "query", f"{status} prism routing", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["knowledge_results"] == []
    assert not any("reviewed_knowledge_path" in item.get("evidence_kinds", []) for item in bundle["evidence"])


def test_context_query_prefers_direct_exact_code_anchor_over_knowledge_path(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "owner.py").write_text("def target_operation():\n    return 'owner'\n", encoding="utf-8")
    (repo / "other.py").write_text("def other_operation():\n    return 'other'\n", encoding="utf-8")
    _write_reviewed_knowledge_record(
        tmp_path,
        record_id="K-20260719010106Z--target-operation",
        claim="target_operation remains a reviewed routing term.",
        applies_to_paths=["other.py"],
    )
    _materialize(tmp_path)

    assert main(["context", "query", "target_operation", "--repo-id", "main", "--full", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    anchor = bundle["selection"]["graph_anchor"]["anchors"][0]
    assert anchor["anchor"]["kind"] == "symbol"
    assert anchor["anchor"]["path"] == "owner.py"
    assert bundle["groups"]["likely_change_surface"][0]["source_ref"]["path"] == "repos/owner.py"


def test_context_query_includes_reviewed_knowledge_separately(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    record_id = _approve_knowledge_source(capsys, build_args=["--kind", "decision"])["data"]["record"]["id"]

    assert main(["context", "query", "reviewed knowledge source authority", "--repo-id", "main", "--mode", "authority", "--explain", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    bundle = payload["data"]["bundle"]
    assert bundle["knowledge_results"][0]["record"]["id"] == record_id
    assert bundle["knowledge_results"][0]["record"]["status"] == "reviewed"
    assert bundle["knowledge_results"][0]["explain"]["source_ref_statuses"][0]["digest_matches"] is True
    assert bundle["query"]["explain"] is True
    reviewed = bundle["completeness"]["project_knowledge"]["reviewed_records"]
    assert reviewed["queried"] is True
    assert reviewed["result_count"] == 1
    assert reviewed["lifecycle"]["available_statuses"] == {"reviewed": 1}
    assert reviewed["lifecycle"]["returned_statuses"] == {"reviewed": 1}
    assert all(item["source_ref"]["kind"] != "knowledge_record" for item in bundle["evidence"])

    source = tmp_path / "docs/contracts/repoctl-context-contract.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")

    assert main(["context", "query", "reviewed knowledge source authority", "--repo-id", "main", "--mode", "authority", "--full", "--json"]) == 0
    stale_payload = json.loads(capsys.readouterr().out)
    stale_bundle = stale_payload["data"]["bundle"]
    assert stale_bundle["knowledge_results"] == []
    stale_reviewed = stale_bundle["completeness"]["project_knowledge"]["reviewed_records"]
    assert stale_reviewed["available_record_count"] == 1
    assert stale_reviewed["lifecycle"]["available_statuses"] == {"stale": 1}
    assert stale_reviewed["lifecycle"]["excluded_statuses"] == {"stale": 1}
    assert stale_reviewed["lifecycle"]["returned_statuses"] == {}
    assert any(problem["code"] == "knowledge_stale_record_excluded" for problem in stale_payload["problems"])




def test_knowledge_render_check_reports_broken_links(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--kind", "decision", "--claim", "Reviewed Context remains non-authoritative.", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    render_payload = json.loads(capsys.readouterr().out)

    index = tmp_path / render_payload["data"]["output"] / "INDEX.md"
    index.write_text(index.read_text(encoding="utf-8") + "\n[Broken](missing.md)\n", encoding="utf-8")

    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "knowledge_render_broken_link" for problem in check_payload["problems"])

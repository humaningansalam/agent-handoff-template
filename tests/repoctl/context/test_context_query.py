from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path

from tools.repoctl import context as context_module
from tools.repoctl.cli import main
from tools.repoctl.context import _candidate_continuations, _knowledge_continuations, compact_context_bundle
from tools.repoctl.context_chunks import chunk_text_source
from tools.repoctl.context_model import ContextBundle, ContextCandidate, ContextSourceRef
from tools.repoctl.context_retrieval import _identity_score, retrieve_context
from tools.repoctl.graph_store import materialize_graph
from tools.repoctl.path_roles import PathRole, classify_path_role
from tools.repoctl.repositories import RepoTarget, require_repo_target
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


def test_context_identity_and_path_roles_are_representation_stable(tmp_path: Path) -> None:
    chunk = chunk_text_source(tmp_path, "repos/web/src/auth.py", "def authenticate(): pass\n", kind="current_source", section="authenticate")

    assert _identity_score("src/auth.py", chunk) == (1.5, "exact path match")
    assert _identity_score("src/auth.py.old", chunk) == (0.0, "")
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
        "continuations": _knowledge_continuations(record),
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


def test_context_continuation_producers_require_their_typed_primary() -> None:
    assert _knowledge_continuations({"source_refs": [{"path": "docs/source.md"}]}) == []

    target = RepoTarget("main", Path("repos"), "repos", "reserved")
    missing_caller = ContextCandidate(
        source_ref=ContextSourceRef(kind="graph_relation", path="<broken-call>"),
        text="broken call relation",
        score=1.0,
        score_breakdown={"graph": 1.0},
        graph_path=[
            {
                "edge": "CALLS",
                "from_path": "src/caller.py",
                "to_path": "src/callee.py",
                "to_symbol": {"qualified_name": "callee"},
            }
        ],
    )
    assert _candidate_continuations(missing_caller, target=target) == []

    valid_call = ContextCandidate(
        source_ref=ContextSourceRef(kind="graph_relation", path="<valid-call>"),
        text="valid call relation",
        score=1.0,
        score_breakdown={"graph": 1.0},
        graph_path=[
            {
                "edge": "CALLS",
                "from_path": "src/caller.py",
                "from_symbol": {"qualified_name": "caller"},
                "to_path": "src/callee.py",
                "to_symbol": {"qualified_name": "callee"},
            }
        ],
    )
    assert _candidate_continuations(valid_call, target=target)[0]["selector"] == {
        "kind": "symbol",
        "value": "caller",
        "in_file": "src/caller.py",
    }


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
    assert bundle["schema_version"] == 4
    assert bundle["view"] == "compact"
    grouped_items = [item for items in bundle["groups"].values() for item in items if isinstance(item.get("source_ref"), dict)]
    refs = [item["source_ref"] for item in grouped_items]
    graph_contracts = [item for item in grouped_items if item["source_ref"]["path"] == "docs/contracts/repoctl-graph-contract.md"]
    assert len(graph_contracts) == 1
    assert {section["section"] for section in graph_contracts[0]["sections"]} == {"repoctl Graph contract", "Source authority"}
    assert all(ref["content_sha256"].startswith("sha256:") for ref in refs)
    assert "graph" not in payload["data"]
    assert payload["warnings"][0]["code"] == "context_not_authoritative"


def test_context_fts_preserves_sqlite_match_order(tmp_path: Path) -> None:
    chunks = [
        chunk_text_source(tmp_path, "a.py", "alpha alpha alpha alpha alpha alpha helper filler words", kind="current_source", section="a.py"),
        chunk_text_source(tmp_path, "b.py", "alpha helper filler words only once here", kind="current_source", section="b.py"),
    ]

    results = retrieve_context("alpha", chunks)

    assert [candidate.source_ref.path for candidate in results] == ["a.py", "b.py"]
    assert results[0].score_breakdown["fts"] > results[1].score_breakdown["fts"]


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


def test_context_query_returns_actionable_groups_for_call_impact(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "auth").mkdir()
    (repo / "auth/flow.py").write_text(
        'def validate_token(token: str) -> bool:\n    return token == "ok"\n\n\ndef login(token: str) -> str:\n    if validate_token(token):\n        return "ok"\n    return "denied"\n',
        encoding="utf-8",
    )
    _materialize(tmp_path)

    assert main(["context", "query", "token validation flow impact", "--mode", "call-impact", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["query"]["mode"] == "call_impact"
    groups = bundle["groups"]
    assert any(item["source_ref"]["kind"] == "graph_relation" for item in groups["callers_and_dependents"])
    assert any("login --CALLS--> validate_token" in item["excerpt"] for item in groups["callers_and_dependents"])
    assert any(item["selector"] == {"kind": "file", "value": "auth/flow.py"} for item in bundle["continuations"])
    assert all("continuations" not in item for items in groups.values() for item in items)
    assert bundle["repository"]["id"] == "main"
    assert all("repo_id" not in item for items in groups.values() for item in items)


def test_context_compact_preserves_direct_anchor_order_and_role(tmp_path: Path, monkeypatch, capsys) -> None:
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
    assert any(item["source_ref"]["kind"] == "graph_relation" for item in bundle["groups"]["callers_and_dependents"])
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
    assert related_surface[1]["source_ref"]["path"] == "repos/flow.py"
    assert related_surface[1]["evidence_role"] == "dependent_source"


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
            problem["code"] == "context_graph_unavailable" and dependency_code in problem["message"]
            for problem in payload["problems"]
        )
        assert any(
            item.get("source_ref", {}).get("path") == "repos/app.py"
            for items in payload["data"]["bundle"]["groups"].values()
            for item in items
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

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--kind", "decision", "--json"]) == 0
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










def test_context_query_configured_multi_requires_repo_id(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_multirepo_workspace(tmp_path, monkeypatch)

    assert main(["context", "query", "Graph authority", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"


def test_context_multirepo_field_loop_keeps_context_and_knowledge_namespaced(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_multirepo_workspace(tmp_path, monkeypatch)
    fixture = Path("tests/fixtures/context-benchmark-multirepo").resolve()
    _write_context_benchmark_collection_corpus(tmp_path, fixture)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "web", "--json"]) == 0
    web_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", web_candidate, "--repo-id", "web", "--json"]) == 0
    web_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "api", "--json"]) == 0
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
    assert bundle["completeness"]["knowledge_result_count"] == 1
    assert bundle["completeness"]["knowledge_lifecycle"]["available_statuses"] == {"reviewed": 1}
    assert bundle["completeness"]["knowledge_lifecycle"]["returned_statuses"] == {"reviewed": 1}
    assert all(item["source_ref"]["kind"] != "knowledge_record" for item in bundle["evidence"])

    source = tmp_path / "docs/contracts/repoctl-context-contract.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")

    assert main(["context", "query", "reviewed knowledge source authority", "--repo-id", "main", "--mode", "authority", "--full", "--json"]) == 0
    stale_payload = json.loads(capsys.readouterr().out)
    stale_bundle = stale_payload["data"]["bundle"]
    assert stale_bundle["knowledge_results"] == []
    assert stale_bundle["completeness"]["knowledge_available_record_count"] == 1
    assert stale_bundle["completeness"]["knowledge_lifecycle"]["available_statuses"] == {"stale": 1}
    assert stale_bundle["completeness"]["knowledge_lifecycle"]["excluded_statuses"] == {"stale": 1}
    assert stale_bundle["completeness"]["knowledge_lifecycle"]["returned_statuses"] == {}
    assert any(problem["code"] == "knowledge_stale_record_excluded" for problem in stale_payload["problems"])




def test_knowledge_render_check_reports_broken_links(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--kind", "decision", "--json"]) == 0
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

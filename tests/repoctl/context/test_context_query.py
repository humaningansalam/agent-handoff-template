from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.context import compact_context_bundle
from tools.repoctl.context_chunks import chunk_text_source
from tools.repoctl.context_model import ContextBundle, ContextCandidate, ContextSourceRef
from tools.repoctl.context_retrieval import retrieve_context
from tools.repoctl.graph_store import materialize_graph
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


def test_compact_context_bounds_groups_and_omits_repository_wide_diagnostics() -> None:
    evidence = [
        ContextCandidate(
            source_ref=ContextSourceRef(
                kind="document",
                path="docs/PRD.md" if index < 2 else f"docs/contracts/source-{index}.md",
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
        groups={"must_read": [candidate.to_dict() for candidate in evidence]},
    ).with_digest()

    compact = compact_context_bundle(bundle, max_group_items=2)

    assert len(compact["groups"]["must_read"]) == 2
    assert [item["source_ref"]["section"] for item in compact["groups"]["must_read"]] == ["Decision 0", "Decision 1"]
    assert compact["selection"]["group_counts"]["must_read"] == 10
    assert compact["selection"]["displayed_group_counts"]["must_read"] == 2
    assert compact["selection"]["omitted_group_items"]["must_read"] == 8
    assert "selected_source_refs" not in compact
    assert "source_snapshots" not in compact
    assert "analyzed_paths" not in json.dumps(compact)


def test_context_query_returns_source_bundle(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")

    assert main(["context", "query", "Why is Graph non-authoritative?", "--mode", "authority", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    bundle = payload["data"]["bundle"]
    assert bundle["schema"] == "repoctl.context.bundle"
    assert bundle["authoritative"] is False
    assert bundle["repository"] == {"id": "main", "path": "repos", "identity_source": "reserved"}
    assert bundle["schema_version"] == 3
    assert bundle["view"] == "compact"
    refs = [item["source_ref"] for items in bundle["groups"].values() for item in items if isinstance(item.get("source_ref"), dict)]
    assert any(ref["path"] == "docs/contracts/repoctl-graph-contract.md" and ref.get("section") == "repoctl Graph contract" for ref in refs)
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
    assert bundle["repository"]["id"] == "main"
    assert all("repo_id" not in item for items in groups.values() for item in items)


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
    assert bundle["completeness"]["evidence_problem_count"] == 1
    assert any(problem["code"] == "context_current_source_too_large" for problem in payload["problems"])


def test_context_query_uses_materialized_index_with_dirty_path_overlay(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def run():\n    return 'old'\n", encoding="utf-8")
    _materialize(tmp_path)
    (repo / "app.py").write_text("def brand_new_overlay_token():\n    return 'new'\n", encoding="utf-8")
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- active task changed after Graph build\n", encoding="utf-8")
    monkeypatch.setattr(
        "tools.repoctl.context.collect_context_sources",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("context performed a full source scan")),
    )

    assert main(["context", "query", "brand_new_overlay_token", "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
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

    assert main(["context", "query", "What should I read first for this project?", "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    warning_codes = {warning["code"] for warning in [*payload["warnings"], *payload["problems"]]}
    assert "context_graph_completion_receipt_invalid" in warning_codes
    bundle = payload["data"]["bundle"]
    assert bundle["completeness"]["receipt_problem_count"] == 1
    assert bundle["completeness"]["receipt_set_complete"] is False


def test_default_context_query_keeps_related_completion_history_separate(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "auth.py").write_text("def validate_token(token: str) -> bool:\n    return bool(token)\n", encoding="utf-8")
    _write_completion_receipt(tmp_path)
    _materialize(tmp_path)

    assert main(["context", "query", "validate_token token validation", "--repo-id", "main", "--json"]) == 0

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

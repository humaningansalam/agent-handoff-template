from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.context_model import ContextBundle, ContextCandidate, ContextSourceRef
from tools.repoctl.graph_model import digest_data, file_id
from tests.repoctl.test_check import write_workspace
from tests.repoctl.test_meta_check import write_repometa
from tests.repoctl.test_repositories import init_repo, write_settings


def _write_context_docs(root: Path) -> None:
    (root / "docs/adr").mkdir(parents=True, exist_ok=True)
    (root / "docs/contracts").mkdir(parents=True, exist_ok=True)
    (root / "docs/workflows").mkdir(parents=True, exist_ok=True)
    (root / "docs/adr/repoctl-graph-v0.md").write_text(
        "# ADR: repoctl Graph v0\n\n## Decision\n\nGraph is a read-only derived evidence snapshot. Source authorities remain repo registry, code index, .repometa, and task completion receipts.\n",
        encoding="utf-8",
    )
    (root / "docs/adr/evidence-context-authority-v0.md").write_text(
        "# ADR: Evidence Context Authority v0\n\n## Decision\n\nEvidence Context comes before reviewed knowledge and keeps source bundles separate.\n\n## Authority Rules\n\nEvidence Context is read-only and non-authoritative. Context retrieval does not replace Graph, task, Board, Backlog, or .repometa authority.\n",
        encoding="utf-8",
    )
    (root / "docs/contracts/repoctl-module-boundaries.md").write_text(
        "# repoctl module boundaries\n\n## Future layer rules\n\nContext must not replace task, Board, Backlog, Graph, or .repometa authority.\n",
        encoding="utf-8",
    )
    (root / "docs/workflows/generated.md").write_text("# Workflow\n\nGenerated output is not an authority.\n", encoding="utf-8")


def _write_context_benchmark_corpus(root: Path, fixture: Path | None = None) -> None:
    fixture = fixture or Path("tests/fixtures/context-benchmark")
    corpus = json.loads((fixture / "corpus.json").read_text(encoding="utf-8"))
    repo = root / "repos"
    main = corpus["repositories"]["main"]
    for item in main["files"]:
        path = repo / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"], encoding="utf-8")


def _write_context_benchmark_collection_corpus(root: Path, fixture: Path) -> None:
    corpus = json.loads((fixture / "corpus.json").read_text(encoding="utf-8"))
    for repo_id, repo_corpus in corpus["repositories"].items():
        for item in repo_corpus["files"]:
            path = root / "repos" / repo_id / item["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["content"], encoding="utf-8")


def _approve_superseded_context_knowledge(capsys) -> tuple[str, str]:
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    first_candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", first_candidate_id, "--repo-id", "main", "--json"]) == 0
    old_record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    replacement_candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", replacement_candidate_id, "--repo-id", "main", "--supersedes", old_record_id, "--json"]) == 0
    new_record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    return old_record_id, new_record_id


def _approve_deprecated_context_knowledge(tmp_path: Path, capsys) -> str:
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    reason = tmp_path / "deprecated-reason.md"
    reason.write_text("Decision is no longer current but remains historical evidence.\n", encoding="utf-8")
    assert main(["knowledge", "deprecate", record_id, "--repo-id", "main", "--reason-file", reason.as_posix(), "--json"]) == 0
    capsys.readouterr()
    return record_id


def test_context_query_returns_source_bundle(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["context", "query", "Why is Graph non-authoritative?", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    bundle = payload["data"]["bundle"]
    assert bundle["schema"] == "repoctl.context.bundle"
    assert bundle["authoritative"] is False
    assert bundle["repository"] == {"id": "main", "path": "repos", "identity_source": "reserved"}
    assert bundle["source_snapshots"]["graph_digest"].startswith("sha256:")
    refs = [candidate["source_ref"] for candidate in bundle["packed_context"]]
    assert any(ref["path"] == "docs/adr/repoctl-graph-v0.md" and ref.get("section") == "Decision" for ref in refs)
    assert all(ref["content_sha256"].startswith("sha256:") for ref in refs)
    assert payload["warnings"][0]["code"] == "context_not_authoritative"


def test_context_query_is_deterministic(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["context", "query", "source authorities", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert main(["context", "query", "source authorities", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)["data"]["bundle"]

    assert first["bundle_digest"] == second["bundle_digest"]
    assert first == second


def test_context_query_respects_budget(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["context", "query", "Graph authority", "--budget-tokens", "10", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["budget"]["requested_tokens"] == 10
    assert bundle["budget"]["estimated_tokens"] <= 10


def test_context_query_configured_multi_requires_repo_id(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_repometa(tmp_path / "repos/web")
    write_repometa(tmp_path / "repos/api")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["context", "query", "Graph authority", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"


def test_context_benchmark_fixture_has_source_refs() -> None:
    fixture = Path("tests/fixtures/context-benchmark")
    questions = [json.loads(line) for line in (fixture / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    expected = json.loads((fixture / "expected-sources.json").read_text(encoding="utf-8"))
    corpus = json.loads((fixture / "corpus.json").read_text(encoding="utf-8"))

    assert len(questions) >= 7
    assert {question["category"] for question in questions} >= {"authority", "contract", "impact", "reference-impact", "method-impact"}
    assert corpus["schema"] == "repoctl.context.benchmark.corpus"
    assert len(corpus["repositories"]["main"]["files"]) >= 10
    for question in questions:
        assert question["id"] in expected
        assert expected[question["id"]]["required_source_refs"]


def test_context_benchmark_multirepo_fixture_has_source_refs() -> None:
    fixture = Path("tests/fixtures/context-benchmark-multirepo")
    questions = [json.loads(line) for line in (fixture / "questions.jsonl").read_text(encoding="utf-8").splitlines() if line.strip()]
    expected = json.loads((fixture / "expected-sources.json").read_text(encoding="utf-8"))
    corpus = json.loads((fixture / "corpus.json").read_text(encoding="utf-8"))

    assert {question["category"] for question in questions} == {"multi-repo-isolation"}
    assert sorted(corpus["repositories"]) == ["api", "web"]
    for question in questions:
        assert question["id"] in expected
        assert expected[question["id"]]["required_source_refs"]
        assert expected[question["id"]]["forbidden_refs"]


def test_context_benchmark_scores_fixture(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    fixture = Path("tests/fixtures/context-benchmark").resolve()

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "context benchmark"
    assert payload["data"]["question_count"] >= 2
    assert payload["data"]["summary"]["source_ref_integrity"] is True
    assert payload["data"]["summary"]["mean_recall_at_5"] > 0
    assert payload["data"]["summary"]["by_category"]["authority"]["mean_packed_recall"] == 1.0
    assert payload["data"]["summary"]["by_category"]["impact"]["mean_recall_at_5"] == 1.0
    assert payload["data"]["summary"]["by_category"]["reference-impact"]["mean_recall_at_5"] == 1.0
    assert payload["data"]["summary"]["by_category"]["reference-impact"]["mean_graph_edge_recall"] == 1.0
    assert payload["data"]["summary"]["by_category"]["method-impact"]["mean_graph_edge_recall"] == 1.0
    assert payload["data"]["summary"]["by_category"]["cross-file-call-impact"]["mean_graph_edge_recall"] == 1.0
    assert payload["data"]["summary"]["knowledge_expected_questions"] >= 1
    assert payload["data"]["summary"]["knowledge_result_questions"] == 0
    assert payload["warnings"][0]["code"] == "context_benchmark_retrieval_only"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-recall-at-5", "impact=1.0", "--require-fixture-corpus", "--json"]) == 0

    gated_payload = json.loads(capsys.readouterr().out)
    assert gated_payload["data"]["summary"]["by_category"]["impact"]["mean_recall_at_5"] == 1.0
    assert gated_payload["data"]["fixture_corpus"]["missing_count"] == 0
    assert gated_payload["data"]["fixture_corpus"]["digest_drift_count"] == 0
    assert gated_payload["problems"] == []

    reference_result = next(result for result in payload["data"]["results"] if result["id"] == "Q-006")
    assert reference_result["metrics"]["graph_edge_recall"] == 1.0
    assert reference_result["required_graph_edges_found"][0]["kind"] == "CALLS"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-graph-edge-recall", "reference-impact=1.0", "--require-fixture-corpus", "--json"]) == 0

    reference_payload = json.loads(capsys.readouterr().out)
    assert reference_payload["data"]["summary"]["by_category"]["reference-impact"]["mean_graph_edge_recall"] == 1.0
    assert reference_payload["problems"] == []

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-packed-recall", "authority=1.0", "--require-fixture-corpus", "--json"]) == 0

    authority_payload = json.loads(capsys.readouterr().out)
    authority_result = next(result for result in authority_payload["data"]["results"] if result["id"] == "Q-001")
    assert authority_result["metrics"]["packed_recall"] == 1.0
    assert len(authority_result["packed_required_found_refs"]) == 2
    assert authority_payload["problems"] == []

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-graph-edge-recall", "method-impact=1.0", "--require-fixture-corpus", "--json"]) == 0

    method_payload = json.loads(capsys.readouterr().out)
    method_result = next(result for result in method_payload["data"]["results"] if result["id"] == "Q-007")
    assert method_result["metrics"]["graph_edge_recall"] == 1.0
    assert method_result["required_graph_edges_found"][0]["kind"] == "CALLS"
    assert method_payload["problems"] == []

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-graph-edge-recall", "cross-file-call-impact=1.0", "--require-fixture-corpus", "--json"]) == 0

    cross_file_payload = json.loads(capsys.readouterr().out)
    cross_file_result = next(result for result in cross_file_payload["data"]["results"] if result["id"] == "Q-008")
    assert cross_file_result["metrics"]["graph_edge_recall"] == 1.0
    assert cross_file_result["required_graph_edges_found"][0]["kind"] == "CALLS"
    assert cross_file_payload["problems"] == []


def test_context_benchmark_fixture_corpus_gate_fails_when_not_applied(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--require-fixture-corpus", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["fixture_corpus"]["present"] is True
    assert payload["data"]["fixture_corpus"]["missing_count"] >= 1
    assert any(problem["code"] == "context_benchmark_corpus_file_missing" for problem in payload["problems"])
    assert any(action["label"] == "Apply the declared benchmark corpus before running this gate" for action in payload["next_actions"])
    assert any(action.get("path") == "tests/fixtures/context-benchmark/corpus.json" for action in payload["next_actions"])


def test_context_benchmark_writes_output_artifact(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()
    output = tmp_path / ".repoctl-state/context-benchmark/result.json"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact == payload
    assert payload["data"]["benchmark_digest"].startswith("sha256:")
    assert payload["data"]["artifact"] == {
        "path": ".repoctl-state/context-benchmark/result.json",
        "benchmark_digest": payload["data"]["benchmark_digest"],
    }


def test_context_benchmark_rejects_output_outside_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()
    outside = tmp_path.parent / f"{tmp_path.name}-context-benchmark.json"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", outside.as_posix(), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "context_benchmark_output_outside_workspace"
    assert not outside.exists()


def test_context_benchmark_compare_artifacts(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()
    baseline = tmp_path / ".repoctl-state/context-benchmark/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-benchmark/candidate.json"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    candidate.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")

    assert main(["context", "benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-recall-at-5-drop", "0", "--max-question-recall-at-5-drop", "0", "--json"]) == 0

    pass_payload = json.loads(capsys.readouterr().out)
    assert pass_payload["data"]["metric_deltas"]["mean_recall_at_5"]["delta"] == 0.0
    assert all(item["metrics"]["recall_at_5"]["delta"] == 0.0 for item in pass_payload["data"]["question_deltas"])
    assert pass_payload["problems"] == []

    regressed = json.loads(candidate.read_text(encoding="utf-8"))
    regressed["data"]["summary"]["mean_recall_at_5"] = 0.0
    for result in regressed["data"]["results"]:
        result["metrics"]["recall_at_5"] = 0.0
    digest_basis = {key: value for key, value in regressed["data"].items() if key not in {"benchmark_digest", "artifact"}}
    regressed["data"]["benchmark_digest"] = digest_data(digest_basis)
    regressed["data"]["artifact"]["benchmark_digest"] = regressed["data"]["benchmark_digest"]
    candidate.write_text(json.dumps(regressed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-recall-at-5-drop", "0", "--max-question-recall-at-5-drop", "0", "--json"]) == 1

    fail_payload = json.loads(capsys.readouterr().out)
    assert fail_payload["data"]["metric_deltas"]["mean_recall_at_5"]["delta"] < 0
    assert any(problem["code"] == "context_benchmark_recall_regressed" for problem in fail_payload["problems"])
    assert any(problem["code"] == "context_benchmark_question_recall_regressed" for problem in fail_payload["problems"])

    missing = json.loads(baseline.read_text(encoding="utf-8"))
    missing_question_id = missing["data"]["results"][0]["id"]
    missing["data"]["results"] = missing["data"]["results"][1:]
    digest_basis = {key: value for key, value in missing["data"].items() if key not in {"benchmark_digest", "artifact"}}
    missing["data"]["benchmark_digest"] = digest_data(digest_basis)
    missing["data"]["artifact"]["benchmark_digest"] = missing["data"]["benchmark_digest"]
    candidate.write_text(json.dumps(missing, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--json"]) == 1

    missing_payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "context_benchmark_question_missing" and problem["path"] == missing_question_id for problem in missing_payload["problems"])

    failed_artifact = json.loads(baseline.read_text(encoding="utf-8"))
    failed_artifact["ok"] = False
    failed_artifact["problems"] = [{"severity": "error", "code": "synthetic_failure", "message": "failed"}]
    candidate.write_text(json.dumps(failed_artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--json"]) == 1

    failed_payload = json.loads(capsys.readouterr().out)
    assert failed_payload["problems"][0]["code"] == "context_benchmark_artifact_failed"


def test_context_benchmark_compare_detects_source_digest_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()
    baseline = tmp_path / ".repoctl-state/context-benchmark/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-benchmark/candidate.json"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    candidate.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")
    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged after benchmark artifact.\n", encoding="utf-8")

    assert main(["context", "benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--require-current-sources", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["gates"]["require_current_sources"] is True
    assert any(problem["code"] == "context_benchmark_artifact_source_digest_drift" for problem in payload["problems"])
    assert any(item["code"] == "context_benchmark_artifact_source_digest_drift" for item in payload["data"]["source_drift"])


def test_context_benchmark_compare_detects_missing_source_after_rename(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()
    baseline = tmp_path / ".repoctl-state/context-benchmark/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-benchmark/candidate.json"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    candidate.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")
    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.rename(tmp_path / "docs/adr/evidence-context-authority-renamed.md")

    assert main(["context", "benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--require-current-sources", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "context_benchmark_artifact_source_missing" for problem in payload["problems"])
    assert any(item["path"] == "docs/adr/evidence-context-authority-v0.md" for item in payload["data"]["source_drift"])


def test_context_benchmark_scores_reviewed_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--kind", "decision", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    q2 = next(result for result in payload["data"]["results"] if result["id"] == "Q-002")
    assert payload["data"]["summary"]["knowledge_result_questions"] >= 1
    assert payload["data"]["summary"]["mean_knowledge_recall_at_5"] == 1.0
    assert payload["data"]["summary"]["knowledge_score_breakdown_integrity"] is True
    assert payload["data"]["summary"]["knowledge_source_status_current"] is True
    assert q2["metrics"]["knowledge_recall_at_5"] == 1.0
    assert q2["metrics"]["knowledge_score_breakdown_present"] is True
    assert q2["metrics"]["knowledge_source_status_current"] is True
    assert q2["metrics"]["knowledge_stale_record_excluded"] == 0
    assert q2["metrics"]["knowledge_superseded_record_excluded"] == 0
    assert payload["data"]["summary"]["by_category"]["contract"]["mean_knowledge_recall_at_5"] == 1.0
    assert payload["data"]["summary"]["by_category"]["contract"]["knowledge_expected_questions"] == 1
    assert q2["required_knowledge_found_at_5"][0]["path"] == "docs/adr/evidence-context-authority-v0.md"
    assert q2["required_knowledge_found_at_5"][0]["section"] == "Decision"
    assert q2["knowledge_score_results"][0]["has_field_breakdown"] is True
    assert "exact_claim" in q2["knowledge_score_results"][0]["score_breakdown_keys"]
    assert q2["knowledge_source_statuses"][0]["digest_matches"] is True


def test_context_benchmark_quality_gate_fails_without_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-knowledge-recall-at-5", "1.0", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["gates"]["min_knowledge_recall_at_5"] == 1.0
    assert payload["problems"][0]["code"] == "context_benchmark_knowledge_gate_failed"


def test_context_benchmark_quality_gate_passes_with_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-knowledge-recall-at-5", "1.0", "--require-source-integrity", "--require-knowledge-source-current", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["summary"]["mean_knowledge_recall_at_5"] == 1.0
    assert payload["data"]["summary"]["knowledge_source_status_current"] is True
    assert payload["data"]["gates"]["require_knowledge_source_current"] is True
    assert payload["problems"] == []

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-knowledge-recall-at-5", "contract=1.0", "--json"]) == 0

    category_payload = json.loads(capsys.readouterr().out)
    assert category_payload["data"]["gates"]["min_category_knowledge_recall_at_5"] == {"contract": 1.0}
    assert category_payload["data"]["summary"]["by_category"]["contract"]["mean_knowledge_recall_at_5"] == 1.0
    assert category_payload["problems"] == []


def test_context_benchmark_forbidden_gate_fails_on_forbidden_source(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = tmp_path / "context-fixture"
    fixture.mkdir()
    (fixture / "questions.jsonl").write_text(
        '{"id":"Q-FORBIDDEN","category":"leakage","repo_id":"main","question":"Generated output authority"}\n',
        encoding="utf-8",
    )
    (fixture / "expected-sources.json").write_text(
        json.dumps(
            {
                "Q-FORBIDDEN": {
                    "required_source_refs": [],
                    "required_knowledge_source_refs": [],
                    "acceptable_optional_refs": [],
                    "forbidden_refs": [{"path": "docs/workflows/generated.md"}],
                }
            }
        ),
        encoding="utf-8",
    )

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--require-no-forbidden", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    result = payload["data"]["results"][0]
    assert payload["data"]["summary"]["forbidden_selected"] >= 1
    assert result["metrics"]["forbidden_selected"] >= 1
    assert result["selected_forbidden"][0]["path"] == "docs/workflows/generated.md"
    assert payload["data"]["gates"]["require_no_forbidden"] is True
    assert payload["problems"][0]["code"] == "context_benchmark_forbidden_selected"


def test_context_benchmark_multi_repo_isolation_passes_for_selected_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_repometa(tmp_path / "repos/web")
    write_repometa(tmp_path / "repos/api")
    (tmp_path / "repos/web/app.py").write_text("def web_auth():\n    return 'web'\n", encoding="utf-8")
    (tmp_path / "repos/api/app.py").write_text("def api_auth():\n    return 'api'\n", encoding="utf-8")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = tmp_path / "context-fixture"
    fixture.mkdir()
    (fixture / "questions.jsonl").write_text(
        '{"id":"Q-WEB","category":"multi-repo","repo_id":"web","question":"web auth repository graph"}\n',
        encoding="utf-8",
    )
    (fixture / "expected-sources.json").write_text(
        json.dumps({"Q-WEB": {"required_source_refs": [], "required_knowledge_source_refs": [], "acceptable_optional_refs": [], "forbidden_refs": []}}),
        encoding="utf-8",
    )

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--require-no-cross-repo", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["summary"]["cross_repo_ref_count"] == 0
    assert payload["data"]["results"][0]["cross_repo_refs"] == []
    assert payload["data"]["gates"]["require_no_cross_repo"] is True


def test_context_benchmark_multirepo_fixture_gates_isolation(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_repometa(tmp_path / "repos/web")
    write_repometa(tmp_path / "repos/api")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    fixture = Path("tests/fixtures/context-benchmark-multirepo").resolve()
    _write_context_benchmark_collection_corpus(tmp_path, fixture)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--require-fixture-corpus", "--require-no-cross-repo", "--require-no-forbidden", "--min-category-packed-recall", "multi-repo-isolation=1.0", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    result = payload["data"]["results"][0]
    assert result["repo_id"] == "web"
    assert result["metrics"]["packed_recall"] == 1.0
    assert result["selected_forbidden"] == []
    assert result["cross_repo_refs"] == []
    assert payload["data"]["fixture_corpus"]["missing_count"] == 0
    assert payload["data"]["summary"]["by_category"]["multi-repo-isolation"]["cross_repo_ref_count"] == 0
    assert payload["problems"] == []


def test_context_benchmark_import_impact_passes_after_resolution(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "utils").mkdir()
    (repo / "handlers").mkdir()
    (repo / "utils/__init__.py").write_text("", encoding="utf-8")
    (repo / "utils/tokens.py").write_text("def issue_token(user_id: str) -> str:\n    return f'token:{user_id}'\n", encoding="utf-8")
    (repo / "handlers/login.py").write_text(
        "from utils.tokens import issue_token as make_session\n\n\ndef login(user_id: str) -> str:\n    return make_session(user_id)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    fixture = tmp_path / "context-impact-fixture"
    fixture.mkdir()
    (fixture / "questions.jsonl").write_text(
        '{"id":"Q-IMPACT-IMPORT","category":"impact","repo_id":"main","question":"What files are impacted if utils/tokens.py changes?"}\n',
        encoding="utf-8",
    )
    (fixture / "expected-sources.json").write_text(
        json.dumps(
            {
                "Q-IMPACT-IMPORT": {
                    "required_source_refs": [
                        {"path": f"<graph:{file_id('main', 'utils/tokens.py')}>"},
                        {"path": f"<graph:{file_id('main', 'handlers/login.py')}>"},
                    ],
                    "required_knowledge_source_refs": [],
                    "acceptable_optional_refs": [],
                    "forbidden_refs": [],
                }
            },
            sort_keys=True,
        ),
        encoding="utf-8",
    )

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    result = payload["data"]["results"][0]
    impact_summary = payload["data"]["summary"]["by_category"]["impact"]
    assert result["metrics"]["recall_at_5"] == 1.0
    assert impact_summary["question_count"] == 1
    assert impact_summary["mean_recall_at_5"] == result["metrics"]["recall_at_5"]
    assert {"path": f"<graph:{file_id('main', 'handlers/login.py')}>"} in result["required_found_at_5"]
    assert payload["data"]["gates"]["min_category_recall_at_5"] == {}

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-recall-at-5", "impact=1.0", "--json"]) == 0

    gated_payload = json.loads(capsys.readouterr().out)
    assert gated_payload["data"]["gates"]["min_category_recall_at_5"] == {"impact": 1.0}
    assert gated_payload["data"]["summary"]["by_category"]["impact"]["mean_recall_at_5"] == 1.0
    assert gated_payload["problems"] == []


def test_context_benchmark_cross_repo_gate_fails_on_foreign_graph_ref(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_repometa(tmp_path / "repos/web")
    write_repometa(tmp_path / "repos/api")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    fixture = tmp_path / "context-fixture"
    fixture.mkdir()
    (fixture / "questions.jsonl").write_text(
        '{"id":"Q-WEB","category":"multi-repo","repo_id":"web","question":"auth"}\n',
        encoding="utf-8",
    )
    (fixture / "expected-sources.json").write_text(
        json.dumps({"Q-WEB": {"required_source_refs": [], "required_knowledge_source_refs": [], "acceptable_optional_refs": [], "forbidden_refs": []}}),
        encoding="utf-8",
    )
    foreign = ContextCandidate(
        source_ref=ContextSourceRef(kind="graph_node", path="<graph:repo:api:file:app.py>", section="file app.py", content_sha256="sha256:" + "0" * 64),
        text='{"identity":{"repo_id":"api","path":"app.py"}}',
        score=1.0,
        score_breakdown={"exact": 1.0},
    )
    bundle = ContextBundle(
        repository={"id": "web", "path": "repos/web", "identity_source": "pinned"},
        query={"text": "auth"},
        source_snapshots={},
        completeness={},
        candidates=[foreign],
        packed_context=[foreign],
        budget={"requested_tokens": 3000, "estimated_tokens": 10, "candidate_count": 1, "packed_count": 1},
    ).with_digest()
    monkeypatch.setattr("tools.repoctl.context_benchmark.build_context_bundle", lambda *args, **kwargs: (bundle, [], {}))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--require-no-cross-repo", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["summary"]["cross_repo_ref_count"] == 2
    assert payload["data"]["results"][0]["cross_repo_refs"][0]["path"] == "<graph:repo:api:file:app.py>"
    assert payload["problems"][0]["code"] == "context_benchmark_cross_repo_leakage"


def test_context_benchmark_knowledge_source_current_gate_fails_on_stale_record(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged after approval.\n", encoding="utf-8")

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--require-knowledge-source-current", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["summary"]["knowledge_source_status_current"] is False
    assert payload["data"]["summary"]["knowledge_stale_record_excluded"] >= 1
    assert payload["data"]["summary"]["knowledge_superseded_record_excluded"] == 0
    assert payload["data"]["gates"]["require_knowledge_source_current"] is True
    assert any(problem["code"] == "context_benchmark_knowledge_source_stale" for problem in payload["problems"])


def test_context_benchmark_counts_superseded_knowledge_exclusion(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()

    old_record_id, new_record_id = _approve_superseded_context_knowledge(capsys)

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-knowledge-recall-at-5", "contract=1.0", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    contract = payload["data"]["summary"]["by_category"]["contract"]
    assert payload["data"]["summary"]["knowledge_superseded_record_excluded"] >= 1
    assert contract["knowledge_superseded_record_excluded"] >= 1
    assert contract["mean_knowledge_recall_at_5"] == 1.0
    q2 = next(result for result in payload["data"]["results"] if result["id"] == "Q-002")
    assert q2["metrics"]["knowledge_superseded_record_excluded"] >= 1
    assert q2["required_knowledge_found_at_5"][0]["path"] == "docs/adr/evidence-context-authority-v0.md"

    assert main(["knowledge", "query", "source authorities remain after context retrieval", "--repo-id", "main", "--include-superseded", "--json"]) == 0

    query_payload = json.loads(capsys.readouterr().out)
    statuses = {item["record"]["id"]: item["record"]["status"] for item in query_payload["data"]["results"]}
    assert statuses[old_record_id] == "superseded"
    assert statuses[new_record_id] == "reviewed"


def test_context_benchmark_counts_deprecated_knowledge_exclusion(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()

    record_id = _approve_deprecated_context_knowledge(tmp_path, capsys)

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    contract = payload["data"]["summary"]["by_category"]["contract"]
    assert payload["data"]["summary"]["knowledge_deprecated_record_excluded"] >= 1
    assert contract["knowledge_deprecated_record_excluded"] >= 1
    assert contract["mean_knowledge_recall_at_5"] == 0.0
    q2 = next(result for result in payload["data"]["results"] if result["id"] == "Q-002")
    assert q2["metrics"]["knowledge_deprecated_record_excluded"] >= 1
    assert q2["missing_required_knowledge_at_5"][0]["path"] == "docs/adr/evidence-context-authority-v0.md"

    assert main(["knowledge", "query", "source authorities remain after context retrieval", "--repo-id", "main", "--include-deprecated", "--json"]) == 0

    query_payload = json.loads(capsys.readouterr().out)
    statuses = {item["record"]["id"]: item["record"]["status"] for item in query_payload["data"]["results"]}
    assert statuses[record_id] == "deprecated"


def test_context_benchmark_category_knowledge_gate_fails_without_reviewed_record(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-knowledge-recall-at-5", "contract=1.0", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["gates"]["min_category_knowledge_recall_at_5"] == {"contract": 1.0}
    assert payload["data"]["summary"]["by_category"]["contract"]["mean_knowledge_recall_at_5"] == 0.0
    assert payload["problems"][0]["code"] == "context_benchmark_category_knowledge_gate_failed"


def test_context_pack_groups_task_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    task_path = tmp_path / "docs/tasks/T-20260622010101Z--context-pack.md"
    task_path.write_text(
        """---
id: T-20260622010101Z
title: "Use Evidence Context for Graph authority"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260622T010101Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260622010101Z - Use Evidence Context for Graph authority

## Context Docs

- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: Graph authority context
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Explain why Graph remains non-authoritative.

## Handoff

- Next exact step: inspect context authority ADR.
- First file to open: `docs/adr/evidence-context-authority-v0.md`
- First command to run: `./scripts/repoctl context query "Graph authority" --json`
- Done when: source-backed context is available.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    output = tmp_path / ".repoctl-state/context-pack/T-20260622010101Z.json"
    assert main(["context", "pack", "--task", "T-20260622010101Z", "--repo-id", "main", "--budget-tokens", "1200", "--output", output.as_posix(), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    artifact = json.loads(output.read_text(encoding="utf-8"))
    data = payload["data"]
    assert artifact == payload
    assert payload["command"] == "context pack"
    assert data["authoritative"] is False
    assert data["pack_digest"].startswith("sha256:")
    assert data["artifact"] == {
        "path": ".repoctl-state/context-pack/T-20260622010101Z.json",
        "pack_digest": data["pack_digest"],
    }
    assert data["seed"]["source"] == "task_fields_for_retrieval_only"
    assert any(item["source_ref"]["path"] == "docs/adr/evidence-context-authority-v0.md" for item in data["groups"]["must_read"])
    assert data["groups"]["reviewed_knowledge"] == []
    assert data["bundle"]["budget"]["estimated_tokens"] <= 1200
    assert data["metrics"]["group_counts"]["must_read"] == len(data["groups"]["must_read"])
    assert data["metrics"]["group_counts"]["reviewed_knowledge"] == 0
    assert data["metrics"]["unique_must_read_source_count"] >= 1
    assert data["metrics"]["estimated_tokens"] == data["bundle"]["budget"]["estimated_tokens"]
    assert data["metrics"]["requested_tokens"] == 1200
    assert any(ref["path"] == "docs/adr/evidence-context-authority-v0.md" for ref in data["metrics"]["must_read_source_refs"])
    assert payload["warnings"][0]["code"] == "context_pack_not_authoritative"


def test_context_pack_warns_on_incomplete_graph_code_facts(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    task_path = tmp_path / "docs/tasks/T-20260622010102Z--context-pack-parse-warning.md"
    task_path.write_text(
        """---
id: T-20260622010102Z
title: "Inspect parse warning context"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260622T010102Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260622010102Z - Inspect parse warning context

## Context Docs

- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: parse warning
- Candidate files reviewed: `repos/broken.py`
- Chosen files: `repos/broken.py`

## Goal

Inspect parse warning context.

## Handoff

- Next exact step: inspect graph completeness.
- First file to open: `repos/broken.py`
- First command to run: `./scripts/repoctl context pack --task T-20260622010102Z --repo-id main --json`
- Done when: parse warning is visible.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["context", "pack", "--task", "T-20260622010102Z", "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["bundle"]["completeness"]["graph_completeness"]["parse_error_count"] == 1
    assert any(warning["code"] == "context_pack_graph_code_facts_incomplete" for warning in payload["warnings"])


def test_context_pack_rejects_output_symlink_escape(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    task_path = tmp_path / "docs/tasks/T-20260622011111Z--context-pack-boundary.md"
    task_path.write_text(
        """---
id: T-20260622011111Z
title: "Keep context pack output inside workspace"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260622T011111Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260622011111Z - Keep context pack output inside workspace

## Context Docs

- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: context pack boundary
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Reject context pack output outside the workspace.

## Handoff

- Next exact step: inspect context pack boundary.
- First file to open: `docs/adr/evidence-context-authority-v0.md`
- First command to run: `./scripts/repoctl context pack --task T-20260622011111Z --repo-id main --json`
- Done when: outside output is rejected.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    escape = tmp_path.parent / f"{tmp_path.name}-context-pack-escape"
    escape.mkdir()
    symlink = tmp_path / ".repoctl-state/context-pack"
    symlink.parent.mkdir(parents=True, exist_ok=True)
    symlink.symlink_to(escape, target_is_directory=True)

    assert main(["context", "pack", "--task", "T-20260622011111Z", "--repo-id", "main", "--output", ".repoctl-state/context-pack/out.json", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "context_pack_output_outside_workspace"
    assert not (escape / "out.json").exists()


def test_context_pack_does_not_write_failed_artifact(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    task_path = tmp_path / "docs/tasks/T-20260622012121Z--failed-pack.md"
    task_path.write_text(
        """---
id: T-20260622012121Z
title: "Reject failed context pack artifact"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260622T012121Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260622012121Z - Reject failed context pack artifact

## Context Docs

- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: source authority knowledge
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Do not write failed context pack artifacts.

## Handoff

- Next exact step: inspect failed pack behavior.
- First file to open: `docs/adr/evidence-context-authority-v0.md`
- First command to run: `./scripts/repoctl context pack --task T-20260622012121Z --repo-id main --json`
- Done when: failed pack output is absent.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    event_id = json.loads(capsys.readouterr().out)["data"]["event"]["id"]
    event_path = tmp_path / "docs/knowledge/events" / f"{event_id}.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["record_digest"] = "sha256:" + "6" * 64
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    event_path.write_text(json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output = tmp_path / ".repoctl-state/context-pack/failed.json"

    assert main(["context", "pack", "--task", "T-20260622012121Z", "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_event_record_digest_mismatch"
    assert not output.exists()


def test_context_pack_groups_reviewed_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    task_path = tmp_path / "docs/tasks/T-20260622020202Z--knowledge-pack.md"
    task_path.write_text(
        """---
id: T-20260622020202Z
title: "Use reviewed knowledge for source authority"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260622T020202Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260622020202Z - Use reviewed knowledge for source authority

## Context Docs

- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: source authority knowledge
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Use reviewed knowledge source authority.

## Handoff

- Next exact step: inspect reviewed knowledge.
- First file to open: `docs/adr/evidence-context-authority-v0.md`
- First command to run: `./scripts/repoctl context pack --task T-20260622020202Z --repo-id main --json`
- Done when: reviewed knowledge is visible.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    old_record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    replacement_candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", replacement_candidate_id, "--repo-id", "main", "--supersedes", old_record_id, "--json"]) == 0
    record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]

    assert main(["context", "pack", "--task", "T-20260622020202Z", "--repo-id", "main", "--explain", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    reviewed = payload["data"]["groups"]["reviewed_knowledge"]
    assert reviewed[0]["record"]["id"] == record_id
    assert reviewed[0]["record"]["status"] == "reviewed"
    assert reviewed[0]["record"]["lifecycle_relations"]["supersedes"] == [old_record_id]
    assert reviewed[0]["explain"]["source_ref_statuses"][0]["digest_matches"] is True
    assert payload["data"]["metrics"]["group_counts"]["reviewed_knowledge"] == 1
    assert payload["data"]["metrics"]["group_estimated_tokens"]["reviewed_knowledge"] > 0
    assert payload["data"]["bundle"]["query"]["explain"] is True
    assert payload["data"]["bundle"]["completeness"]["knowledge_lifecycle"]["available_statuses"] == {"reviewed": 1, "superseded": 1}
    assert payload["data"]["bundle"]["completeness"]["knowledge_lifecycle"]["returned_statuses"] == {"reviewed": 1}
    assert any(warning["code"] == "context_pack_knowledge_superseded_excluded" for warning in payload["warnings"])


def test_context_pack_compare_artifacts(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    task_path = tmp_path / "docs/tasks/T-20260622030303Z--pack-compare.md"
    task_path.write_text(
        """---
id: T-20260622030303Z
title: "Compare context pack artifacts"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260622T030303Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260622030303Z - Compare context pack artifacts

## Context Docs

- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: source authority knowledge
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Use reviewed knowledge source authority.

## Handoff

- Next exact step: inspect context pack compare.
- First file to open: `docs/adr/evidence-context-authority-v0.md`
- First command to run: `./scripts/repoctl context pack --task T-20260622030303Z --repo-id main --json`
- Done when: pack compare catches regressions.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    baseline = tmp_path / ".repoctl-state/context-pack/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-pack/candidate.json"

    assert main(["context", "pack", "--task", "T-20260622030303Z", "--repo-id", "main", "--explain", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    candidate.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-must-read-drop", "0", "--max-reviewed-knowledge-drop", "0", "--json"]) == 0

    pass_payload = json.loads(capsys.readouterr().out)
    assert pass_payload["data"]["count_deltas"]["must_read"]["delta"] == 0
    assert pass_payload["data"]["count_deltas"]["reviewed_knowledge"]["delta"] == 0
    assert pass_payload["data"]["metric_deltas"]["unique_must_read_source_count"]["delta"] == 0
    assert pass_payload["data"]["metric_deltas"]["estimated_tokens"]["delta"] == 0
    assert pass_payload["data"]["missing_must_read_refs"] == []
    assert pass_payload["data"]["missing_reviewed_knowledge_ids"] == []
    assert pass_payload["problems"] == []

    swapped_knowledge = json.loads(baseline.read_text(encoding="utf-8"))
    original_record_id = swapped_knowledge["data"]["groups"]["reviewed_knowledge"][0]["record"]["id"]
    swapped_knowledge["data"]["groups"]["reviewed_knowledge"][0]["record"]["id"] = "K-20260622000000Z--other"
    digest_basis = {key: value for key, value in swapped_knowledge["data"].items() if key not in {"pack_digest", "artifact", "repository", "graph"}}
    swapped_knowledge["data"]["pack_digest"] = digest_data(digest_basis)
    swapped_knowledge["data"]["artifact"]["pack_digest"] = swapped_knowledge["data"]["pack_digest"]
    candidate.write_text(json.dumps(swapped_knowledge, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-reviewed-knowledge-drop", "0", "--json"]) == 1

    swapped_knowledge_payload = json.loads(capsys.readouterr().out)
    assert swapped_knowledge_payload["data"]["count_deltas"]["reviewed_knowledge"]["delta"] == 0
    assert original_record_id in swapped_knowledge_payload["data"]["missing_reviewed_knowledge_ids"]
    assert any(problem["code"] == "context_pack_reviewed_knowledge_missing" for problem in swapped_knowledge_payload["problems"])

    swapped = json.loads(baseline.read_text(encoding="utf-8"))
    assert len(swapped["data"]["groups"]["must_read"]) > 1
    missing_ref = swapped["data"]["groups"]["must_read"][0]["source_ref"]
    swapped["data"]["groups"]["must_read"][0] = swapped["data"]["groups"]["must_read"][1]
    digest_basis = {key: value for key, value in swapped["data"].items() if key not in {"pack_digest", "artifact", "repository", "graph"}}
    swapped["data"]["pack_digest"] = digest_data(digest_basis)
    swapped["data"]["artifact"]["pack_digest"] = swapped["data"]["pack_digest"]
    candidate.write_text(json.dumps(swapped, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-must-read-drop", "0", "--json"]) == 1

    swapped_payload = json.loads(capsys.readouterr().out)
    assert swapped_payload["data"]["count_deltas"]["must_read"]["delta"] == 0
    assert any(ref["path"] == missing_ref["path"] and ref["section"] == missing_ref.get("section", "") for ref in swapped_payload["data"]["missing_must_read_refs"])
    assert any(problem["code"] == "context_pack_must_read_ref_missing" for problem in swapped_payload["problems"])

    regressed = json.loads(candidate.read_text(encoding="utf-8"))
    regressed["data"]["groups"]["must_read"] = []
    regressed["data"]["groups"]["reviewed_knowledge"] = []
    digest_basis = {key: value for key, value in regressed["data"].items() if key not in {"pack_digest", "artifact", "repository", "graph"}}
    regressed["data"]["pack_digest"] = digest_data(digest_basis)
    regressed["data"]["artifact"]["pack_digest"] = regressed["data"]["pack_digest"]
    candidate.write_text(json.dumps(regressed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-must-read-drop", "0", "--max-reviewed-knowledge-drop", "0", "--json"]) == 1

    fail_payload = json.loads(capsys.readouterr().out)
    assert fail_payload["data"]["count_deltas"]["must_read"]["delta"] < 0
    assert fail_payload["data"]["count_deltas"]["reviewed_knowledge"]["delta"] < 0
    assert any(problem["code"] == "context_pack_must_read_regressed" for problem in fail_payload["problems"])
    assert any(problem["code"] == "context_pack_reviewed_knowledge_regressed" for problem in fail_payload["problems"])

    failed_artifact = json.loads(baseline.read_text(encoding="utf-8"))
    failed_artifact["ok"] = False
    failed_artifact["problems"] = [{"severity": "error", "code": "synthetic_failure", "message": "failed"}]
    candidate.write_text(json.dumps(failed_artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--json"]) == 1
    failed_artifact_payload = json.loads(capsys.readouterr().out)
    assert failed_artifact_payload["problems"][0]["code"] == "context_pack_artifact_failed"


def test_context_query_includes_reviewed_knowledge_separately(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--kind", "decision", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]

    assert main(["context", "query", "reviewed knowledge source authority", "--repo-id", "main", "--explain", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    bundle = payload["data"]["bundle"]
    assert bundle["knowledge_results"][0]["record"]["id"] == record_id
    assert bundle["knowledge_results"][0]["record"]["status"] == "reviewed"
    assert bundle["knowledge_results"][0]["explain"]["source_ref_statuses"][0]["digest_matches"] is True
    assert bundle["query"]["explain"] is True
    assert bundle["completeness"]["knowledge_result_count"] == 1
    assert bundle["completeness"]["knowledge_lifecycle"]["available_statuses"] == {"reviewed": 1}
    assert bundle["completeness"]["knowledge_lifecycle"]["returned_statuses"] == {"reviewed": 1}
    assert all(candidate["source_ref"]["kind"] != "knowledge_record" for candidate in bundle["packed_context"])

    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")

    assert main(["context", "query", "reviewed knowledge source authority", "--repo-id", "main", "--json"]) == 0
    stale_payload = json.loads(capsys.readouterr().out)
    stale_bundle = stale_payload["data"]["bundle"]
    assert stale_bundle["knowledge_results"] == []
    assert stale_bundle["completeness"]["knowledge_available_record_count"] == 1
    assert stale_bundle["completeness"]["knowledge_lifecycle"]["available_statuses"] == {"stale": 1}
    assert stale_bundle["completeness"]["knowledge_lifecycle"]["excluded_statuses"] == {"stale": 1}
    assert stale_bundle["completeness"]["knowledge_lifecycle"]["returned_statuses"] == {}
    assert any(problem["code"] == "knowledge_stale_record_excluded" for problem in stale_payload["problems"])

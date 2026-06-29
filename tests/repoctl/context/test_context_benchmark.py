from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.context_model import ContextBundle, ContextCandidate, ContextSourceRef
from tools.repoctl.graph_model import digest_data, file_id
from tests.repoctl.knowledge_test_helpers import _approve_knowledge_source
from tests.repoctl.context_test_helpers import (
    _approve_deprecated_context_knowledge,
    _approve_superseded_context_knowledge,
    _setup_context_multirepo_workspace,
    _write_context_benchmark_collection_corpus,
    _write_context_benchmark_corpus,
    _write_context_docs,
    init_repo,
    write_repometa,
    write_workspace,
)


def _setup_benchmark_workspace(tmp_path: Path, monkeypatch, *, corpus: bool = False) -> tuple[Path, Path]:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    if corpus:
        _write_context_benchmark_corpus(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    return repo, Path("tests/fixtures/context-benchmark").resolve()


def test_context_benchmark_scores_fixture(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch, corpus=True)

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "context benchmark"
    assert payload["data"]["question_count"] >= 2
    assert payload["data"]["summary"]["source_ref_integrity"] is True
    assert payload["data"]["summary"]["mean_recall_at_5"] > 0
    assert payload["data"]["summary"]["by_category"]["authority"]["mean_recall_at_5"] >= 0.85
    assert payload["data"]["summary"]["by_category"]["authority"]["mean_packed_recall"] == 1.0
    assert payload["data"]["summary"]["by_category"]["impact"]["mean_recall_at_5"] == 1.0
    assert payload["data"]["summary"]["by_category"]["reference-impact"]["mean_recall_at_5"] == 1.0
    assert payload["data"]["summary"]["by_category"]["cross-file-call-impact"]["mean_recall_at_5"] == 1.0
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

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-packed-recall", "authority=1.0", "--require-fixture-corpus", "--json"]) == 0

    authority_payload = json.loads(capsys.readouterr().out)
    authority_result = next(result for result in authority_payload["data"]["results"] if result["id"] == "Q-001")
    assert authority_result["metrics"]["packed_recall"] == 1.0
    assert len(authority_result["missing_required_from_packed"]) == 0
    assert authority_payload["problems"] == []


def test_context_benchmark_materialize_fixture_then_scores(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)

    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0

    materialize_payload = json.loads(capsys.readouterr().out)
    assert materialize_payload["command"] == "context benchmark-materialize"
    assert materialize_payload["data"]["totals"]["created"] >= 10
    assert materialize_payload["data"]["totals"]["conflict"] == 0
    assert materialize_payload["warnings"][0]["code"] == "context_benchmark_materialize_mutates_workspace"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-recall-at-5", "0.85", "--require-source-integrity", "--require-fixture-corpus", "--require-no-forbidden", "--json"]) == 0

    benchmark_payload = json.loads(capsys.readouterr().out)
    assert benchmark_payload["data"]["question_count"] == 24
    assert benchmark_payload["data"]["summary"]["mean_recall_at_5"] >= 0.85
    assert benchmark_payload["data"]["fixture_corpus"]["missing_count"] == 0
    assert benchmark_payload["problems"] == []


def test_context_benchmark_materialize_blocks_conflicts_without_force(tmp_path: Path, monkeypatch, capsys) -> None:
    repo, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)
    (repo / "utils").mkdir(parents=True)
    (repo / "utils/tokens.py").write_text("local edit\n", encoding="utf-8")

    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 1

    conflict_payload = json.loads(capsys.readouterr().out)
    assert conflict_payload["data"]["totals"]["conflict"] == 1
    assert conflict_payload["problems"][0]["code"] == "context_benchmark_corpus_materialize_conflict"
    assert (repo / "utils/tokens.py").read_text(encoding="utf-8") == "local edit\n"

    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--force", "--json"]) == 0

    force_payload = json.loads(capsys.readouterr().out)
    assert force_payload["data"]["totals"]["overwritten"] == 1
    assert "def issue_token" in (repo / "utils/tokens.py").read_text(encoding="utf-8")


def test_context_benchmark_fixture_corpus_gate_fails_when_not_applied(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--require-fixture-corpus", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["fixture_corpus"]["present"] is True
    assert payload["data"]["fixture_corpus"]["missing_count"] >= 1
    assert any(problem["code"] == "context_benchmark_corpus_file_missing" for problem in payload["problems"])
    assert any(action["label"] == "Apply the declared benchmark corpus before running this gate" for action in payload["next_actions"])
    assert any(action.get("path") == "tests/fixtures/context-benchmark/corpus.json" for action in payload["next_actions"])


def test_context_benchmark_rejects_output_outside_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch, corpus=True)
    outside = tmp_path.parent / f"{tmp_path.name}-context-benchmark.json"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", outside.as_posix(), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "context_benchmark_output_outside_workspace"
    assert not outside.exists()


def test_context_benchmark_compare_artifacts(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch, corpus=True)
    baseline = tmp_path / ".repoctl-state/context-benchmark/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-benchmark/candidate.json"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    baseline_payload = json.loads(capsys.readouterr().out)
    assert json.loads(baseline.read_text(encoding="utf-8")) == baseline_payload
    assert baseline_payload["data"]["artifact"] == {
        "path": ".repoctl-state/context-benchmark/baseline.json",
        "benchmark_digest": baseline_payload["data"]["benchmark_digest"],
    }
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
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch, corpus=True)
    baseline = tmp_path / ".repoctl-state/context-benchmark/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-benchmark/candidate.json"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    candidate.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")
    source = tmp_path / "docs/contracts/repoctl-context-contract.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged after benchmark artifact.\n", encoding="utf-8")

    assert main(["context", "benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--require-current-sources", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["gates"]["require_current_sources"] is True
    assert any(problem["code"] == "context_benchmark_artifact_source_digest_drift" for problem in payload["problems"])
    assert any(item["code"] == "context_benchmark_artifact_source_digest_drift" for item in payload["data"]["source_drift"])


def test_context_benchmark_compare_detects_missing_source_after_rename(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch, corpus=True)
    baseline = tmp_path / ".repoctl-state/context-benchmark/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-benchmark/candidate.json"

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    candidate.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")
    source = tmp_path / "docs/contracts/repoctl-context-contract.md"
    source.rename(tmp_path / "docs/contracts/repoctl-context-contract-renamed.md")

    assert main(["context", "benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--require-current-sources", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "context_benchmark_artifact_source_missing" for problem in payload["problems"])
    assert any(item["path"] == "docs/contracts/repoctl-context-contract.md" for item in payload["data"]["source_drift"])


def test_context_benchmark_scores_reviewed_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch, corpus=True)

    _approve_knowledge_source(capsys, build_args=["--kind", "decision"])

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
    assert q2["required_knowledge_found_at_5"][0]["path"] == "docs/contracts/repoctl-context-contract.md"
    assert q2["required_knowledge_found_at_5"][0]["section"] == "repoctl Context contract"
    assert q2["knowledge_score_results"][0]["has_field_breakdown"] is True
    assert "exact_claim" in q2["knowledge_score_results"][0]["score_breakdown_keys"]
    assert q2["knowledge_source_statuses"][0]["digest_matches"] is True


def test_context_benchmark_quality_gate_fails_without_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch, corpus=True)

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-knowledge-recall-at-5", "1.0", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["gates"]["min_knowledge_recall_at_5"] == 1.0
    assert payload["problems"][0]["code"] == "context_benchmark_knowledge_gate_failed"


def test_context_benchmark_quality_gate_passes_with_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)

    _approve_knowledge_source(capsys)

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-knowledge-recall-at-5", "1.0", "--require-source-integrity", "--require-knowledge-source-current", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["summary"]["mean_knowledge_recall_at_5"] == 1.0
    assert payload["data"]["summary"]["knowledge_source_status_current"] is True
    assert payload["data"]["gates"]["require_knowledge_source_current"] is True
    assert payload["problems"] == []


def test_context_benchmark_forbidden_gate_fails_on_forbidden_source(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_benchmark_workspace(tmp_path, monkeypatch)
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
    _setup_context_multirepo_workspace(tmp_path, monkeypatch)
    (tmp_path / "repos/web/app.py").write_text("def web_auth():\n    return 'web'\n", encoding="utf-8")
    (tmp_path / "repos/api/app.py").write_text("def api_auth():\n    return 'api'\n", encoding="utf-8")
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
    _setup_context_multirepo_workspace(tmp_path, monkeypatch)
    fixture = Path("tests/fixtures/context-benchmark-multirepo").resolve()
    _write_context_benchmark_collection_corpus(tmp_path, fixture)

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
    _setup_context_multirepo_workspace(tmp_path, monkeypatch)
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
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)

    _approve_knowledge_source(capsys)
    source = tmp_path / "docs/contracts/repoctl-context-contract.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged after approval.\n", encoding="utf-8")

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--require-knowledge-source-current", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["summary"]["knowledge_source_status_current"] is False
    assert payload["data"]["summary"]["knowledge_stale_record_excluded"] >= 1
    assert payload["data"]["summary"]["knowledge_superseded_record_excluded"] == 0
    assert payload["data"]["gates"]["require_knowledge_source_current"] is True
    assert any(problem["code"] == "context_benchmark_knowledge_source_stale" for problem in payload["problems"])


def test_context_benchmark_counts_superseded_knowledge_exclusion(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch, corpus=True)

    old_record_id, new_record_id = _approve_superseded_context_knowledge(capsys)

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-knowledge-recall-at-5", "contract=1.0", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    contract = payload["data"]["summary"]["by_category"]["contract"]
    assert payload["data"]["summary"]["knowledge_superseded_record_excluded"] >= 1
    assert contract["knowledge_superseded_record_excluded"] >= 1
    assert contract["mean_knowledge_recall_at_5"] == 1.0
    q2 = next(result for result in payload["data"]["results"] if result["id"] == "Q-002")
    assert q2["metrics"]["knowledge_superseded_record_excluded"] >= 1
    assert q2["required_knowledge_found_at_5"][0]["path"] == "docs/contracts/repoctl-context-contract.md"

    assert main(["knowledge", "query", "source authorities remain after context retrieval", "--repo-id", "main", "--include-superseded", "--json"]) == 0

    query_payload = json.loads(capsys.readouterr().out)
    statuses = {item["record"]["id"]: item["record"]["status"] for item in query_payload["data"]["results"]}
    assert statuses[old_record_id] == "superseded"
    assert statuses[new_record_id] == "reviewed"


def test_context_benchmark_counts_deprecated_knowledge_exclusion(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch, corpus=True)

    record_id = _approve_deprecated_context_knowledge(tmp_path, capsys)

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    contract = payload["data"]["summary"]["by_category"]["contract"]
    assert payload["data"]["summary"]["knowledge_deprecated_record_excluded"] >= 1
    assert contract["knowledge_deprecated_record_excluded"] >= 1
    assert contract["mean_knowledge_recall_at_5"] == 0.0
    q2 = next(result for result in payload["data"]["results"] if result["id"] == "Q-002")
    assert q2["metrics"]["knowledge_deprecated_record_excluded"] >= 1
    assert q2["missing_required_knowledge_at_5"][0]["path"] == "docs/contracts/repoctl-context-contract.md"

    assert main(["knowledge", "query", "source authorities remain after context retrieval", "--repo-id", "main", "--include-deprecated", "--json"]) == 0

    query_payload = json.loads(capsys.readouterr().out)
    statuses = {item["record"]["id"]: item["record"]["status"] for item in query_payload["data"]["results"]}
    assert statuses[record_id] == "deprecated"


def test_context_benchmark_category_knowledge_gate_fails_without_reviewed_record(tmp_path: Path, monkeypatch, capsys) -> None:
    _, fixture = _setup_benchmark_workspace(tmp_path, monkeypatch, corpus=True)

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-knowledge-recall-at-5", "contract=1.0", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["gates"]["min_category_knowledge_recall_at_5"] == {"contract": 1.0}
    assert payload["data"]["summary"]["by_category"]["contract"]["mean_knowledge_recall_at_5"] == 0.0
    assert payload["problems"][0]["code"] == "context_benchmark_category_knowledge_gate_failed"

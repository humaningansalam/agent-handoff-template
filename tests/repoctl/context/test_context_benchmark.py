from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.context_test_helpers import (
    _setup_context_multirepo_workspace,
    _write_context_benchmark_collection_corpus,
    _write_context_docs,
    init_repo,
    write_repometa,
    write_workspace,
)


def _setup_benchmark_workspace(tmp_path: Path, monkeypatch) -> Path:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    return Path("tests/fixtures/context-benchmark").resolve()


def test_context_benchmark_materializes_real_fixture_and_measures_retrieval_quality(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    fixture = _setup_benchmark_workspace(tmp_path, monkeypatch)

    assert main(["context", "benchmark-materialize", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0
    materialized = json.loads(capsys.readouterr().out)
    assert materialized["data"]["totals"]["created"] >= 10
    assert materialized["data"]["totals"]["conflict"] == 0

    assert main(
        [
            "context",
            "benchmark",
            "--fixture",
            fixture.as_posix(),
            "--repo-id",
            "main",
            "--min-recall-at-5",
            "0.85",
            "--require-source-integrity",
            "--require-fixture-corpus",
            "--require-no-forbidden",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    summary = payload["data"]["summary"]
    assert payload["data"]["question_count"] == 34
    assert summary["source_ref_integrity"] is True
    assert summary["mean_recall_at_5"] >= 0.85
    assert summary["by_category"]["method-impact"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["cross-file-call-impact"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["structured-compose"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["structured-workflow"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["structured-shell"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["structured-sql-seed"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["structured-sql-rpc-typescript"]["mean_graph_edge_recall"] == 1.0
    assert summary["by_category"]["integrated-owner-test"]["mean_visible_recall"] == 1.0
    assert summary["by_category"]["area-isolation"]["mean_visible_recall"] == 1.0
    assert summary["by_category"]["multi-owner-impact"]["mean_visible_recall"] == 1.0
    assert summary["by_category"]["multi-owner-impact"]["mean_graph_edge_recall"] == 1.0
    assert summary["generated_or_ignored_noise"] == 0
    assert summary["forbidden_selected"] == 0
    assert payload["problems"] == []


def test_context_benchmark_keeps_multirepo_results_isolated(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _setup_context_multirepo_workspace(tmp_path, monkeypatch)
    fixture = Path("tests/fixtures/context-benchmark-multirepo").resolve()
    _write_context_benchmark_collection_corpus(tmp_path, fixture)

    assert main(
        [
            "context",
            "benchmark",
            "--fixture",
            fixture.as_posix(),
            "--require-fixture-corpus",
            "--require-no-cross-repo",
            "--require-no-forbidden",
            "--min-category-visible-recall",
            "multi-repo-isolation=1.0",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    result = payload["data"]["results"][0]
    assert result["repo_id"] == "web"
    assert result["metrics"]["visible_recall"] == 1.0
    assert result["selected_forbidden"] == []
    assert result["cross_repo_refs"] == []
    assert payload["data"]["summary"]["cross_repo_ref_count"] == 0
    assert payload["problems"] == []

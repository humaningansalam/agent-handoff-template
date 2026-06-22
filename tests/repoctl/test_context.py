from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
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
    (root / "docs/contracts/repoctl-module-boundaries.md").write_text(
        "# repoctl module boundaries\n\n## Future layer rules\n\nContext must not replace task, Board, Backlog, Graph, or .repometa authority.\n",
        encoding="utf-8",
    )
    (root / "docs/workflows/generated.md").write_text("# Workflow\n\nGenerated output is not an authority.\n", encoding="utf-8")


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

    assert len(questions) >= 2
    for question in questions:
        assert question["id"] in expected
        assert expected[question["id"]]["required_source_refs"]

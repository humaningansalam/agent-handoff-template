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
    (root / "docs/adr/evidence-context-authority-v0.md").write_text(
        "# ADR: Evidence Context Authority v0\n\n## Decision\n\nEvidence Context comes before reviewed knowledge and keeps source bundles separate.\n\n## Authority Rules\n\nEvidence Context is read-only and non-authoritative. Context retrieval does not replace Graph, task, Board, Backlog, or .repometa authority.\n",
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


def test_context_benchmark_scores_fixture(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    fixture = Path("tests/fixtures/context-benchmark").resolve()

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "context benchmark"
    assert payload["data"]["question_count"] >= 2
    assert payload["data"]["summary"]["source_ref_integrity"] is True
    assert payload["data"]["summary"]["mean_recall_at_5"] > 0
    assert payload["data"]["summary"]["knowledge_expected_questions"] >= 1
    assert payload["data"]["summary"]["knowledge_result_questions"] == 0
    assert payload["warnings"][0]["code"] == "context_benchmark_retrieval_only"


def test_context_benchmark_scores_reviewed_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
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

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-knowledge-recall-at-5", "1.0", "--require-source-integrity", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["summary"]["mean_knowledge_recall_at_5"] == 1.0
    assert payload["problems"] == []


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

    assert main(["context", "pack", "--task", "T-20260622010101Z", "--repo-id", "main", "--budget-tokens", "1200", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    data = payload["data"]
    assert payload["command"] == "context pack"
    assert data["authoritative"] is False
    assert data["seed"]["source"] == "task_fields_for_retrieval_only"
    assert any(item["source_ref"]["path"] == "docs/adr/evidence-context-authority-v0.md" for item in data["groups"]["must_read"])
    assert data["groups"]["reviewed_knowledge"] == []
    assert data["bundle"]["budget"]["estimated_tokens"] <= 1200
    assert payload["warnings"][0]["code"] == "context_pack_not_authoritative"


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
    record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]

    assert main(["context", "pack", "--task", "T-20260622020202Z", "--repo-id", "main", "--explain", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    reviewed = payload["data"]["groups"]["reviewed_knowledge"]
    assert reviewed[0]["record"]["id"] == record_id
    assert reviewed[0]["record"]["status"] == "reviewed"
    assert reviewed[0]["explain"]["source_ref_statuses"][0]["digest_matches"] is True
    assert payload["data"]["bundle"]["query"]["explain"] is True


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
    assert all(candidate["source_ref"]["kind"] != "knowledge_record" for candidate in bundle["packed_context"])

    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")

    assert main(["context", "query", "reviewed knowledge source authority", "--repo-id", "main", "--json"]) == 0
    stale_payload = json.loads(capsys.readouterr().out)
    stale_bundle = stale_payload["data"]["bundle"]
    assert stale_bundle["knowledge_results"] == []
    assert stale_bundle["completeness"]["knowledge_available_record_count"] == 1
    assert any(problem["code"] == "knowledge_stale_record_excluded" for problem in stale_payload["problems"])

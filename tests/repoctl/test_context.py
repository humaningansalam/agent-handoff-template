from __future__ import annotations

import json
import shutil
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


def _write_pack_benchmark_task(root: Path) -> None:
    task_path = root / "docs/tasks/T-20260624020202Z--pack-benchmark.md"
    task_path.write_text(
        """---
id: T-20260624020202Z
title: "Benchmark context pack must read recall"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260624T020202Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260624020202Z - Benchmark context pack must read recall

## Context Docs

- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: evidence context authority
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Use evidence context authority for task startup.

## Handoff

- Next exact step: inspect evidence context authority.
- First file to open: `docs/adr/evidence-context-authority-v0.md`
- First command to run: `./scripts/repoctl context pack --task T-20260624020202Z --repo-id main --json`
- Done when: mandatory source refs are packed.
""",
        encoding="utf-8",
    )
    contract_task_path = root / "docs/tasks/T-20260624030303Z--pack-benchmark-contract.md"
    contract_task_path.write_text(
        """---
id: T-20260624030303Z
title: "Benchmark context pack contract recall"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260624T030303Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260624030303Z - Benchmark context pack contract recall

## Context Docs

- `docs/contracts/repoctl-module-boundaries.md`

## Discovery

- Candidate query: context authority boundaries
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Preserve repoctl module boundaries while preparing context packs.

## Handoff

- Next exact step: inspect module boundary contract.
- First file to open: `docs/contracts/repoctl-module-boundaries.md`
- First command to run: `./scripts/repoctl context pack --task T-20260624030303Z --repo-id main --json`
- Done when: contract source refs are packed.
""",
        encoding="utf-8",
    )
    graph_task_path = root / "docs/tasks/T-20260624040404Z--pack-benchmark-graph.md"
    graph_task_path.write_text(
        """---
id: T-20260624040404Z
title: "Benchmark Graph authority startup context"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260624T040404Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260624040404Z - Benchmark Graph authority startup context

## Context Docs

- `docs/adr/repoctl-graph-v0.md`

## Discovery

- Candidate query: graph derived evidence snapshot
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Preserve Graph as a read-only derived evidence snapshot.

## Handoff

- Next exact step: inspect Graph authority decision.
- First file to open: `docs/adr/repoctl-graph-v0.md`
- First command to run: `./scripts/repoctl context pack --task T-20260624040404Z --repo-id main --json`
- Done when: Graph authority source refs are packed.
""",
        encoding="utf-8",
    )
    future_layer_task_path = root / "docs/tasks/T-20260624050505Z--pack-benchmark-future-layer.md"
    future_layer_task_path.write_text(
        """---
id: T-20260624050505Z
title: "Benchmark future layer boundaries"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260624T050505Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260624050505Z - Benchmark future layer boundaries

## Context Docs

- `docs/contracts/repoctl-module-boundaries.md`
- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: future layer rules context authority
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Keep context and knowledge from replacing source authorities.

## Handoff

- Next exact step: inspect future layer rules.
- First file to open: `docs/contracts/repoctl-module-boundaries.md`
- First command to run: `./scripts/repoctl context pack --task T-20260624050505Z --repo-id main --json`
- Done when: future layer boundary refs are packed.
""",
        encoding="utf-8",
    )
    workspace_contract_task_path = root / "docs/tasks/T-20260624060606Z--pack-benchmark-workspace-contract.md"
    workspace_contract_task_path.write_text(
        """---
id: T-20260624060606Z
title: "Benchmark workspace contract startup context"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260624T060606Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260624060606Z - Benchmark workspace contract startup context

## Context Docs

- `AGENTS.md`

## Discovery

- Candidate query: workspace contract selected product repo boundary
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Follow workspace contract and selected product repository boundary.

## Handoff

- Next exact step: inspect workspace contract.
- First file to open: `AGENTS.md`
- First command to run: `./scripts/repoctl context pack --task T-20260624060606Z --repo-id main --json`
- Done when: workspace contract source refs are packed.
""",
        encoding="utf-8",
    )


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

    assert len(questions) >= 24
    assert {question["category"] for question in questions} >= {"authority", "contract", "code-location", "impact", "reference-impact", "method-impact", "import-impact"}
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

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-graph-edge-recall", "reference-impact=1.0", "--require-fixture-corpus", "--json"]) == 0

    reference_payload = json.loads(capsys.readouterr().out)
    assert reference_payload["data"]["summary"]["by_category"]["reference-impact"]["mean_graph_edge_recall"] == 1.0
    assert reference_payload["problems"] == []

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-category-packed-recall", "authority=1.0", "--require-fixture-corpus", "--json"]) == 0

    authority_payload = json.loads(capsys.readouterr().out)
    authority_result = next(result for result in authority_payload["data"]["results"] if result["id"] == "Q-001")
    assert authority_result["metrics"]["packed_recall"] == 1.0
    assert len(authority_result["missing_required_from_packed"]) == 0
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


def test_context_benchmark_materialize_fixture_then_scores(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()

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
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "utils").mkdir(parents=True)
    (repo / "utils/tokens.py").write_text("local edit\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-benchmark").resolve()

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


def test_context_multirepo_field_loop_keeps_context_and_knowledge_namespaced(tmp_path: Path, monkeypatch, capsys) -> None:
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

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "web", "--json"]) == 0
    web_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", web_candidate, "--repo-id", "web", "--json"]) == 0
    web_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "api", "--json"]) == 0
    api_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", api_candidate, "--repo-id", "api", "--json"]) == 0
    api_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--require-fixture-corpus", "--require-no-cross-repo", "--require-no-forbidden", "--min-category-packed-recall", "multi-repo-isolation=1.0", "--json"]) == 0
    benchmark_payload = json.loads(capsys.readouterr().out)
    assert benchmark_payload["data"]["summary"]["cross_repo_ref_count"] == 0

    assert main(["knowledge", "query", "context returns source bundles", "--repo-id", "web", "--json"]) == 0
    web_query = json.loads(capsys.readouterr().out)
    assert web_query["data"]["results"][0]["record"]["id"] == web_record
    assert all(item["record"]["id"] != api_record for item in web_query["data"]["results"])

    assert main(["knowledge", "render", "--repo-id", "web", "--json"]) == 0
    web_render = json.loads(capsys.readouterr().out)
    assert main(["knowledge", "render", "--repo-id", "api", "--json"]) == 0
    api_render = json.loads(capsys.readouterr().out)
    assert web_render["data"]["output"] == "docs/knowledge/generated/web"
    assert api_render["data"]["output"] == "docs/knowledge/generated/api"


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


def test_knowledge_check_reports_record_source_diagnostics(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged after approval.\n", encoding="utf-8")

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["record_checks"]["problem_codes"] == {"knowledge_source_digest_drift": 1}
    record = next(item for item in payload["data"]["records"] if item["id"] == record_id)
    assert record["status"] == "stale"
    assert record["error_count"] == 1
    assert record["problem_codes"] == {"knowledge_source_digest_drift": 1}
    source_status = record["source_statuses"][0]
    assert source_status["path"] == "docs/adr/evidence-context-authority-v0.md"
    assert source_status["exists"] is True
    assert source_status["digest_matches"] is False
    assert source_status["expected_sha256"].startswith("sha256:")
    assert source_status["actual_sha256"].startswith("sha256:")
    assert any(problem["code"] == "knowledge_source_digest_drift" for problem in payload["problems"])


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
    assert pass_payload["data"]["warning_deltas"]["missing_codes"] == []
    assert pass_payload["data"]["warning_deltas"]["added_codes"] == []
    assert "context_pack_not_authoritative" in pass_payload["data"]["warning_deltas"]["baseline_codes"]
    assert pass_payload["data"]["missing_must_read_refs"] == []
    assert pass_payload["data"]["missing_reviewed_knowledge_ids"] == []
    assert pass_payload["problems"] == []

    changed_warnings = json.loads(baseline.read_text(encoding="utf-8"))
    original_warning_code = changed_warnings["data"]["warnings"][0]["code"]
    changed_warnings["data"]["warnings"] = []
    digest_basis = {key: value for key, value in changed_warnings["data"].items() if key not in {"pack_digest", "artifact", "repository", "graph"}}
    changed_warnings["data"]["pack_digest"] = digest_data(digest_basis)
    changed_warnings["data"]["artifact"]["pack_digest"] = changed_warnings["data"]["pack_digest"]
    candidate.write_text(json.dumps(changed_warnings, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--json"]) == 0

    warning_info_payload = json.loads(capsys.readouterr().out)
    assert original_warning_code in warning_info_payload["data"]["warning_deltas"]["missing_codes"]
    assert warning_info_payload["problems"] == []

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--require-warning-stability", "--json"]) == 1

    warning_gate_payload = json.loads(capsys.readouterr().out)
    assert warning_gate_payload["data"]["gates"]["require_warning_stability"] is True
    assert any(problem["code"] == "context_pack_warning_missing" and problem["path"] == original_warning_code for problem in warning_gate_payload["problems"])

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


def test_context_pack_benchmark_scores_required_must_read_refs(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_pack_benchmark_task(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-pack-benchmark").resolve()

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-must-read-recall", "1.0", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "context pack-benchmark"
    assert payload["data"]["case_count"] == 5
    assert payload["data"]["summary"]["mean_must_read_recall"] == 1.0
    found_paths = {
        ref["path"]
        for result in payload["data"]["results"]
        for ref in result["required_must_read_found"]
    }
    assert found_paths == {
        "AGENTS.md",
        "docs/adr/evidence-context-authority-v0.md",
        "docs/adr/repoctl-graph-v0.md",
        "docs/contracts/repoctl-module-boundaries.md",
    }
    assert payload["data"]["gates"]["min_must_read_recall"] == 1.0
    assert payload["warnings"][0]["code"] == "context_pack_benchmark_retrieval_only"


def test_context_pack_benchmark_materialize_makes_shipped_fixture_runnable(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-pack-benchmark").resolve()

    assert main(["context", "pack-benchmark-materialize", "--fixture", fixture.as_posix(), "--json"]) == 0
    materialize_payload = json.loads(capsys.readouterr().out)
    assert materialize_payload["command"] == "context pack-benchmark-materialize"
    assert materialize_payload["data"]["totals"]["created"] == 5

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-must-read-recall", "1.0", "--json"]) == 0
    benchmark_payload = json.loads(capsys.readouterr().out)
    assert benchmark_payload["data"]["case_count"] == 5
    assert benchmark_payload["data"]["summary"]["mean_must_read_recall"] == 1.0


def test_release_candidate_field_gate_runner_writes_summary_artifact(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = Path(__file__).resolve().parents[2]
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    output = tmp_path / ".repoctl-state/field-gates/release-candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert payload["command"] == "field-gate run"
    assert payload["data"]["schema"] == "repoctl.field_gate.release_candidate"
    assert payload["data"]["failed_count"] == 0
    assert payload["data"]["gate_count"] == 7
    assert artifact["data"]["artifact"]["path"] == ".repoctl-state/field-gates/release-candidate.json"
    gate_names = [gate["name"] for gate in payload["data"]["gates"]]
    assert gate_names == [
        "workspace_check",
        "repository_check",
        "knowledge_check",
        "context_benchmark_materialize",
        "context_benchmark",
        "context_pack_benchmark_materialize",
        "context_pack_benchmark",
    ]
    context_summary = next(gate["summary"] for gate in payload["data"]["gates"] if gate["name"] == "context_benchmark")
    pack_summary = next(gate["summary"] for gate in payload["data"]["gates"] if gate["name"] == "context_pack_benchmark")
    assert context_summary["mean_recall_at_5"] >= 0.85
    assert pack_summary["mean_must_read_recall"] == 1.0


def test_release_candidate_field_gate_rejects_invalid_output_before_mutation(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = Path(__file__).resolve().parents[2]
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    outside = tmp_path.parent / "release-candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", outside.as_posix(), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "field_gate_output_outside_workspace"
    assert not outside.exists()
    assert not (tmp_path / "repos/auth/flow.py").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260624020202Z--pack-benchmark.md").exists()


def test_release_candidate_field_gate_fails_on_stale_reviewed_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = Path(__file__).resolve().parents[2]
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    (tmp_path / "docs/adr/evidence-context-authority-v0.md").write_text("# Drifted\n\n## Decision\n\nChanged after approval.\n", encoding="utf-8")

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    knowledge_gate = next(gate for gate in payload["data"]["gates"] if gate["name"] == "knowledge_check")
    assert knowledge_gate["ok"] is False
    assert knowledge_gate["summary"]["record_error_count"] == 1
    assert knowledge_gate["summary"]["record_problem_codes"] == {"knowledge_source_digest_drift": 1}
    assert any(problem["code"] == "knowledge_source_digest_drift" for problem in knowledge_gate["problems"])
    assert any(problem["code"] == "field_gate_failed" and problem["message"].endswith("knowledge_check") for problem in payload["problems"])


def test_knowledge_refresh_all_stale_can_create_candidate_from_stale_record(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approved_payload = json.loads(capsys.readouterr().out)
    record_id = approved_payload["data"]["record"]["id"]
    record_path = tmp_path / approved_payload["data"]["record_path"]
    original_record_text = record_path.read_text(encoding="utf-8")
    source_path = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source_path.write_text("# ADR: Evidence Context Authority v0\n\n## Decision\n\nChanged after approval and needs review.\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "refresh", "--all-stale", "--include-records", "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["refreshed_candidates"] == []
    assert payload["data"]["refreshed_records"][0]["record_id"] == record_id
    new_candidate_id = payload["data"]["refreshed_records"][0]["new_candidate_id"]
    assert new_candidate_id.startswith("KC-")
    assert record_path.read_text(encoding="utf-8") == original_record_text
    new_candidate_path = tmp_path / ".repoctl-state/knowledge/candidates/main" / f"{new_candidate_id}.json"
    new_candidate = json.loads(new_candidate_path.read_text(encoding="utf-8"))
    assert new_candidate["authoritative"] is False
    assert new_candidate["derived_from"] == {
        "kind": "knowledge_record",
        "record_id": record_id,
        "record_digest": approved_payload["data"]["record"]["record_digest"],
    }
    assert "approval should supersede the original reviewed record instead of editing it" in new_candidate["review"]["checklist"]
    assert new_candidate["source_refs"][0]["content_sha256"] != approved_payload["data"]["record"]["source_refs"][0]["content_sha256"]

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["event_checks"]["error_count"] == 0
    assert check_payload["data"]["record_checks"]["problem_codes"] == {"knowledge_source_digest_drift": 1}

    assert main(["knowledge", "candidate", "refresh", "--all-stale", "--include-records", "--repo-id", "main", "--json"]) == 0
    second_payload = json.loads(capsys.readouterr().out)
    assert second_payload["data"]["refreshed_records"] == []
    assert second_payload["data"]["skipped_records"][0] == {"record_id": record_id, "reason": "already_refreshed"}

    assert main(["knowledge", "approve", new_candidate_id, "--repo-id", "main", "--json"]) == 0
    replacement_payload = json.loads(capsys.readouterr().out)
    replacement_record_id = replacement_payload["data"]["record"]["id"]
    assert replacement_payload["data"]["record"]["supersedes"] == [record_id]
    assert replacement_payload["data"]["superseded_events"][0]["event"]["record_id"] == record_id
    assert replacement_payload["data"]["superseded_events"][0]["event"]["superseded_by"] == replacement_record_id

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 0
    recovered_check_payload = json.loads(capsys.readouterr().out)
    assert recovered_check_payload["data"]["record_checks"]["error_count"] == 0

    assert main(["knowledge", "query", "Changed after approval", "--repo-id", "main", "--include-superseded", "--json"]) == 0
    query_payload = json.loads(capsys.readouterr().out)
    statuses = {item["record"]["id"]: item["record"]["status"] for item in query_payload["data"]["results"]}
    assert statuses[record_id] == "superseded"
    assert statuses[replacement_record_id] == "reviewed"


def test_knowledge_refresh_all_stale_reports_missing_record_source(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    (tmp_path / "docs/adr/evidence-context-authority-v0.md").unlink()

    assert main(["knowledge", "candidate", "refresh", "--all-stale", "--include-records", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["refreshed_records"] == []
    assert payload["data"]["skipped_records"][0] == {
        "record_id": record_id,
        "reason": "blocked_by_non_drift_errors",
        "problem_codes": {"knowledge_source_missing": 1},
    }
    assert payload["problems"][0]["code"] == "knowledge_source_missing"


def test_release_candidate_field_gate_runner_includes_multirepo_isolation_when_configured(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_repometa(tmp_path / "repos/web")
    write_repometa(tmp_path / "repos/api")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    source_root = Path(__file__).resolve().parents[2]
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-benchmark-multirepo", tmp_path / "tests/fixtures/context-benchmark-multirepo")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "web", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    gate_names = [gate["name"] for gate in payload["data"]["gates"]]
    assert "context_benchmark" not in gate_names
    assert "context_benchmark_multirepo_materialize" in gate_names
    assert "context_benchmark_multirepo_isolation" in gate_names
    multi_summary = next(gate["summary"] for gate in payload["data"]["gates"] if gate["name"] == "context_benchmark_multirepo_isolation")
    assert multi_summary["question_count"] == 8
    assert multi_summary["cross_repo_ref_count"] == 0
    assert multi_summary["by_category"]["multi-repo-isolation"]["mean_packed_recall"] == 1.0
    assert payload["data"]["failed_count"] == 0


def test_field_gate_compare_detects_gate_regression_and_digest_tamper(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = Path(__file__).resolve().parents[2]
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    baseline = tmp_path / ".repoctl-state/field-gates/baseline.json"
    candidate = tmp_path / ".repoctl-state/field-gates/candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    assert main(["field-gate", "compare", "--baseline", baseline.as_posix(), "--candidate", baseline.as_posix(), "--max-failed-count-increase", "0", "--require-same-gates", "--require-no-gate-regressions", "--json"]) == 0
    compare_payload = json.loads(capsys.readouterr().out)
    assert compare_payload["command"] == "field-gate compare"
    assert compare_payload["data"]["failed_count_delta"]["delta"] == 0
    assert compare_payload["data"]["missing_gates"] == []
    assert compare_payload["data"]["new_gates"] == []

    regressed = json.loads(baseline.read_text(encoding="utf-8"))
    regressed["data"]["failed_count"] = 1
    regressed["data"]["passed_count"] -= 1
    regressed["data"]["gates"][-1]["ok"] = False
    regressed["data"]["gates"][-1]["problems"] = [{"severity": "error", "code": "synthetic", "message": "synthetic"}]
    regressed["data"]["run_digest"] = digest_data({key: value for key, value in regressed["data"].items() if key not in {"run_digest", "artifact"}})
    candidate.write_text(json.dumps(regressed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["field-gate", "compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-failed-count-increase", "0", "--require-same-gates", "--require-no-gate-regressions", "--json"]) == 1
    failed_payload = json.loads(capsys.readouterr().out)
    codes = [problem["code"] for problem in failed_payload["problems"]]
    assert "field_gate_failed_count_regressed" in codes
    assert "field_gate_gate_regressed" in codes

    tampered = json.loads(candidate.read_text(encoding="utf-8"))
    tampered["data"]["failed_count"] = 0
    candidate.write_text(json.dumps(tampered, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["field-gate", "compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--json"]) == 1
    tamper_payload = json.loads(capsys.readouterr().out)
    assert tamper_payload["problems"][0]["code"] == "field_gate_artifact_digest_mismatch"


def test_field_gate_compare_accepts_failed_run_artifact(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = Path(__file__).resolve().parents[2]
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    baseline = tmp_path / ".repoctl-state/field-gates/baseline.json"
    candidate = tmp_path / ".repoctl-state/field-gates/candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    assert main(["field-gate", "cleanup", "--artifact", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    (tmp_path / "docs/adr/evidence-context-authority-v0.md").write_text("# Drifted\n\n## Decision\n\nChanged after approval.\n", encoding="utf-8")

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", candidate.as_posix(), "--json"]) == 1
    failed_run_payload = json.loads(capsys.readouterr().out)
    assert failed_run_payload["ok"] is False
    assert candidate.is_file()

    assert main(["field-gate", "compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-failed-count-increase", "0", "--require-same-gates", "--require-no-gate-regressions", "--json"]) == 1
    compare_payload = json.loads(capsys.readouterr().out)
    codes = [problem["code"] for problem in compare_payload["problems"]]
    assert "field_gate_artifact_failed" not in codes
    assert "field_gate_failed_count_regressed" in codes
    assert "field_gate_gate_regressed" in codes
    assert compare_payload["data"]["failed_count_delta"]["baseline"] == 0
    assert compare_payload["data"]["failed_count_delta"]["candidate"] >= 1
    assert compare_payload["data"]["failed_count_delta"]["delta"] >= 1
    knowledge_delta = next(delta for delta in compare_payload["data"]["gate_deltas"] if delta["name"] == "knowledge_check")
    assert knowledge_delta["ok"]["regressed"] is True
    assert knowledge_delta["problem_count"]["candidate"] == 1


def test_field_gate_cleanup_removes_only_recorded_created_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = Path(__file__).resolve().parents[2]
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    artifact = tmp_path / ".repoctl-state/field-gates/release-candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", artifact.as_posix(), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    cleanup_count = sum(len(gate.get("cleanup", [])) for gate in payload["data"]["gates"])
    assert cleanup_count == 17
    assert (tmp_path / "repos/auth/flow.py").is_file()
    assert (tmp_path / "docs/archive/tasks/T-20260624020202Z--pack-benchmark.md").is_file()

    assert main(["field-gate", "cleanup", "--artifact", artifact.as_posix(), "--json"]) == 0
    cleanup_payload = json.loads(capsys.readouterr().out)
    assert cleanup_payload["data"]["removed_count"] == 17
    assert not (tmp_path / "repos/auth/flow.py").exists()
    assert not (tmp_path / "repos/auth").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260624020202Z--pack-benchmark.md").exists()
    assert (tmp_path / "docs/archive/tasks").is_dir()

    assert main(["field-gate", "cleanup", "--artifact", artifact.as_posix(), "--json"]) == 0
    second_payload = json.loads(capsys.readouterr().out)
    assert second_payload["data"]["removed_count"] == 0
    assert second_payload["data"]["skipped_count"] == 17


def test_field_gate_cleanup_refuses_changed_created_file(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = Path(__file__).resolve().parents[2]
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    artifact = tmp_path / ".repoctl-state/field-gates/release-candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", artifact.as_posix(), "--json"]) == 0
    capsys.readouterr()
    (tmp_path / "repos/auth/flow.py").write_text("user changed file\n", encoding="utf-8")

    assert main(["field-gate", "cleanup", "--artifact", artifact.as_posix(), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "field_gate_cleanup_digest_mismatch" and problem["path"] == "repos/auth/flow.py" for problem in payload["problems"])
    assert (tmp_path / "repos/auth/flow.py").read_text(encoding="utf-8") == "user changed file\n"


def test_context_pack_benchmark_writes_output_artifact(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_pack_benchmark_task(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-pack-benchmark").resolve()
    output = tmp_path / ".repoctl-state/context-pack-benchmark/result.json"

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert artifact["command"] == "context pack-benchmark"
    assert artifact["data"]["artifact"] == {
        "path": ".repoctl-state/context-pack-benchmark/result.json",
        "benchmark_digest": payload["data"]["benchmark_digest"],
    }
    assert artifact["data"]["benchmark_digest"] == payload["data"]["benchmark_digest"]


def test_context_pack_benchmark_rejects_output_outside_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_pack_benchmark_task(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-pack-benchmark").resolve()
    outside = tmp_path.parent / f"{tmp_path.name}-pack-benchmark.json"

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", outside.as_posix(), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "context_pack_benchmark_output_outside_workspace"
    assert not outside.exists()


def test_context_pack_benchmark_compare_reports_metric_deltas(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_pack_benchmark_task(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-pack-benchmark").resolve()
    baseline = tmp_path / ".repoctl-state/context-pack-benchmark/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-pack-benchmark/candidate.json"

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    candidate.parent.mkdir(parents=True, exist_ok=True)
    candidate.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")

    assert main(["context", "pack-benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-mean-must-read-recall-drop", "0", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "context pack-benchmark-compare"
    assert payload["data"]["metric_deltas"]["mean_must_read_recall"] == {"baseline": 1.0, "candidate": 1.0, "delta": 0.0}
    assert payload["data"]["case_deltas"][0]["id"] == "CP-001"
    assert payload["data"]["regressions"] == []


def test_context_pack_benchmark_compare_gates_recall_regression(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_pack_benchmark_task(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-pack-benchmark").resolve()
    baseline = tmp_path / ".repoctl-state/context-pack-benchmark/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-pack-benchmark/candidate.json"

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    candidate_payload = json.loads(baseline.read_text(encoding="utf-8"))
    candidate_data = candidate_payload["data"]
    candidate_data["summary"]["mean_must_read_recall"] = 0.0
    candidate_data["results"][0]["metrics"]["must_read_recall"] = 0.0
    candidate_data["results"][0]["required_must_read_found"] = []
    candidate_data["results"][0]["missing_required_must_read"] = [{"kind": "document", "path": "docs/adr/evidence-context-authority-v0.md", "section": "Decision"}]
    candidate_data["benchmark_digest"] = digest_data({key: value for key, value in candidate_data.items() if key not in {"benchmark_digest", "artifact"}})
    candidate.write_text(json.dumps(candidate_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-mean-must-read-recall-drop", "0.1", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["metric_deltas"]["mean_must_read_recall"] == {"baseline": 1.0, "candidate": 0.0, "delta": -1.0}
    assert payload["problems"][0]["code"] == "context_pack_benchmark_must_read_recall_regressed"


def test_context_field_loop_runs_pack_benchmark_compare_and_render_check(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_pack_benchmark_task(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = Path("tests/fixtures/context-pack-benchmark").resolve()
    baseline = tmp_path / ".repoctl-state/context-pack-benchmark/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-pack-benchmark/candidate.json"

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 0
    render_check = json.loads(capsys.readouterr().out)
    assert render_check["data"]["check"]["current"] is True

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-must-read-recall", "1.0", "--output", baseline.as_posix(), "--json"]) == 0
    baseline_payload = json.loads(capsys.readouterr().out)
    candidate.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["context", "pack-benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-mean-must-read-recall-drop", "0", "--json"]) == 0
    compare_payload = json.loads(capsys.readouterr().out)

    assert baseline_payload["data"]["summary"]["mean_must_read_recall"] == 1.0
    assert compare_payload["data"]["metric_deltas"]["mean_must_read_recall"]["delta"] == 0.0
    assert compare_payload["data"]["case_deltas"][0]["present_in_candidate"] is True


def test_context_pack_benchmark_gate_fails_missing_required_ref(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_pack_benchmark_task(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    fixture = tmp_path / "pack-fixture"
    fixture.mkdir()
    (fixture / "cases.json").write_text(
        json.dumps(
            {
                "schema": "repoctl.context.task_pack.benchmark.cases",
                "schema_version": 1,
                "cases": [
                    {
                        "id": "CP-MISSING",
                        "task_id": "T-20260624020202Z",
                        "required_must_read_refs": [{"kind": "document", "path": "docs/adr/missing.md"}],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-must-read-recall", "1.0", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["summary"]["mean_must_read_recall"] == 0.0
    assert payload["data"]["results"][0]["missing_required_must_read"][0]["path"] == "docs/adr/missing.md"
    assert payload["problems"][0]["code"] == "context_pack_benchmark_must_read_recall_failed"


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

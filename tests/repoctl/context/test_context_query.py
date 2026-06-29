from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.knowledge_test_helpers import _approve_knowledge_source
from tests.repoctl.context_test_helpers import (
    _write_completion_receipt,
    _write_context_benchmark_collection_corpus,
    _setup_context_multirepo_workspace,
    _setup_context_workspace,
    _write_context_docs,
    init_repo,
    write_repometa,
    write_workspace,
)


def test_context_query_returns_source_bundle(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "app.py").write_text("def run():\n    return 'ok'\n", encoding="utf-8")

    assert main(["context", "query", "Why is Graph non-authoritative?", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    bundle = payload["data"]["bundle"]
    assert bundle["schema"] == "repoctl.context.bundle"
    assert bundle["authoritative"] is False
    assert bundle["repository"] == {"id": "main", "path": "repos", "identity_source": "reserved"}
    assert bundle["source_snapshots"]["graph_digest"].startswith("sha256:")
    refs = [candidate["source_ref"] for candidate in bundle["packed_context"]]
    assert any(ref["path"] == "docs/contracts/repoctl-graph-contract.md" and ref.get("section") == "repoctl Graph contract" for ref in refs)
    assert all(ref["content_sha256"].startswith("sha256:") for ref in refs)
    assert payload["warnings"][0]["code"] == "context_not_authoritative"


def test_context_query_returns_actionable_groups_for_call_impact(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "auth").mkdir()
    (repo / "auth/flow.py").write_text(
        'def validate_token(token: str) -> bool:\n    return token == "ok"\n\n\ndef login(token: str) -> str:\n    if validate_token(token):\n        return "ok"\n    return "denied"\n',
        encoding="utf-8",
    )

    assert main(["context", "query", "What calls validate_token?", "--mode", "call-impact", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["query"]["mode"] == "call_impact"
    groups = bundle["groups"]
    assert any(item["source_ref"]["kind"] == "graph_query" for item in groups["callers_and_dependents"])
    assert any("login --CALLS--> validate_token" in item["excerpt"] for item in groups["callers_and_dependents"])
    assert all(item["repo_id"] == "main" for items in groups.values() for item in items)


def test_context_query_markdown_uses_same_grouped_sources(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "auth.py").write_text("def validate_token():\n    return True\n", encoding="utf-8")

    assert main(["context", "query", "Where is validate_token defined?", "--format", "markdown"]) == 0

    output = capsys.readouterr().out
    assert "# Context Bundle" in output
    assert "## Must Read" in output
    assert "## Likely Change Surface" in output
    assert "<graph-query:symbol:" in output
    assert "validate_token" in output


def test_context_query_is_deterministic(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["context", "query", "source authorities", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert main(["context", "query", "source authorities", "--json"]) == 0
    second = json.loads(capsys.readouterr().out)["data"]["bundle"]

    assert first["bundle_digest"] == second["bundle_digest"]
    assert first == second


def test_context_query_respects_budget(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["context", "query", "Graph authority", "--budget-tokens", "10", "--json"]) == 0

    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["budget"]["requested_tokens"] == 10
    assert bundle["budget"]["estimated_tokens"] <= 10


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

    assert main(["context", "benchmark", "--fixture", fixture.as_posix(), "--require-fixture-corpus", "--require-no-cross-repo", "--require-no-forbidden", "--min-category-packed-recall", "multi-repo-isolation=1.0", "--json"]) == 0
    benchmark_payload = json.loads(capsys.readouterr().out)
    assert benchmark_payload["data"]["summary"]["cross_repo_ref_count"] == 0

    assert main(["knowledge", "query", "context returns source bundles", "--repo-id", "web", "--json"]) == 0
    web_query = json.loads(capsys.readouterr().out)
    assert web_query["data"]["results"][0]["record"]["id"] == web_record
    assert all(item["record"]["id"] != api_record for item in web_query["data"]["results"])



def test_knowledge_check_reports_record_source_diagnostics(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)

    record_id = _approve_knowledge_source(capsys)["data"]["record"]["id"]
    source = tmp_path / "docs/contracts/repoctl-context-contract.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged after approval.\n", encoding="utf-8")

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["record_checks"]["problem_codes"] == {"knowledge_source_digest_drift": 1}
    record = next(item for item in payload["data"]["records"] if item["id"] == record_id)
    assert record["status"] == "stale"
    assert record["error_count"] == 1
    assert record["problem_codes"] == {"knowledge_source_digest_drift": 1}
    source_status = record["source_statuses"][0]
    assert source_status["path"] == "docs/contracts/repoctl-context-contract.md"
    assert source_status["exists"] is True
    assert source_status["digest_matches"] is False
    assert source_status["expected_sha256"].startswith("sha256:")
    assert source_status["actual_sha256"].startswith("sha256:")
    assert any(problem["code"] == "knowledge_source_digest_drift" for problem in payload["problems"])


def test_knowledge_refresh_all_stale_can_create_candidate_from_stale_record(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approved_payload = json.loads(capsys.readouterr().out)
    record_id = approved_payload["data"]["record"]["id"]
    record_path = tmp_path / approved_payload["data"]["record_path"]
    original_record_text = record_path.read_text(encoding="utf-8")
    source_path = tmp_path / "docs/contracts/repoctl-context-contract.md"
    source_path.write_text("# ADR: repoctl Context contract v0\n\n## Decision\n\nChanged after approval and needs review.\n", encoding="utf-8")

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
    repo = _setup_context_workspace(tmp_path, monkeypatch)

    record_id = _approve_knowledge_source(capsys)["data"]["record"]["id"]
    (tmp_path / "docs/contracts/repoctl-context-contract.md").unlink()

    assert main(["knowledge", "candidate", "refresh", "--all-stale", "--include-records", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["refreshed_records"] == []
    assert payload["data"]["skipped_records"][0] == {
        "record_id": record_id,
        "reason": "blocked_by_non_drift_errors",
        "problem_codes": {"knowledge_source_missing": 1},
    }
    assert payload["problems"][0]["code"] == "knowledge_source_missing"


def test_context_query_includes_reviewed_knowledge_separately(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)

    record_id = _approve_knowledge_source(capsys, build_args=["--kind", "decision"])["data"]["record"]["id"]

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

    source = tmp_path / "docs/contracts/repoctl-context-contract.md"
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


def test_knowledge_render_builds_navigable_record_target_and_search_pages(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_completion_receipt(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    note = tmp_path / "review-note.md"
    note.write_text("Reviewed receipt evidence as a file-level invariant.\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "build", "--from-receipt", "T-20260625010101Z", "--repo-id", "main", "--kind", "invariant", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--reviewed-by", "render-test", "--note-file", note.as_posix(), "--json"]) == 0
    record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0

    render_payload = json.loads(capsys.readouterr().out)
    output = tmp_path / render_payload["data"]["output"]
    record_page = output / "records" / f"{record_id}.md"
    target_page = output / "targets/files/auth.py.md"
    search_index = output / "search-index.json"
    history_page = output / "history.md"
    assert record_page.is_file()
    assert target_page.is_file()
    assert search_index.is_file()
    assert history_page.is_file()
    record_text = record_page.read_text(encoding="utf-8")
    assert "## Lifecycle" in record_text
    assert "Status: `reviewed`" in record_text
    assert "Reviewed by: `render-test`" in record_text
    assert "Review note: Reviewed receipt evidence as a file-level invariant." in record_text
    assert "Origin kind: `completion_receipt`" in record_text
    assert "verification_artifact: `docs/archive/tasks/T-20260625010101Z--knowledge-receipt.md`" in record_text
    assert "[auth.py](../targets/files/auth.py.md)" in record_text
    assert "docs/tasks/.repoctl-state/completions/T-20260625010101Z.json" in record_text
    target_text = target_page.read_text(encoding="utf-8")
    assert f"Target: auth.py" in target_text
    assert f"../../records/{record_id}.md" in target_text
    rows = json.loads(search_index.read_text(encoding="utf-8"))
    assert rows[0]["record_id"] == record_id
    assert rows[0]["applies_to"]["files"] == ["auth.py"]
    assert rows[0]["page_path"] == f"records/{record_id}.md"

    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["check"]["current"] is True
    assert check_payload["data"]["check"]["broken_links"] == []


def test_knowledge_render_check_reports_broken_links(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)

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

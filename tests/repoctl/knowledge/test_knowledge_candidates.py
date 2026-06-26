from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.knowledge_test_helpers import (
    _setup_knowledge_workspace,
    _write_knowledge_docs,
    add_task,
    init_repo,
    task_text,
    write_repometa,
    write_workspace,
)


def test_knowledge_candidate_build_list_show(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--kind", "decision", "--json"]) == 0
    build_payload = json.loads(capsys.readouterr().out)
    candidate = build_payload["data"]["candidate"]
    assert candidate["schema"] == "repoctl.knowledge.candidate"
    assert candidate["authoritative"] is False
    assert candidate["status"] == "candidate"
    assert candidate["source_refs"][0]["path"] == "docs/adr/evidence-context-authority-v0.md"
    assert candidate["source_refs"][0]["content_sha256"].startswith("sha256:")
    assert build_payload["warnings"][0]["code"] == "knowledge_candidate_not_authoritative"

    assert main(["knowledge", "candidate", "list", "--repo-id", "main", "--json"]) == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert [item["id"] for item in list_payload["data"]["candidates"]] == [candidate["id"]]
    assert list_payload["data"]["candidates"][0]["review_state"] == "pending"

    assert main(["knowledge", "candidate", "show", candidate["id"], "--repo-id", "main", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["data"]["candidate"]["candidate_digest"] == candidate["candidate_digest"]

    assert main(["knowledge", "candidate", "show", candidate["id"], "--repo-id", "main", "--format", "markdown"]) == 0
    markdown = capsys.readouterr().out
    assert "# Knowledge Candidate Review:" in markdown
    assert "## Source Refs" in markdown
    assert "docs/adr/evidence-context-authority-v0.md" in markdown
    assert "digest_matches=`True`" in markdown
    assert "## Next Commands" in markdown

    assert main(["knowledge", "candidate", "check", candidate["id"], "--repo-id", "main", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["passed"] is True
    assert check_payload["data"]["checks"]["source_refs_valid"] is True

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--kind", "decision", "--json"]) == 0
    second_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]
    assert second_candidate["id"] != candidate["id"]

    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["candidate_count"] == 2
    assert status_payload["data"]["candidate_review_states"] == {"pending": 2}
    assert status_payload["data"]["record_count"] == 0
    assert status_payload["data"]["candidate_checks"]["passed_count"] == 2
    assert status_payload["data"]["candidate_checks"]["error_count"] == 0
    assert status_payload["data"]["record_checks"]["error_count"] == 0


def test_knowledge_candidate_check_warns_on_duplicate_reviewed_claim(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    first_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", first_candidate, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    second_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]

    assert main(["knowledge", "candidate", "check", second_candidate, "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["passed"] is True
    assert payload["data"]["related_records"][0]["status"] == "reviewed"
    assert payload["data"]["related_records"][0]["relation"] == "same_claim"
    assert payload["warnings"][0]["code"] == "knowledge_candidate_duplicate_reviewed_claim"

    assert main(["knowledge", "approve", second_candidate, "--repo-id", "main", "--json"]) == 0
    approve_payload = json.loads(capsys.readouterr().out)
    assert approve_payload["data"]["record"]["created_from"]["candidate_check"]["related_records"][0]["status"] == "reviewed"
    assert approve_payload["warnings"][0]["code"] == "knowledge_candidate_duplicate_reviewed_claim"


def test_knowledge_candidate_check_reports_related_record_statuses(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    first_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", first_candidate, "--repo-id", "main", "--json"]) == 0
    old_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    second_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", second_candidate, "--repo-id", "main", "--supersedes", old_record, "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    third_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]

    assert main(["knowledge", "candidate", "check", third_candidate, "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    related = {item["record_id"]: item["status"] for item in payload["data"]["related_records"]}
    assert related[old_record] == "superseded"
    assert "reviewed" in set(related.values())

    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged after approval.\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "check", third_candidate, "--repo-id", "main", "--json"]) == 1
    stale_payload = json.loads(capsys.readouterr().out)
    assert any(item["status"] == "stale" for item in stale_payload["data"]["related_records"])


def test_knowledge_candidate_check_blocks_source_digest_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "check", candidate_id, "--repo-id", "main", "--json"]) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["problems"][0]["code"] == "knowledge_source_digest_drift"

    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 1
    approve_payload = json.loads(capsys.readouterr().out)
    assert approve_payload["problems"][0]["code"] == "knowledge_source_digest_drift"


def test_knowledge_candidate_refresh_creates_new_candidate_after_source_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--kind", "decision", "--json"]) == 0
    old_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]
    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\n## Update\n\nThe source changed after candidate creation.\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "check", old_candidate["id"], "--repo-id", "main", "--json"]) == 1
    capsys.readouterr()
    assert main(["knowledge", "candidate", "refresh", old_candidate["id"], "--repo-id", "main", "--json"]) == 0
    refresh_payload = json.loads(capsys.readouterr().out)
    new_candidate = refresh_payload["data"]["candidate"]
    assert new_candidate["id"] != old_candidate["id"]
    assert refresh_payload["data"]["refreshed_from"] == old_candidate["id"]
    assert refresh_payload["data"]["event"]["type"] == "refreshed_candidate"
    assert refresh_payload["data"]["event"]["candidate_id"] == old_candidate["id"]
    assert refresh_payload["data"]["event"]["new_candidate_id"] == new_candidate["id"]
    assert refresh_payload["warnings"][0]["code"] == "knowledge_candidate_refresh_creates_new_candidate"

    assert main(["knowledge", "candidate", "check", new_candidate["id"], "--repo-id", "main", "--json"]) == 0
    new_check = json.loads(capsys.readouterr().out)
    assert new_check["data"]["passed"] is True
    assert main(["knowledge", "candidate", "check", old_candidate["id"], "--repo-id", "main", "--json"]) == 1
    old_check = json.loads(capsys.readouterr().out)
    assert old_check["problems"][0]["code"] == "knowledge_source_digest_drift"

    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["candidate_count"] == 2
    assert status_payload["data"]["candidate_review_states"] == {"pending": 1, "refreshed": 1}
    assert status_payload["data"]["event_types"] == {"refreshed_candidate": 1}


def test_knowledge_candidate_refresh_all_stale_only_refreshes_drifted_candidates(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    (tmp_path / "docs/contracts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs/contracts/context-contract.md").write_text(
        "# Context Contract\n\n## Invariant\n\nContext bundles must keep source references resolvable.\n",
        encoding="utf-8",
    )

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--kind", "decision", "--json"]) == 0
    stale_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/context-contract.md", "--repo-id", "main", "--kind", "invariant", "--json"]) == 0
    current_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\n## Update\n\nThe decision source changed.\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "refresh", "--all-stale", "--repo-id", "main", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["candidate_count"] == 2
    assert payload["data"]["refreshed_count"] == 1
    assert payload["data"]["skipped_count"] == 1
    assert payload["data"]["refreshed"][0]["candidate_id"] == stale_candidate
    assert payload["data"]["refreshed"][0]["new_candidate_id"] != stale_candidate
    assert payload["data"]["skipped"] == [{"candidate_id": current_candidate, "reason": "not_stale"}]

    assert main(["knowledge", "candidate", "check", payload["data"]["refreshed"][0]["new_candidate_id"], "--repo-id", "main", "--json"]) == 0
    refreshed_check = json.loads(capsys.readouterr().out)
    assert refreshed_check["data"]["passed"] is True
    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["candidate_count"] == 3
    assert status_payload["data"]["candidate_review_states"] == {"pending": 2, "refreshed": 1}
    assert status_payload["data"]["event_types"] == {"refreshed_candidate": 1}

    assert main(["knowledge", "candidate", "refresh", "--all-stale", "--repo-id", "main", "--json"]) == 0
    second_payload = json.loads(capsys.readouterr().out)
    assert second_payload["data"]["refreshed_count"] == 0
    assert {"candidate_id": stale_candidate, "reason": "already_refreshed"} in second_payload["data"]["skipped"]
    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    second_status = json.loads(capsys.readouterr().out)
    assert second_status["data"]["candidate_count"] == 3
    assert second_status["data"]["candidate_review_states"] == {"pending": 2, "refreshed": 1}
    assert second_status["data"]["event_types"] == {"refreshed_candidate": 1}


def test_knowledge_candidate_bulk_checks_list_review_state(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    first_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", first_candidate, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    duplicate_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    drift_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "list", "--repo-id", "main", "--with-checks", "--json"]) == 0
    list_payload = json.loads(capsys.readouterr().out)
    checks = {item["id"]: item["check"] for item in list_payload["data"]["candidates"]}
    assert checks[duplicate_candidate]["warning_count"] >= 1
    assert checks[drift_candidate]["error_count"] >= 1

    assert main(["knowledge", "candidate", "check", "--all", "--repo-id", "main", "--json"]) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["candidate_count"] == 2
    assert check_payload["data"]["candidate_total_count"] == 3
    assert check_payload["data"]["pending_only"] is True
    assert check_payload["data"]["skipped_non_pending_count"] == 1
    assert check_payload["data"]["error_count"] >= 1
    assert check_payload["data"]["warning_count"] >= 1
    assert any(result["candidate_id"] == drift_candidate and result["problems"] for result in check_payload["data"]["results"])

    assert main(["knowledge", "candidate", "check", "--all", "--all-states", "--repo-id", "main", "--json"]) == 1
    all_states_payload = json.loads(capsys.readouterr().out)
    assert all_states_payload["data"]["candidate_count"] == 3
    assert all_states_payload["data"]["pending_only"] is False
    assert all_states_payload["data"]["skipped_non_pending_count"] == 0

    assert main(["knowledge", "check", "--repo-id", "main", "--include-candidates", "--json"]) == 1
    integrated_payload = json.loads(capsys.readouterr().out)
    assert integrated_payload["data"]["candidate_checks"]["candidate_count"] == 2
    assert integrated_payload["data"]["candidate_checks"]["candidate_total_count"] == 3
    assert any(problem["code"] == "knowledge_source_digest_drift" for problem in integrated_payload["problems"])

    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["candidate_checks"]["error_count"] >= 1
    assert status_payload["data"]["candidate_checks"]["warning_count"] >= 1
    assert status_payload["data"]["candidate_checks"]["problem_codes"]["knowledge_source_digest_drift"] >= 1
    assert status_payload["data"]["candidate_checks"]["warning_codes"]["knowledge_candidate_duplicate_reviewed_claim"] >= 1


def test_knowledge_candidate_rejects_plans_source(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/plans/private-plan.md", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_candidate_source_excluded"


def test_knowledge_candidate_rejects_generated_knowledge_source(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "candidate", "build", "--source", "docs/knowledge/generated/decisions.md", "--repo-id", "main", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_candidate_source_excluded"


def test_knowledge_candidate_ids_are_global_across_repos(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_repometa(tmp_path / "repos/web")
    write_repometa(tmp_path / "repos/api")
    (tmp_path / "docs/repoctl.json").write_text(
        json.dumps({"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "web", "--json"]) == 0
    web_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "api", "--json"]) == 0
    api_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert api_candidate != web_candidate

    assert main(["knowledge", "approve", web_candidate, "--repo-id", "web", "--json"]) == 0
    web_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    assert main(["knowledge", "approve", api_candidate, "--repo-id", "api", "--json"]) == 0
    api_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    assert api_record != web_record


def test_knowledge_candidate_builds_from_completion_receipt(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    task_body = task_text("T-20260609184046Z", status="todo").replace("State the outcome in one clear sentence.", "Document the stable receipt-backed recovery invariant.")
    add_task(tmp_path, "T-20260609184046Z--receipt-backed.md", task_body)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--receipt-backed.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("- Command: pytest tests/repoctl/test_knowledge_candidates.py\n- Result: pass\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", verification.as_posix(), "--json"]) == 0
    finish_payload = json.loads(capsys.readouterr().out)
    assert finish_payload["completion_receipt"] == "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json"

    assert main(["knowledge", "candidate", "build", "--from-receipt", "T-20260609184046Z", "--repo-id", "main", "--kind", "invariant", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    candidate = payload["data"]["candidate"]
    assert candidate["kind"] == "invariant"
    assert candidate["title"] == "Task T-20260609184046Z"
    assert candidate["authoritative"] is False
    assert candidate["derived_from"] == {
        "kind": "completion_receipt",
        "task_id": "T-20260609184046Z",
        "repo_id": "main",
        "verification_artifact": "docs/archive/tasks/T-20260609184046Z--receipt-backed.md",
        "changed_files": [],
        "related_symbols": [],
    }
    source_refs = candidate["source_refs"]
    assert source_refs[0]["kind"] == "completion_receipt"
    assert source_refs[0]["path"] == "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json"
    assert source_refs[0]["content_sha256"].startswith("sha256:")
    assert source_refs[1]["kind"] == "task_artifact"
    assert source_refs[1]["path"] == "docs/archive/tasks/T-20260609184046Z--receipt-backed.md"
    assert source_refs[1]["content_sha256"].startswith("sha256:")
    assert "pytest tests/repoctl/test_knowledge_candidates.py" in candidate["summary"]

    assert main(["knowledge", "candidate", "show", candidate["id"], "--repo-id", "main", "--format", "markdown"]) == 0
    review = capsys.readouterr().out
    assert "Kind: `completion_receipt`" in review
    assert "task_id: `T-20260609184046Z`" in review
    assert "verification_artifact: `docs/archive/tasks/T-20260609184046Z--receipt-backed.md`" in review
    assert "kind `completion_receipt`" in review
    assert "kind `task_artifact`" in review


def test_knowledge_candidate_build_requires_one_source_mode(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_candidate_source_required"

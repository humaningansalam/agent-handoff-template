from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import digest_data
from tests.repoctl.test_check import write_workspace
from tests.repoctl.test_meta_check import write_repometa
from tests.repoctl.test_repositories import init_repo
from tests.repoctl.test_task_lifecycle import add_task, task_text


def _write_knowledge_docs(root: Path) -> None:
    (root / "docs/adr").mkdir(parents=True, exist_ok=True)
    (root / "docs/plans").mkdir(parents=True, exist_ok=True)
    (root / "docs/adr/evidence-context-authority-v0.md").write_text(
        "# Evidence Context Authority\n\n## Decision\n\nContext returns source bundles but does not create authoritative knowledge.\n\n## Authority Rules\n\nReviewed knowledge requires explicit human approval.\n",
        encoding="utf-8",
    )
    (root / "docs/plans/private-plan.md").write_text("# Private Plan\n\nDo not ingest this.\n", encoding="utf-8")


def _read_event(root: Path, event_id: str) -> dict:
    return json.loads((root / "docs/knowledge/events" / f"{event_id}.json").read_text(encoding="utf-8"))


def _write_event(root: Path, event: dict) -> None:
    (root / "docs/knowledge/events" / f"{event['id']}.json").write_text(
        json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def test_knowledge_candidate_build_list_show(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

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
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

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
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

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
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

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
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

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
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    (tmp_path / "docs/contracts").mkdir(parents=True, exist_ok=True)
    (tmp_path / "docs/contracts/context-contract.md").write_text(
        "# Context Contract\n\n## Invariant\n\nContext bundles must keep source references resolvable.\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

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
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

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
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/plans/private-plan.md", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_candidate_source_excluded"


def test_knowledge_candidate_rejects_generated_knowledge_source(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "candidate", "build", "--source", "docs/knowledge/generated/decisions.md", "--repo-id", "main", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_candidate_source_excluded"


def test_knowledge_approve_show_check_and_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]

    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approve_payload = json.loads(capsys.readouterr().out)
    record = approve_payload["data"]["record"]
    assert record["schema"] == "repoctl.knowledge.record"
    assert record["status"] == "reviewed"
    assert record["authoritative"] is True
    assert record["id"].startswith("K-")
    assert record["created_from"]["candidate_check"] == {"passed": True, "warning_codes": [], "related_records": []}
    assert approve_payload["data"]["event"]["type"] == "approved"
    assert record["id"].lower().replace("--", "-") in approve_payload["data"]["event"]["id"]
    approved_event_id = approve_payload["data"]["event"]["id"]

    assert main(["knowledge", "show", record["id"], "--repo-id", "main", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["data"]["record"]["record_digest"] == record["record_digest"]

    assert main(["knowledge", "candidate", "list", "--repo-id", "main", "--json"]) == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert list_payload["data"]["candidates"][0]["review_state"] == "approved"

    assert main(["knowledge", "event", "list", "--repo-id", "main", "--candidate-id", candidate_id, "--json"]) == 0
    event_list = json.loads(capsys.readouterr().out)
    assert event_list["data"]["event_count"] == 1
    assert event_list["data"]["events"][0]["id"] == approved_event_id
    assert event_list["data"]["events"][0]["type"] == "approved"
    assert event_list["warnings"][0]["code"] == "knowledge_events_are_append_only"

    assert main(["knowledge", "event", "show", approved_event_id, "--repo-id", "main", "--json"]) == 0
    event_show = json.loads(capsys.readouterr().out)
    assert event_show["data"]["event"]["id"] == approved_event_id
    assert event_show["data"]["event"]["record_id"] == record["id"]

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["record_count"] == 1
    assert check_payload["problems"] == []

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 0
    query_payload = json.loads(capsys.readouterr().out)
    assert query_payload["data"]["result_count"] == 1
    assert query_payload["data"]["results"][0]["record"]["id"] == record["id"]
    assert query_payload["data"]["results"][0]["record"]["status"] == "reviewed"
    assert query_payload["data"]["results"][0]["record"]["approval_context"] == {
        "candidate_id": candidate_id,
        "candidate_digest": record["created_from"]["candidate_digest"],
        "warning_codes": [],
        "related_records": [],
    }
    breakdown = query_payload["data"]["results"][0]["score_breakdown"]
    assert breakdown["exact_claim"] > 0
    assert breakdown["exact_summary"] > 0
    assert breakdown["authority"] == 0.5
    assert "exact claim match" in query_payload["data"]["results"][0]["selection_reasons"]

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--explain", "--json"]) == 0
    explain_payload = json.loads(capsys.readouterr().out)
    explain = explain_payload["data"]["results"][0]["explain"]
    assert explain["status"] == "reviewed"
    assert explain["stale"] is False
    assert explain["source_ref_statuses"][0]["path"] == "docs/adr/evidence-context-authority-v0.md"
    assert explain["source_ref_statuses"][0]["exists"] is True
    assert explain["source_ref_statuses"][0]["digest_matches"] is True

    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 0
    stale_excluded = json.loads(capsys.readouterr().out)
    assert stale_excluded["data"]["result_count"] == 0
    assert stale_excluded["data"]["lifecycle"] == {
        "available_statuses": {"stale": 1},
        "excluded_statuses": {"stale": 1},
        "returned_statuses": {},
        "default_excludes": ["stale", "superseded", "deprecated"],
    }
    assert stale_excluded["warnings"][0]["code"] == "knowledge_stale_record_excluded"

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--include-stale", "--json"]) == 0
    stale_included = json.loads(capsys.readouterr().out)
    assert stale_included["data"]["results"][0]["record"]["status"] == "stale"
    assert stale_included["data"]["lifecycle"]["excluded_statuses"] == {}
    assert stale_included["data"]["lifecycle"]["returned_statuses"] == {"stale": 1}

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--include-stale", "--explain", "--json"]) == 0
    stale_explain = json.loads(capsys.readouterr().out)["data"]["results"][0]["explain"]
    assert stale_explain["status"] == "stale"
    assert stale_explain["stale"] is True
    assert stale_explain["source_ref_statuses"][0]["digest_matches"] is False

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    drift_payload = json.loads(capsys.readouterr().out)
    assert drift_payload["problems"][0]["code"] == "knowledge_source_digest_drift"
    assert drift_payload["data"]["records"][0]["status"] == "stale"

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    render_payload = json.loads(capsys.readouterr().out)
    rendered_by_path = {item["path"]: item for item in render_payload["data"]["rendered"]}
    stale_bundle = rendered_by_path["docs/knowledge/generated/decisions.md"]["source_bundle"]
    assert stale_bundle["source_status_counts"] == {"digest_mismatch": 1}
    assert stale_bundle["source_statuses"][0]["status"] == "digest_mismatch"
    stale_decisions_text = (tmp_path / "docs/knowledge/generated/decisions.md").read_text(encoding="utf-8")
    assert "- Status: `stale`" in stale_decisions_text
    assert "status=`digest_mismatch`" in stale_decisions_text

    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["record_statuses"] == {"stale": 1}
    assert status_payload["data"]["record_checks"]["error_count"] == 1
    assert status_payload["data"]["record_checks"]["problem_codes"] == {"knowledge_source_digest_drift": 1}


def test_knowledge_check_reports_event_digest_mismatch(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approved_event = json.loads(capsys.readouterr().out)["data"]["event"]
    event = _read_event(tmp_path, approved_event["id"])
    event["record_id"] = "K-20260622000000Z--missing"
    _write_event(tmp_path, event)

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["event_count"] == 1
    assert check_payload["data"]["event_checks"]["error_count"] == 1
    assert check_payload["problems"][0]["code"] == "knowledge_event_digest_mismatch"

    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["record_checks"]["problem_codes"]["knowledge_event_digest_mismatch"] == 1


def test_knowledge_check_reports_event_missing_record_with_valid_digest(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approved_event = json.loads(capsys.readouterr().out)["data"]["event"]
    event = _read_event(tmp_path, approved_event["id"])
    event["record_id"] = "K-20260622000000Z--missing"
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    _write_event(tmp_path, event)

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_event_record_missing"


def test_knowledge_check_reports_event_record_digest_mismatch(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approved_event = json.loads(capsys.readouterr().out)["data"]["event"]
    event = _read_event(tmp_path, approved_event["id"])
    event["record_digest"] = "sha256:" + "0" * 64
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    _write_event(tmp_path, event)

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_event_record_digest_mismatch"


def test_knowledge_check_reports_superseded_event_missing_replacement(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    first_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", first_candidate, "--repo-id", "main", "--json"]) == 0
    old_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    second_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", second_candidate, "--repo-id", "main", "--supersedes", old_record, "--json"]) == 0
    superseded_event = json.loads(capsys.readouterr().out)["data"]["superseded_events"][0]["event"]
    event = _read_event(tmp_path, superseded_event["id"])
    event["superseded_by"] = "K-20260622000000Z--missing"
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    _write_event(tmp_path, event)

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_event_superseded_by_missing"


def test_knowledge_query_rejects_invalid_lifecycle_events(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approved_event = json.loads(capsys.readouterr().out)["data"]["event"]
    event = _read_event(tmp_path, approved_event["id"])
    event["record_digest"] = "sha256:" + "1" * 64
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    _write_event(tmp_path, event)

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["result_count"] == 0
    assert payload["data"]["lifecycle"]["event_checks"]["error_count"] == 1
    assert payload["problems"][0]["code"] == "knowledge_event_record_digest_mismatch"


def test_knowledge_render_rejects_invalid_lifecycle_events(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approved_event = json.loads(capsys.readouterr().out)["data"]["event"]
    event = _read_event(tmp_path, approved_event["id"])
    event["record_digest"] = "sha256:" + "2" * 64
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    _write_event(tmp_path, event)
    output = tmp_path / "docs/knowledge/generated/invalid"

    assert main(["knowledge", "render", "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["rendered"] == []
    assert payload["data"]["event_checks"]["error_count"] == 1
    assert payload["problems"][0]["code"] == "knowledge_event_record_digest_mismatch"
    assert not output.exists()


def test_knowledge_query_ranks_more_specific_record_first(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    (tmp_path / "docs/adr/context-benchmark-gates.md").write_text(
        "# Context Benchmark Gates\n\n## Decision\n\nContext benchmark gates reject stale reviewed knowledge source drift before release.\n",
        encoding="utf-8",
    )
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    broad_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", broad_candidate, "--repo-id", "main", "--json"]) == 0
    broad_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/context-benchmark-gates.md", "--repo-id", "main", "--json"]) == 0
    specific_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", specific_candidate, "--repo-id", "main", "--json"]) == 0
    specific_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]

    assert main(["knowledge", "query", "context benchmark stale reviewed knowledge source drift", "--repo-id", "main", "--explain", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    result_ids = [item["record"]["id"] for item in payload["data"]["results"]]
    assert result_ids.index(specific_record) < result_ids.index(broad_record)
    first = payload["data"]["results"][0]
    second = next(item for item in payload["data"]["results"] if item["record"]["id"] == broad_record)
    assert first["record"]["id"] == specific_record
    assert first["score"] > second["score"]
    assert first["score_breakdown"]["exact_claim"] == 1.0
    assert "exact claim match" in first["selection_reasons"]


def test_knowledge_render_generated_view_is_not_context_source(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approved_event = json.loads(capsys.readouterr().out)["data"]["event"]

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    render_payload = json.loads(capsys.readouterr().out)
    assert render_payload["data"]["event_count"] == 1
    manifest_path = tmp_path / render_payload["data"]["manifest"]["path"]
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["schema"] == "repoctl.knowledge.render_manifest"
    assert manifest["render_digest"] == render_payload["data"]["render_digest"]
    assert manifest["manifest_digest"] == render_payload["data"]["manifest"]["digest"]
    assert manifest["rendered"] == render_payload["data"]["rendered"]
    rendered_paths = {item["path"] for item in render_payload["data"]["rendered"]}
    assert "docs/knowledge/generated/INDEX.md" in rendered_paths
    assert "docs/knowledge/generated/decisions.md" in rendered_paths
    rendered_by_path = {item["path"]: item for item in render_payload["data"]["rendered"]}
    decisions_bundle = rendered_by_path["docs/knowledge/generated/decisions.md"]["source_bundle"]
    assert decisions_bundle["record_ids"]
    assert decisions_bundle["source_refs"][0]["path"] == "docs/adr/evidence-context-authority-v0.md"
    assert decisions_bundle["source_statuses"] == [
        {
            "path": "docs/adr/evidence-context-authority-v0.md",
            "section": "Decision",
            "content_sha256": decisions_bundle["source_refs"][0]["content_sha256"],
            "status": "current",
        }
    ]
    assert decisions_bundle["source_status_counts"] == {"current": 1}
    assert decisions_bundle["event_ids"] == [approved_event["id"]]
    assert decisions_bundle["source_bundle_digest"].startswith("sha256:")
    index_bundle = rendered_by_path["docs/knowledge/generated/INDEX.md"]["source_bundle"]
    assert index_bundle["record_ids"] == decisions_bundle["record_ids"]
    assert index_bundle["event_ids"] == [approved_event["id"]]
    index_text = (tmp_path / "docs/knowledge/generated/INDEX.md").read_text(encoding="utf-8")
    assert "- Events: 1" in index_text
    assert "- Events digest: sha256:" in index_text
    assert "## Lifecycle" in index_text
    assert "- reviewed: 1" in index_text
    assert "- stale: 0" in index_text
    assert "- superseded: 0" in index_text
    assert "- deprecated: 0" in index_text
    assert "### Reviewed" in index_text
    decisions_text = (tmp_path / "docs/knowledge/generated/decisions.md").read_text(encoding="utf-8")
    assert "Non-authoritative generated view" in decisions_text
    assert f"- Lifecycle events: `{approved_event['id']}`" in decisions_text
    assert "status=`current`" in decisions_text
    assert "docs/adr/evidence-context-authority-v0.md#Decision" in decisions_text

    assert main(["context", "query", "Knowledge Index", "--repo-id", "main", "--json"]) == 0
    context_payload = json.loads(capsys.readouterr().out)
    refs = [candidate["source_ref"]["path"] for candidate in context_payload["data"]["bundle"]["candidates"]]
    assert all(not path.startswith("docs/knowledge/generated/") for path in refs)


def test_knowledge_render_is_deterministic(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    first_payload = json.loads(capsys.readouterr().out)
    first_files = {
        item["path"]: (tmp_path / item["path"]).read_text(encoding="utf-8")
        for item in first_payload["data"]["rendered"]
    }

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    second_payload = json.loads(capsys.readouterr().out)
    second_files = {
        item["path"]: (tmp_path / item["path"]).read_text(encoding="utf-8")
        for item in second_payload["data"]["rendered"]
    }

    assert first_payload["data"]["render_digest"] == second_payload["data"]["render_digest"]
    assert first_payload["data"]["rendered"] == second_payload["data"]["rendered"]
    assert first_files == second_files


def test_knowledge_render_removes_manifest_owned_stale_pages_only(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    first_payload = json.loads(capsys.readouterr().out)
    manifest_path = tmp_path / first_payload["data"]["manifest"]["path"]
    generated_dir = manifest_path.parent
    stale_page = generated_dir / "old-decisions.md"
    stale_page.write_text("# Old generated page\n", encoding="utf-8")
    unowned_note = generated_dir / "operator-note.md"
    unowned_note.write_text("local note\n", encoding="utf-8")

    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["rendered"].append(
        {
            "path": "docs/knowledge/generated/old-decisions.md",
            "digest": "sha256:old",
            "source_bundle": {"record_ids": [], "source_refs": [], "event_ids": []},
        }
    )
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    second_payload = json.loads(capsys.readouterr().out)
    assert second_payload["data"]["removed"] == ["docs/knowledge/generated/old-decisions.md"]
    assert not stale_page.exists()
    assert unowned_note.read_text(encoding="utf-8") == "local note\n"


def test_knowledge_render_check_detects_current_and_stale_outputs_without_writing(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()

    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 1
    missing_payload = json.loads(capsys.readouterr().out)
    assert missing_payload["data"]["mode"] == "check"
    assert missing_payload["problems"][0]["code"] == "knowledge_render_manifest_missing"
    assert not (tmp_path / "docs/knowledge/generated").exists()

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 0
    current_payload = json.loads(capsys.readouterr().out)
    assert current_payload["data"]["check"] == {
        "current": True,
        "missing_pages": [],
        "stale_pages": [],
        "unreadable_pages": [],
        "stale_owned_pages": [],
    }

    decisions_path = tmp_path / "docs/knowledge/generated/decisions.md"
    original_decisions = decisions_path.read_text(encoding="utf-8")
    (tmp_path / "docs/adr/evidence-context-authority-v0.md").write_text("# Changed\n\n## Decision\n\nChanged source.\n", encoding="utf-8")

    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 1
    stale_payload = json.loads(capsys.readouterr().out)
    problem_codes = {problem["code"] for problem in stale_payload["problems"]}
    assert "knowledge_render_manifest_stale" in problem_codes
    assert "knowledge_render_page_stale" in problem_codes
    assert stale_payload["data"]["check"]["current"] is False
    assert stale_payload["data"]["check"]["stale_pages"] == ["docs/knowledge/generated/INDEX.md", "docs/knowledge/generated/decisions.md"]
    assert decisions_path.read_text(encoding="utf-8") == original_decisions


def test_knowledge_render_check_reports_unreadable_generated_page(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    bad_page = tmp_path / "docs/knowledge/generated/invariants.md"
    bad_page.write_bytes(b"\xff\xfe\xfd")

    assert main(["knowledge", "render", "--repo-id", "main", "--check", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_render_page_unreadable"
    assert payload["data"]["check"]["unreadable_pages"] == ["docs/knowledge/generated/invariants.md"]
    assert bad_page.read_bytes() == b"\xff\xfe\xfd"


def test_knowledge_render_rejects_output_outside_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    outside = tmp_path.parent / f"{tmp_path.name}-knowledge-render"
    assert main(["knowledge", "render", "--repo-id", "main", "--output", outside.as_posix(), "--json"]) == 1
    outside_payload = json.loads(capsys.readouterr().out)
    assert outside_payload["problems"][0]["code"] == "knowledge_render_output_outside_workspace"
    assert not outside.exists()

    escape = tmp_path.parent / f"{tmp_path.name}-render-escape"
    escape.mkdir()
    symlink = tmp_path / "docs/knowledge/generated"
    symlink.parent.mkdir(parents=True, exist_ok=True)
    symlink.symlink_to(escape, target_is_directory=True)

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 1
    symlink_payload = json.loads(capsys.readouterr().out)
    assert symlink_payload["problems"][0]["code"] == "knowledge_render_output_outside_workspace"
    assert not (escape / "INDEX.md").exists()


def test_knowledge_render_rejects_context_source_output_path(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    output = tmp_path / "docs/knowledge/rendered"
    assert main(["knowledge", "render", "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_render_output_not_generated"
    assert not output.exists()

    safe_output = tmp_path / "docs/knowledge/generated/snapshot"
    assert main(["knowledge", "render", "--repo-id", "main", "--output", safe_output.as_posix(), "--json"]) == 0
    safe_payload = json.loads(capsys.readouterr().out)
    assert safe_payload["data"]["output"] == "docs/knowledge/generated/snapshot"
    assert (safe_output / "INDEX.md").is_file()


def test_knowledge_supersession_excludes_old_record_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    first_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", first_candidate, "--repo-id", "main", "--json"]) == 0
    old_record = json.loads(capsys.readouterr().out)["data"]["record"]["id"]

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    second_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", second_candidate, "--repo-id", "main", "--supersedes", old_record, "--json"]) == 0
    approve_payload = json.loads(capsys.readouterr().out)
    new_record = approve_payload["data"]["record"]["id"]
    assert approve_payload["data"]["record"]["supersedes"] == [old_record]
    assert approve_payload["data"]["superseded_events"][0]["event"]["superseded_by"] == new_record
    assert approve_payload["data"]["record"]["created_from"]["candidate_check"]["related_records"][0]["record_id"] == old_record

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    statuses = {record["id"]: record["status"] for record in check_payload["data"]["records"]}
    assert statuses[old_record] == "superseded"
    assert statuses[new_record] == "reviewed"

    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["record_statuses"] == {"reviewed": 1, "superseded": 1}
    assert status_payload["data"]["event_types"] == {"approved": 2, "superseded": 1}

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 0
    query_payload = json.loads(capsys.readouterr().out)
    returned_ids = [item["record"]["id"] for item in query_payload["data"]["results"]]
    assert old_record not in returned_ids
    assert new_record in returned_ids
    assert query_payload["data"]["lifecycle"]["available_statuses"] == {"reviewed": 1, "superseded": 1}
    assert query_payload["data"]["lifecycle"]["excluded_statuses"] == {"superseded": 1}
    assert query_payload["data"]["lifecycle"]["returned_statuses"] == {"reviewed": 1}
    assert any(warning["code"] == "knowledge_superseded_record_excluded" for warning in query_payload["warnings"])

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--include-superseded", "--json"]) == 0
    include_payload = json.loads(capsys.readouterr().out)
    include_statuses = {item["record"]["id"]: item["record"]["status"] for item in include_payload["data"]["results"]}
    assert include_statuses[old_record] == "superseded"
    assert include_statuses[new_record] == "reviewed"
    assert include_payload["data"]["lifecycle"]["excluded_statuses"] == {}
    assert include_payload["data"]["lifecycle"]["returned_statuses"] == {"reviewed": 1, "superseded": 1}
    old_query_record = next(item["record"] for item in include_payload["data"]["results"] if item["record"]["id"] == old_record)
    assert old_query_record["lifecycle_relations"]["superseded_by"] == [new_record]
    new_query_record = next(item["record"] for item in include_payload["data"]["results"] if item["record"]["id"] == new_record)
    assert new_query_record["lifecycle_relations"]["supersedes"] == [old_record]
    assert new_query_record["approval_context"]["related_records"][0]["record_id"] == old_record
    assert new_query_record["approval_context"]["related_records"][0]["status"] == "reviewed"

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--include-history", "--json"]) == 0
    history_payload = json.loads(capsys.readouterr().out)
    history_statuses = {item["record"]["id"]: item["record"]["status"] for item in history_payload["data"]["results"]}
    assert history_statuses[old_record] == "superseded"
    assert history_statuses[new_record] == "reviewed"
    assert history_payload["data"]["query"]["include_superseded"] is True

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    render_payload = json.loads(capsys.readouterr().out)
    assert render_payload["data"]["event_count"] == 3
    decisions_text = (tmp_path / "docs/knowledge/generated/decisions.md").read_text(encoding="utf-8")
    assert f"- Record: `{old_record}`" in decisions_text
    assert "- Status: `superseded`" in decisions_text
    assert f"- Superseded by: `{new_record}`" in decisions_text
    assert f"- Supersedes: `{old_record}`" in decisions_text
    assert "- Lifecycle events: `" in decisions_text
    assert f"- Approved from candidate: `{second_candidate}`" in decisions_text
    assert f"- Related at approval: `{old_record} status=reviewed relation=same_claim`" in decisions_text


def test_knowledge_reject_candidate_writes_event_only(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    reason = tmp_path / "reject.md"
    reason.write_text("Candidate is too broad for reviewed knowledge.\n", encoding="utf-8")

    assert main(["knowledge", "reject", candidate_id, "--repo-id", "main", "--reason-file", reason.as_posix(), "--json"]) == 0

    reject_payload = json.loads(capsys.readouterr().out)
    event = reject_payload["data"]["event"]
    assert event["type"] == "rejected_candidate"
    assert event["candidate_id"] == candidate_id
    assert event["reason"] == "Candidate is too broad for reviewed knowledge."
    assert Path(tmp_path / reject_payload["data"]["event_path"]).is_file()

    assert main(["knowledge", "query", "broad reviewed knowledge", "--repo-id", "main", "--json"]) == 0
    query_payload = json.loads(capsys.readouterr().out)
    assert query_payload["data"]["available_record_count"] == 0
    assert query_payload["data"]["results"] == []

    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["event_types"] == {"rejected_candidate": 1}
    assert status_payload["data"]["candidate_review_states"] == {"rejected": 1}

    assert main(["knowledge", "event", "list", "--repo-id", "main", "--type", "rejected_candidate", "--json"]) == 0
    event_payload = json.loads(capsys.readouterr().out)
    assert event_payload["data"]["event_count"] == 1
    assert event_payload["data"]["events"][0]["candidate_id"] == candidate_id


def test_knowledge_deprecate_record_writes_event_only(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    record = json.loads(capsys.readouterr().out)["data"]["record"]
    record_path = tmp_path / "docs/knowledge/records" / f"{record['id']}.json"
    record_before = record_path.read_text(encoding="utf-8")
    reason = tmp_path / "deprecated-reason.md"
    reason.write_text("Decision is no longer used but remains historical evidence.\n", encoding="utf-8")

    assert main(["knowledge", "deprecate", record["id"], "--repo-id", "main", "--reason-file", reason.as_posix(), "--json"]) == 0

    deprecate_payload = json.loads(capsys.readouterr().out)
    assert deprecate_payload["data"]["event"]["type"] == "deprecated"
    assert deprecate_payload["data"]["event"]["record_id"] == record["id"]
    assert deprecate_payload["warnings"][0]["code"] == "knowledge_deprecation_is_append_only"
    assert record_path.read_text(encoding="utf-8") == record_before

    assert main(["knowledge", "deprecate", record["id"], "--repo-id", "main", "--reason-file", reason.as_posix(), "--json"]) == 1
    duplicate_payload = json.loads(capsys.readouterr().out)
    assert duplicate_payload["problems"][0]["code"] == "knowledge_record_already_deprecated"

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["records"][0]["status"] == "deprecated"

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 0
    default_query = json.loads(capsys.readouterr().out)
    assert default_query["data"]["result_count"] == 0
    assert default_query["data"]["lifecycle"]["excluded_statuses"] == {"deprecated": 1}
    assert default_query["warnings"][0]["code"] == "knowledge_deprecated_record_excluded"

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--include-deprecated", "--explain", "--json"]) == 0
    include_query = json.loads(capsys.readouterr().out)
    assert include_query["data"]["results"][0]["record"]["status"] == "deprecated"
    assert include_query["data"]["results"][0]["record"]["lifecycle_relations"]["deprecated_by"] == [deprecate_payload["data"]["event"]["id"]]
    assert include_query["data"]["results"][0]["explain"]["deprecated"] is True
    assert include_query["data"]["lifecycle"]["returned_statuses"] == {"deprecated": 1}

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--include-history", "--json"]) == 0
    history_query = json.loads(capsys.readouterr().out)
    assert history_query["data"]["results"][0]["record"]["status"] == "deprecated"
    assert history_query["data"]["query"]["include_deprecated"] is True

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    index_text = (tmp_path / "docs/knowledge/generated/INDEX.md").read_text(encoding="utf-8")
    assert "- reviewed: 0" in index_text
    assert "- deprecated: 1" in index_text
    assert "### Deprecated" in index_text
    decisions_text = (tmp_path / "docs/knowledge/generated/decisions.md").read_text(encoding="utf-8")
    assert "- Status: `deprecated`" in decisions_text

    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["record_statuses"] == {"deprecated": 1}
    assert status_payload["data"]["event_types"] == {"approved": 1, "deprecated": 1}


def test_knowledge_event_show_enforces_repo_namespace(tmp_path: Path, monkeypatch, capsys) -> None:
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
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "web", "--json"]) == 0
    event_id = json.loads(capsys.readouterr().out)["data"]["event"]["id"]

    assert main(["knowledge", "event", "list", "--repo-id", "api", "--json"]) == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert list_payload["data"]["event_count"] == 0

    assert main(["knowledge", "event", "show", event_id, "--repo-id", "api", "--json"]) == 1
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["problems"][0]["code"] == "knowledge_event_repo_mismatch"


def test_knowledge_record_show_enforces_repo_namespace(tmp_path: Path, monkeypatch, capsys) -> None:
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
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "web", "--json"]) == 0
    record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]

    assert main(["knowledge", "show", record_id, "--repo-id", "web", "--json"]) == 0
    web_payload = json.loads(capsys.readouterr().out)
    assert web_payload["data"]["record"]["repo_id"] == "web"

    assert main(["knowledge", "show", record_id, "--repo-id", "api", "--json"]) == 1
    api_payload = json.loads(capsys.readouterr().out)
    assert api_payload["problems"][0]["code"] == "knowledge_record_repo_mismatch"


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
    assert candidate["derived_from"] == {"kind": "completion_receipt", "task_id": "T-20260609184046Z"}
    source_refs = candidate["source_refs"]
    assert source_refs[0]["kind"] == "completion_receipt"
    assert source_refs[0]["path"] == "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json"
    assert source_refs[0]["content_sha256"].startswith("sha256:")
    assert source_refs[1]["kind"] == "task_artifact"
    assert source_refs[1]["path"] == "docs/archive/tasks/T-20260609184046Z--receipt-backed.md"
    assert source_refs[1]["content_sha256"].startswith("sha256:")
    assert "pytest tests/repoctl/test_knowledge_candidates.py" in candidate["summary"]


def test_knowledge_candidate_builds_from_context_pack_authority_source(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    task_path = tmp_path / "docs/tasks/T-20260622070707Z--pack-backed.md"
    task_path.write_text(
        """---
id: T-20260622070707Z
title: "Promote context pack authority"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260622T070707Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260622070707Z - Promote context pack authority

## Context Docs

- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: Evidence Context authority
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Create a candidate from a context pack without making the pack an authority source.

## Handoff

- Next exact step: inspect the candidate.
- First file to open: `docs/adr/evidence-context-authority-v0.md`
- First command to run: `./scripts/repoctl knowledge candidate build --from-pack .repoctl-state/context-pack/T-20260622070707Z.json --repo-id main --json`
- Done when: candidate source refs point at authority docs.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    pack = tmp_path / ".repoctl-state/context-pack/T-20260622070707Z.json"

    assert main(["context", "pack", "--task", "T-20260622070707Z", "--repo-id", "main", "--output", pack.as_posix(), "--json"]) == 0
    pack_payload = json.loads(capsys.readouterr().out)

    assert main(["knowledge", "candidate", "build", "--from-pack", pack.as_posix(), "--repo-id", "main", "--kind", "decision", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    candidate = payload["data"]["candidate"]
    assert candidate["authoritative"] is False
    assert candidate["source_refs"][0]["path"] == "docs/adr/evidence-context-authority-v0.md"
    assert candidate["source_refs"][0]["content_sha256"].startswith("sha256:")
    assert candidate["derived_from"] == {
        "kind": "context_pack",
        "path": ".repoctl-state/context-pack/T-20260622070707Z.json",
        "pack_digest": pack_payload["data"]["pack_digest"],
    }
    assert "context pack was used only to select authority source refs" in candidate["review"]["checklist"]
    assert all(not ref["path"].startswith(".repoctl-state/") for ref in candidate["source_refs"])

    assert main(["knowledge", "candidate", "check", candidate["id"], "--repo-id", "main", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["checks"]["pack_provenance_current"] is True
    assert check_payload["warnings"] == []

    failed_pack_payload = json.loads(pack.read_text(encoding="utf-8"))
    failed_pack_payload["ok"] = False
    failed_pack_payload["problems"] = [{"severity": "error", "code": "synthetic_failure", "message": "failed"}]
    failed_pack = tmp_path / ".repoctl-state/context-pack/failed.json"
    failed_pack.write_text(json.dumps(failed_pack_payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "build", "--from-pack", failed_pack.as_posix(), "--repo-id", "main", "--kind", "decision", "--json"]) == 1
    failed_payload = json.loads(capsys.readouterr().out)
    assert failed_payload["problems"][0]["code"] == "knowledge_candidate_pack_failed"

    pack.unlink()
    assert main(["knowledge", "candidate", "check", candidate["id"], "--repo-id", "main", "--json"]) == 0
    missing_pack_payload = json.loads(capsys.readouterr().out)
    assert missing_pack_payload["ok"] is True
    assert missing_pack_payload["data"]["checks"]["source_refs_valid"] is True
    assert missing_pack_payload["data"]["checks"]["pack_provenance_current"] is False
    assert missing_pack_payload["warnings"][0]["code"] == "knowledge_candidate_pack_provenance_missing"

    assert main(["knowledge", "approve", candidate["id"], "--repo-id", "main", "--json"]) == 0
    approve_payload = json.loads(capsys.readouterr().out)
    assert approve_payload["warnings"][0]["code"] == "knowledge_candidate_pack_provenance_missing"
    assert approve_payload["data"]["record"]["created_from"]["candidate_check"]["warning_codes"] == ["knowledge_candidate_pack_provenance_missing"]

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    decisions_text = (tmp_path / "docs/knowledge/generated/decisions.md").read_text(encoding="utf-8")
    assert f"- Approved from candidate: `{candidate['id']}`" in decisions_text
    assert "- Candidate warnings: `knowledge_candidate_pack_provenance_missing`" in decisions_text


def test_knowledge_candidate_from_context_pack_rejects_drift_and_generated_pack(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_knowledge_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    task_path = tmp_path / "docs/tasks/T-20260622080808Z--pack-drift.md"
    task_path.write_text(
        """---
id: T-20260622080808Z
title: "Reject stale context pack"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260622T080808Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260622080808Z - Reject stale context pack

## Context Docs

- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: Evidence Context authority
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Reject stale context pack inputs.

## Handoff

- Next exact step: inspect drift rejection.
- First file to open: `docs/adr/evidence-context-authority-v0.md`
- First command to run: `./scripts/repoctl knowledge candidate build --from-pack .repoctl-state/context-pack/T-20260622080808Z.json --repo-id main --json`
- Done when: stale pack source refs are rejected.
""",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    pack = tmp_path / ".repoctl-state/context-pack/T-20260622080808Z.json"

    assert main(["context", "pack", "--task", "T-20260622080808Z", "--repo-id", "main", "--output", pack.as_posix(), "--json"]) == 0
    capsys.readouterr()
    outside_pack = tmp_path.parent / f"{tmp_path.name}-outside-pack.json"
    outside_pack.write_text(pack.read_text(encoding="utf-8"), encoding="utf-8")
    assert main(["knowledge", "candidate", "build", "--from-pack", outside_pack.as_posix(), "--repo-id", "main", "--json"]) == 1
    outside_payload = json.loads(capsys.readouterr().out)
    assert outside_payload["problems"][0]["code"] == "knowledge_candidate_pack_outside_workspace"

    generated_pack = tmp_path / "docs/knowledge/generated/pack.json"
    generated_pack.parent.mkdir(parents=True, exist_ok=True)
    generated_pack.write_text(pack.read_text(encoding="utf-8"), encoding="utf-8")

    assert main(["knowledge", "candidate", "build", "--from-pack", generated_pack.as_posix(), "--repo-id", "main", "--json"]) == 1
    generated_payload = json.loads(capsys.readouterr().out)
    assert generated_payload["problems"][0]["code"] == "knowledge_candidate_pack_generated"

    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged after pack.\n", encoding="utf-8")

    assert main(["knowledge", "candidate", "build", "--from-pack", pack.as_posix(), "--repo-id", "main", "--json"]) == 1
    drift_payload = json.loads(capsys.readouterr().out)
    assert drift_payload["problems"][0]["code"] == "knowledge_candidate_pack_source_drift"
    assert drift_payload["problems"][0]["path"] == "docs/adr/evidence-context-authority-v0.md"


def test_knowledge_candidate_build_requires_one_source_mode(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_candidate_source_required"

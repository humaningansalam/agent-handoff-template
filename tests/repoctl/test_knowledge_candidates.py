from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
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
    assert status_payload["data"]["record_count"] == 0


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
    assert payload["warnings"][0]["code"] == "knowledge_candidate_duplicate_reviewed_claim"


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
    assert status_payload["data"]["event_types"] == {"refreshed_candidate": 1}


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
    assert check_payload["data"]["candidate_count"] == 3
    assert check_payload["data"]["error_count"] >= 1
    assert check_payload["data"]["warning_count"] >= 1
    assert any(result["candidate_id"] == drift_candidate and result["problems"] for result in check_payload["data"]["results"])

    assert main(["knowledge", "check", "--repo-id", "main", "--include-candidates", "--json"]) == 1
    integrated_payload = json.loads(capsys.readouterr().out)
    assert integrated_payload["data"]["candidate_checks"]["candidate_count"] == 3
    assert any(problem["code"] == "knowledge_source_digest_drift" for problem in integrated_payload["problems"])


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
    assert record["created_from"]["candidate_check"] == {"passed": True, "warning_codes": []}
    assert approve_payload["data"]["event"]["type"] == "approved"
    assert record["id"].lower().replace("--", "-") in approve_payload["data"]["event"]["id"]

    assert main(["knowledge", "show", record["id"], "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["data"]["record"]["record_digest"] == record["record_digest"]

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["record_count"] == 1
    assert check_payload["problems"] == []

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 0
    query_payload = json.loads(capsys.readouterr().out)
    assert query_payload["data"]["result_count"] == 1
    assert query_payload["data"]["results"][0]["record"]["id"] == record["id"]
    assert query_payload["data"]["results"][0]["record"]["status"] == "reviewed"

    source = tmp_path / "docs/adr/evidence-context-authority-v0.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged.\n", encoding="utf-8")

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 0
    stale_excluded = json.loads(capsys.readouterr().out)
    assert stale_excluded["data"]["result_count"] == 0
    assert stale_excluded["warnings"][0]["code"] == "knowledge_stale_record_excluded"

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--include-stale", "--json"]) == 0
    stale_included = json.loads(capsys.readouterr().out)
    assert stale_included["data"]["results"][0]["record"]["status"] == "stale"

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    drift_payload = json.loads(capsys.readouterr().out)
    assert drift_payload["problems"][0]["code"] == "knowledge_source_digest_drift"
    assert drift_payload["data"]["records"][0]["status"] == "stale"


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
    capsys.readouterr()

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    render_payload = json.loads(capsys.readouterr().out)
    rendered_paths = {item["path"] for item in render_payload["data"]["rendered"]}
    assert "docs/knowledge/generated/INDEX.md" in rendered_paths
    assert "docs/knowledge/generated/decisions.md" in rendered_paths
    decisions_text = (tmp_path / "docs/knowledge/generated/decisions.md").read_text(encoding="utf-8")
    assert "Non-authoritative generated view" in decisions_text
    assert "docs/adr/evidence-context-authority-v0.md#Decision" in decisions_text

    assert main(["context", "query", "Knowledge Index", "--repo-id", "main", "--json"]) == 0
    context_payload = json.loads(capsys.readouterr().out)
    refs = [candidate["source_ref"]["path"] for candidate in context_payload["data"]["bundle"]["candidates"]]
    assert all(not path.startswith("docs/knowledge/generated/") for path in refs)


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
    assert any(warning["code"] == "knowledge_superseded_record_excluded" for warning in query_payload["warnings"])

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--include-superseded", "--json"]) == 0
    include_payload = json.loads(capsys.readouterr().out)
    include_statuses = {item["record"]["id"]: item["record"]["status"] for item in include_payload["data"]["results"]}
    assert include_statuses[old_record] == "superseded"
    assert include_statuses[new_record] == "reviewed"

    assert main(["knowledge", "render", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    decisions_text = (tmp_path / "docs/knowledge/generated/decisions.md").read_text(encoding="utf-8")
    assert f"- Record: `{old_record}`" in decisions_text
    assert "- Status: `superseded`" in decisions_text


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


def test_knowledge_candidate_build_requires_one_source_mode(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_candidate_source_required"

from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import digest_data
from tests.repoctl.knowledge_test_helpers import (
    _approve_knowledge_source,
    _read_event,
    _setup_knowledge_multirepo_workspace,
    _setup_knowledge_workspace,
    _write_event,
    _write_knowledge_docs,
    init_repo,
    write_repometa,
    write_workspace,
)


def test_knowledge_approve_show_check_and_drift(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    note = tmp_path / "review-note.md"
    note.write_text("Reviewed source refs and approved as reusable project decision.\n", encoding="utf-8")

    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--reviewed-by", "codex-field-test", "--note-file", note.as_posix(), "--json"]) == 0
    approve_payload = json.loads(capsys.readouterr().out)
    record = approve_payload["data"]["record"]
    assert record["schema"] == "repoctl.knowledge.record"
    assert record["status"] == "reviewed"
    assert record["authoritative"] is True
    assert record["review"]["reviewed_by"] == "codex-field-test"
    assert record["review"]["review_note"] == "Reviewed source refs and approved as reusable project decision."
    assert record["review"]["source_digest_set"] == [record["source_refs"][0]["content_sha256"]]
    assert record["id"].startswith("K-")
    assert record["created_from"]["candidate_check"] == {"passed": True, "warning_codes": [], "related_records": []}
    assert approve_payload["data"]["event"]["type"] == "approved"
    assert approve_payload["data"]["event"]["reviewed_by"] == "codex-field-test"
    assert approve_payload["data"]["event"]["review_note"] == "Reviewed source refs and approved as reusable project decision."
    assert approve_payload["data"]["event"]["source_digest_set"] == record["review"]["source_digest_set"]
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
    assert explain["source_ref_statuses"][0]["path"] == "docs/contracts/repoctl-context-contract.md"
    assert explain["source_ref_statuses"][0]["exists"] is True
    assert explain["source_ref_statuses"][0]["digest_matches"] is True

    source = tmp_path / "docs/contracts/repoctl-context-contract.md"
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
    assert f"records/{record['id']}.md" in stale_decisions_text
    stale_record_text = (tmp_path / "docs/knowledge/generated/records" / f"{record['id']}.md").read_text(encoding="utf-8")
    assert "- Status: `stale`" in stale_record_text
    assert "status=`digest_mismatch`" in stale_record_text

    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["record_statuses"] == {"stale": 1}
    assert status_payload["data"]["record_checks"]["error_count"] == 1
    assert status_payload["data"]["record_checks"]["problem_codes"] == {"knowledge_source_digest_drift": 1}


def test_knowledge_check_reports_event_digest_mismatch(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    approve_payload = _approve_knowledge_source(capsys)
    approved_event = approve_payload["data"]["event"]
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
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    approve_payload = _approve_knowledge_source(capsys)
    approved_event = approve_payload["data"]["event"]
    event = _read_event(tmp_path, approved_event["id"])
    event["record_id"] = "K-20260622000000Z--missing"
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    _write_event(tmp_path, event)

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_event_record_missing"


def test_knowledge_check_reports_event_record_digest_mismatch(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    approve_payload = _approve_knowledge_source(capsys)
    approved_event = approve_payload["data"]["event"]
    event = _read_event(tmp_path, approved_event["id"])
    event["record_digest"] = "sha256:" + "0" * 64
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    _write_event(tmp_path, event)

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_event_record_digest_mismatch"


def test_knowledge_check_reports_superseded_event_missing_replacement(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    old_record = _approve_knowledge_source(capsys)["data"]["record"]["id"]
    superseded_event = _approve_knowledge_source(capsys, approve_args=["--supersedes", old_record])["data"]["superseded_events"][0]["event"]
    event = _read_event(tmp_path, superseded_event["id"])
    event["superseded_by"] = "K-20260622000000Z--missing"
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    _write_event(tmp_path, event)

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_event_superseded_by_missing"


def test_knowledge_query_rejects_invalid_lifecycle_events(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    approved_event = _approve_knowledge_source(capsys)["data"]["event"]
    event = _read_event(tmp_path, approved_event["id"])
    event["record_digest"] = "sha256:" + "1" * 64
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    _write_event(tmp_path, event)

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["result_count"] == 0
    assert payload["data"]["lifecycle"]["event_checks"]["error_count"] == 1
    assert payload["problems"][0]["code"] == "knowledge_event_record_digest_mismatch"


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

    broad_record = _approve_knowledge_source(capsys)["data"]["record"]["id"]

    specific_record = _approve_knowledge_source(capsys, source="docs/adr/context-benchmark-gates.md")["data"]["record"]["id"]

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


def test_knowledge_supersession_excludes_old_record_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    old_record = _approve_knowledge_source(capsys)["data"]["record"]["id"]

    approve_payload = _approve_knowledge_source(capsys, approve_args=["--supersedes", old_record])
    second_candidate = approve_payload["candidate_id"]
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
    assert f"records/{old_record}.md" in decisions_text
    assert f"records/{new_record}.md" in decisions_text
    old_record_text = (tmp_path / "docs/knowledge/generated/records" / f"{old_record}.md").read_text(encoding="utf-8")
    new_record_text = (tmp_path / "docs/knowledge/generated/records" / f"{new_record}.md").read_text(encoding="utf-8")
    assert f"- Record: `{old_record}`" in old_record_text
    assert "- Status: `superseded`" in old_record_text
    assert f"- Superseded by: [{new_record}]" in old_record_text
    assert f"- Supersedes: [{old_record}]" in new_record_text
    assert "- Lifecycle events: `" in old_record_text
    assert f"- Approved from candidate: `{second_candidate}`" in new_record_text
    assert f"- Related at approval: `{old_record} status=reviewed relation=same_claim`" in new_record_text


def test_knowledge_reject_candidate_writes_event_only(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
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
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    record = _approve_knowledge_source(capsys)["data"]["record"]
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
    assert f"records/{record['id']}.md" in decisions_text
    record_text = (tmp_path / "docs/knowledge/generated/records" / f"{record['id']}.md").read_text(encoding="utf-8")
    assert "- Status: `deprecated`" in record_text

    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["record_statuses"] == {"deprecated": 1}
    assert status_payload["data"]["event_types"] == {"approved": 1, "deprecated": 1}


def test_knowledge_event_show_enforces_repo_namespace(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_multirepo_workspace(tmp_path, monkeypatch)

    event_id = _approve_knowledge_source(capsys, repo_id="web")["data"]["event"]["id"]

    assert main(["knowledge", "event", "list", "--repo-id", "api", "--json"]) == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert list_payload["data"]["event_count"] == 0

    assert main(["knowledge", "event", "show", event_id, "--repo-id", "api", "--json"]) == 1
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["problems"][0]["code"] == "knowledge_event_repo_mismatch"


def test_knowledge_record_show_enforces_repo_namespace(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_multirepo_workspace(tmp_path, monkeypatch)

    record_id = _approve_knowledge_source(capsys, repo_id="web")["data"]["record"]["id"]

    assert main(["knowledge", "show", record_id, "--repo-id", "web", "--json"]) == 0
    web_payload = json.loads(capsys.readouterr().out)
    assert web_payload["data"]["record"]["repo_id"] == "web"

    assert main(["knowledge", "show", record_id, "--repo-id", "api", "--json"]) == 1
    api_payload = json.loads(capsys.readouterr().out)
    assert api_payload["problems"][0]["code"] == "knowledge_record_repo_mismatch"

from __future__ import annotations

import json
from pathlib import Path

import pytest

import tools.repoctl.knowledge_candidates as knowledge_candidates
from tools.repoctl.cli import main
from tools.repoctl.graph_model import digest_data
from tools.repoctl.knowledge_candidates import knowledge_records_for_graph
from tools.repoctl.knowledge_projection import (
    initialize_empty_knowledge_projection,
    knowledge_projection_path,
)
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

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--claim", "Reviewed Context remains non-authoritative.", "--json"]) == 0
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
    assert "summary" not in record
    assert record["created_from"]["candidate_check"] == {"passed": True, "warning_codes": [], "related_records": []}
    assert approve_payload["data"]["event"]["type"] == "approved"
    assert approve_payload["data"]["event"]["reviewed_by"] == "codex-field-test"
    assert approve_payload["data"]["event"]["review_note"] == "Reviewed source refs and approved as reusable project decision."
    assert approve_payload["data"]["event"]["source_digest_set"] == record["review"]["source_digest_set"]
    assert record["id"].lower().replace("--", "-") in approve_payload["data"]["event"]["id"]
    assert len(json.dumps(approve_payload, ensure_ascii=False)) < 10_000
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
    assert "summary" not in query_payload["data"]["results"][0]["record"]
    breakdown = query_payload["data"]["results"][0]["score_breakdown"]
    assert breakdown["exact_claim"] > 0
    assert breakdown["exact_summary"] > 0
    assert breakdown["authority"] == 0.5
    assert "exact claim match" in query_payload["data"]["results"][0]["selection_reasons"]

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--full", "--json"]) == 0
    full_query_payload = json.loads(capsys.readouterr().out)
    assert full_query_payload["data"]["results"][0]["record"]["summary"] == show_payload["data"]["record"]["summary"]

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

    assert main(["knowledge", "render", "--repo-id", "main", "--full", "--json"]) == 0
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


def test_knowledge_rebuild_recovers_missing_projection_before_new_approval(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    first_record = _approve_knowledge_source(capsys)["data"]["record"]

    assert main(
        [
            "knowledge",
            "candidate",
            "build",
            "--source",
            "docs/contracts/repoctl-context-contract.md",
            "--repo-id",
            "main",
            "--claim",
            "A second reviewed decision must preserve prior durable Knowledge.",
            "--json",
        ]
    ) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    projection_path = knowledge_projection_path(tmp_path, repo_id="main")
    projection_path.unlink()

    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 1
    missing = json.loads(capsys.readouterr().out)
    assert missing["problems"][0]["code"] == "knowledge_projection_unavailable"
    assert missing["problems"][0]["cause_code"] == "missing"
    rebuild_action = next(action for action in missing["next_actions"] if action.get("kind") == "knowledge_rebuild")
    assert rebuild_action["command"] == "./scripts/repoctl knowledge rebuild --repo-id main --json"
    assert not projection_path.exists()

    empty, empty_problems = initialize_empty_knowledge_projection(tmp_path, repo_id="main")
    assert empty["heads"] == []
    assert empty_problems == []
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 1
    empty_projection = json.loads(capsys.readouterr().out)
    assert empty_projection["problems"][0]["cause_code"] == "cold_record_count_mismatch"

    assert main(["knowledge", "rebuild", "--repo-id", "main", "--json"]) == 0
    rebuilt = json.loads(capsys.readouterr().out)
    assert rebuilt["data"]["head_count"] == 1
    assert rebuilt["data"]["checkpoint"]["kind"] == "full_rebuild"
    assert rebuilt["warnings"][0]["code"] == "knowledge_projection_rebuild_scanned_cold_history"

    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    approved = json.loads(capsys.readouterr().out)
    assert approved["data"]["record"]["id"] != first_record["id"]


@pytest.mark.parametrize(
    ("history_case", "expected_code"),
    [
        ("duplicate_deprecation", "knowledge_deprecated_event_duplicate"),
        ("superseded_and_deprecated", "knowledge_lifecycle_status_conflict"),
    ],
)
def test_knowledge_rebuild_rejects_ambiguous_cold_lifecycle_before_projection_write(
    tmp_path: Path,
    monkeypatch,
    capsys,
    history_case: str,
    expected_code: str,
) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    record = _approve_knowledge_source(capsys)["data"]["record"]

    if history_case == "duplicate_deprecation":
        reason = tmp_path / "deprecated-reason.md"
        reason.write_text("Decision is no longer active.\n", encoding="utf-8")
        assert main(
            [
                "knowledge",
                "deprecate",
                record["id"],
                "--repo-id",
                "main",
                "--reason-file",
                reason.as_posix(),
                "--json",
            ]
        ) == 0
        event = json.loads(capsys.readouterr().out)["data"]["event"]
        event["id"] = f"{event['id']}-2"
    else:
        _approve_knowledge_source(
            capsys,
            claim="Replacement reviewed routing decision.",
            approve_args=["--supersedes", record["id"]],
        )
        event = {
            "schema": "repoctl.knowledge.event",
            "schema_version": 1,
            "id": "E-20260815000000Z--deprecated-superseded-record",
            "type": "deprecated",
            "repo_id": "main",
            "record_id": record["id"],
            "record_digest": record["record_digest"],
            "reason": "Conflicting cold-history lifecycle state.",
        }
    event["event_digest"] = digest_data(
        {key: value for key, value in event.items() if key != "event_digest"}
    )
    _write_event(tmp_path, event)

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert expected_code in {
        problem["code"] for problem in check_payload["problems"]
    }

    assert main(
        [
            "upgrade",
            "postflight",
            "--workspace-root",
            tmp_path.as_posix(),
            "--json",
        ]
    ) == 1
    postflight_payload = json.loads(capsys.readouterr().out)
    assert expected_code in {
        problem["code"] for problem in postflight_payload["problems"]
    }

    projection_path = knowledge_projection_path(tmp_path, repo_id="main")
    projection_before = projection_path.read_bytes()

    assert main(["knowledge", "rebuild", "--repo-id", "main", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)

    assert [problem["code"] for problem in payload["problems"]] == [expected_code]
    assert projection_path.read_bytes() == projection_before


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








def test_knowledge_consumers_reject_record_without_approval_event(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    approve_payload = _approve_knowledge_source(capsys)
    event_path = tmp_path / approve_payload["data"]["event_path"]
    event_path.unlink()

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 1
    query_payload = json.loads(capsys.readouterr().out)
    assert query_payload["data"]["result_count"] == 0
    assert query_payload["problems"][0]["code"] == "knowledge_approval_incomplete"

    graph_records, graph_problems = knowledge_records_for_graph(tmp_path, repo_id="main")
    assert graph_records == []
    assert [problem.code for problem in graph_problems] == ["knowledge_approval_incomplete"]

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["event_checks"]["error_count"] == 1
    assert check_payload["problems"][0]["code"] == "knowledge_approval_incomplete"


def test_knowledge_consumers_recompute_record_digest(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_knowledge_workspace(tmp_path, monkeypatch)
    (repo / "safe.py").write_text("SAFE = True\n", encoding="utf-8")
    (repo / "wrong.py").write_text("WRONG = True\n", encoding="utf-8")

    approve_payload = _approve_knowledge_source(capsys, build_args=["--applies-to", "safe.py"])
    candidate_id = approve_payload["candidate_id"]
    record_path = tmp_path / approve_payload["data"]["record_path"]
    record = json.loads(record_path.read_text(encoding="utf-8"))
    record["applies_to"] = {"paths": ["wrong.py"]}
    record_path.write_text(json.dumps(record, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["knowledge", "query", "authoritative knowledge approval", "--repo-id", "main", "--json"]) == 1
    query_payload = json.loads(capsys.readouterr().out)
    assert query_payload["data"]["result_count"] == 0
    assert {problem["code"] for problem in query_payload["problems"]} == {
        "knowledge_event_record_digest_mismatch",
        "knowledge_record_digest_mismatch",
    }

    graph_records, graph_problems = knowledge_records_for_graph(tmp_path, repo_id="main")
    assert graph_records == []
    assert {problem.code for problem in graph_problems} == {
        "knowledge_event_record_digest_mismatch",
        "knowledge_record_digest_mismatch",
    }

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["event_checks"]["error_count"] == 2
    assert check_payload["data"]["record_checks"]["problem_codes"]["knowledge_record_digest_mismatch"] == 1

    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 1
    retry_payload = json.loads(capsys.readouterr().out)
    assert retry_payload["problems"][0]["code"] == "knowledge_record_digest_mismatch"


def test_candidate_check_and_approval_reject_identity_and_digest_tampering(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_multirepo_workspace(tmp_path, monkeypatch)

    cases = (
        ("id", "knowledge_candidate_id_mismatch"),
        ("repo_id", "knowledge_candidate_repo_mismatch"),
        ("candidate_digest", "knowledge_candidate_digest_mismatch"),
    )
    for tamper, expected_code in cases:
        assert main(
            [
                "knowledge",
                "candidate",
                "build",
                "--source",
                "docs/contracts/repoctl-context-contract.md",
                "--repo-id",
                "web",
                "--claim",
                "Reviewed Context remains non-authoritative.",
                "--json",
            ]
        ) == 0
        built = json.loads(capsys.readouterr().out)["data"]
        candidate_id = built["candidate"]["id"]
        candidate_path = tmp_path / built["path"]
        candidate = json.loads(candidate_path.read_text(encoding="utf-8"))

        if tamper == "id":
            candidate["id"] = f"{candidate_id}-payload"
        elif tamper == "repo_id":
            candidate["repo_id"] = "api"
        else:
            candidate["claim"] = "Tampered candidate claim."
        if tamper != "candidate_digest":
            candidate["candidate_digest"] = digest_data(
                {key: value for key, value in candidate.items() if key != "candidate_digest"}
            )
        candidate_path.write_text(
            json.dumps(candidate, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
            encoding="utf-8",
        )

        assert main(["knowledge", "candidate", "check", candidate_id, "--repo-id", "web", "--json"]) == 1
        check_payload = json.loads(capsys.readouterr().out)
        assert [problem["code"] for problem in check_payload["problems"]] == [expected_code]

        assert main(["knowledge", "approve", candidate_id, "--repo-id", "web", "--json"]) == 1
        approve_payload = json.loads(capsys.readouterr().out)
        assert [problem["code"] for problem in approve_payload["problems"]] == [expected_code]
        assert not (tmp_path / "docs/knowledge/records" / f"K{candidate_id[2:]}.json").exists()


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
    assert payload["data"]["lifecycle"]["event_checks"]["error_count"] == 2
    assert [problem["code"] for problem in payload["problems"]] == [
        "knowledge_event_digest_mismatch",
        "knowledge_event_record_digest_mismatch",
    ]


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

    specific_record = _approve_knowledge_source(
        capsys,
        source="docs/adr/context-benchmark-gates.md",
        claim="Context benchmark gates reject stale reviewed knowledge source drift before release.",
    )["data"]["record"]["id"]

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


def test_knowledge_consumers_require_complete_supersede_lifecycle(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    old_record = _approve_knowledge_source(capsys, claim="Original reviewed routing decision.")["data"]["record"]["id"]
    replacement = _approve_knowledge_source(
        capsys,
        claim="Replacement reviewed routing decision.",
        approve_args=["--supersedes", old_record],
    )
    replacement_candidate = replacement["candidate_id"]
    superseded_event_path = tmp_path / replacement["data"]["superseded_events"][0]["event_path"]
    superseded_event_path.unlink()

    assert main(["knowledge", "query", "Replacement reviewed routing decision.", "--repo-id", "main", "--json"]) == 1
    query_payload = json.loads(capsys.readouterr().out)
    assert query_payload["data"]["result_count"] == 0
    assert query_payload["problems"][0]["code"] == "knowledge_superseded_event_missing"

    graph_records, graph_problems = knowledge_records_for_graph(tmp_path, repo_id="main")
    assert graph_records == []
    assert [problem.code for problem in graph_problems] == ["knowledge_superseded_event_missing"]

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert check_payload["data"]["event_checks"]["error_count"] == 1
    assert check_payload["problems"][0]["code"] == "knowledge_superseded_event_missing"

    assert main(["knowledge", "approve", replacement_candidate, "--repo-id", "main", "--supersedes", old_record, "--json"]) == 1
    retry_payload = json.loads(capsys.readouterr().out)
    assert retry_payload["problems"][0]["code"] == "knowledge_superseded_event_missing"


def test_knowledge_approval_rollback_attempts_all_written_artifacts(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    old_record = _approve_knowledge_source(capsys, claim="Original reviewed routing decision.")["data"]["record"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--claim", "Replacement reviewed routing decision.", "--json"]) == 0
    replacement_candidate = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    replacement_record = "K" + replacement_candidate[2:]
    replacement_record_path = tmp_path / "docs/knowledge/records" / f"{replacement_record}.json"
    approved_event_path: list[Path] = []
    unlink_attempts: list[Path] = []
    real_atomic_write = knowledge_candidates.atomic_write
    real_unlink = Path.unlink

    def fail_superseded_event(path: Path, text: str) -> None:
        payload = json.loads(text)
        if payload.get("type") == "approved":
            approved_event_path[:] = [path]
        if payload.get("type") == "superseded":
            raise OSError("injected superseded event write failure")
        real_atomic_write(path, text)

    def fail_approved_event_rollback(self: Path, *args, **kwargs):
        unlink_attempts.append(self)
        if approved_event_path and self == approved_event_path[0]:
            raise OSError("injected approved event rollback failure")
        return real_unlink(self, *args, **kwargs)

    with monkeypatch.context() as patcher:
        patcher.setattr(knowledge_candidates, "atomic_write", fail_superseded_event)
        patcher.setattr(Path, "unlink", fail_approved_event_rollback)
        assert main(["knowledge", "approve", replacement_candidate, "--repo-id", "main", "--supersedes", old_record, "--json"]) == 1
        failed_payload = json.loads(capsys.readouterr().out)

    assert failed_payload["problems"][0]["code"] == "knowledge_approval_rollback_failed"
    assert approved_event_path[0].is_file()
    assert replacement_record_path in unlink_attempts
    assert not replacement_record_path.exists()

    assert main(["knowledge", "approve", replacement_candidate, "--repo-id", "main", "--supersedes", old_record, "--json"]) == 1
    retry_payload = json.loads(capsys.readouterr().out)
    assert retry_payload["problems"][0]["code"] == "knowledge_approval_incomplete"
    assert not replacement_record_path.exists()


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
    assert render_payload["data"]["record_count"] == 1
    assert render_payload["data"]["event_count"] == 2
    decisions_text = (tmp_path / "docs/knowledge/generated/decisions.md").read_text(encoding="utf-8")
    assert f"records/{old_record}.md" not in decisions_text
    assert f"records/{new_record}.md" in decisions_text
    new_record_text = (tmp_path / "docs/knowledge/generated/records" / f"{new_record}.md").read_text(encoding="utf-8")
    assert not (tmp_path / "docs/knowledge/generated/records" / f"{old_record}.md").exists()
    assert f"- Supersedes: `{old_record}`" in new_record_text
    assert "- Lifecycle events: `" in new_record_text
    assert f"- Approved from candidate: `{second_candidate}`" in new_record_text
    assert f"- Related at approval: `{old_record} status=reviewed relation=same_claim`" in new_record_text
    assert not (tmp_path / "docs/knowledge/generated/history.md").exists()


def test_knowledge_reject_candidate_writes_event_only(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--claim", "Reviewed Context remains non-authoritative.", "--json"]) == 0
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

    assert main(["knowledge", "query", "broad reviewed knowledge", "--repo-id", "main", "--json"]) == 1
    query_payload = json.loads(capsys.readouterr().out)
    assert query_payload["problems"][0]["code"] == "knowledge_projection_unavailable"
    assert main(["knowledge", "rebuild", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
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
    render_payload = json.loads(capsys.readouterr().out)
    assert render_payload["data"]["record_count"] == 0
    assert render_payload["data"]["event_count"] == 0
    index_text = (tmp_path / "docs/knowledge/generated/INDEX.md").read_text(encoding="utf-8")
    assert "- reviewed: 0" in index_text
    assert "deprecated" not in index_text
    decisions_text = (tmp_path / "docs/knowledge/generated/decisions.md").read_text(encoding="utf-8")
    assert f"records/{record['id']}.md" not in decisions_text
    assert not (tmp_path / "docs/knowledge/generated/records" / f"{record['id']}.md").exists()
    assert not (tmp_path / "docs/knowledge/generated/history.md").exists()

    assert main(["knowledge", "status", "--repo-id", "main", "--json"]) == 0
    status_payload = json.loads(capsys.readouterr().out)
    assert status_payload["data"]["record_statuses"] == {"deprecated": 1}
    assert status_payload["data"]["event_types"] == {"approved": 1, "deprecated": 1}


def test_knowledge_record_and_event_show_enforce_repo_namespace(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_knowledge_multirepo_workspace(tmp_path, monkeypatch)

    approval = _approve_knowledge_source(capsys, repo_id="web")["data"]
    event_id = approval["event"]["id"]
    record_id = approval["record"]["id"]

    assert main(["knowledge", "event", "list", "--repo-id", "api", "--json"]) == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert list_payload["data"]["event_count"] == 0

    assert main(["knowledge", "event", "show", event_id, "--repo-id", "api", "--json"]) == 1
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["problems"][0]["code"] == "knowledge_event_repo_mismatch"

    assert main(["knowledge", "show", record_id, "--repo-id", "web", "--json"]) == 0
    web_payload = json.loads(capsys.readouterr().out)
    assert web_payload["data"]["record"]["repo_id"] == "web"

    assert main(["knowledge", "show", record_id, "--repo-id", "api", "--json"]) == 1
    api_payload = json.loads(capsys.readouterr().out)
    assert api_payload["problems"][0]["code"] == "knowledge_record_repo_mismatch"

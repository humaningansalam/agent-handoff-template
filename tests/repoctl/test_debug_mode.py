from __future__ import annotations

import json
from concurrent.futures import ThreadPoolExecutor
from pathlib import Path

import pytest

from tests.repoctl.context_test_helpers import (
    _rebuild_completion_history,
    _setup_context_workspace,
    _write_completion_receipt,
)
from tests.repoctl.knowledge_test_helpers import _approve_knowledge_source
from tests.repoctl.meta.test_meta_check import commit_all
from tests.repoctl.repository.test_repositories import write_settings
from tests.repoctl.task_lifecycle_helpers import add_board_task, task_text
from tests.repoctl.workspace.test_check import write_workspace
from tools.repoctl.cli import main
from tools.repoctl.debug import DEBUG_EVENTS_REL, append_debug_event


def _events(root: Path) -> list[dict]:
    return [
        json.loads(line)
        for line in (root / DEBUG_EVENTS_REL).read_text(encoding="utf-8").splitlines()
    ]


def _repo_task(task_id: str) -> str:
    return (
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )


def _select_result_member(
    capsys,
    *,
    task_id: str,
    query: str,
    receipt: dict,
    authority: str,
    ref: str = "",
    producer: str = "context",
) -> None:
    items = receipt.get("selectable") or receipt["manifest"]["items"]
    ref = ref or next(item["ref"] for item in items if item["authority"] == authority)
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            query,
            "--result-producer",
            producer,
            "--result-id",
            receipt["result_id"],
            "--result-authority",
            authority,
            "--result-ref",
            ref,
            "--json",
        ]
    ) == 0
    capsys.readouterr()


def test_debug_mode_is_opt_in_and_preserves_normal_output(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "resume"]) == 0
    disabled = capsys.readouterr()
    assert not (tmp_path / DEBUG_EVENTS_REL).exists()

    write_settings(tmp_path, {"debug_mode": True})
    assert main(["task", "resume"]) == 0
    enabled = capsys.readouterr()

    assert enabled == disabled
    [event] = _events(tmp_path)
    assert event["command"] == "task.resume"

    (tmp_path / DEBUG_EVENTS_REL).unlink()
    (tmp_path / DEBUG_EVENTS_REL).parent.rmdir()
    (tmp_path / DEBUG_EVENTS_REL).parent.symlink_to(tmp_path, target_is_directory=True)
    assert main(["task", "resume"]) == 0
    assert capsys.readouterr() == disabled


@pytest.mark.parametrize("value", ["true", 1, None])
def test_debug_mode_rejects_non_boolean_settings(tmp_path: Path, monkeypatch, capsys, value: object) -> None:
    write_workspace(tmp_path)
    write_settings(tmp_path, {"debug_mode": value})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "resume", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_debug_mode"
    assert not (tmp_path / DEBUG_EVENTS_REL).exists()


def test_debug_mode_records_failures_without_values_or_handoff_mutation(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    task_path = add_board_task(tmp_path, f"{task_id}--debug.md", task_text(task_id, status="doing"))
    write_settings(tmp_path, {"debug_mode": True})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
    capsys.readouterr()
    task_before_resume = task_path.read_bytes()
    assert main(["task", "resume", "--json"]) == 0
    resume = json.loads(capsys.readouterr().out)
    assert resume["data"]["resume_guidance"]["status"] == "current"
    assert task_path.read_bytes() == task_before_resume
    assert _events(tmp_path)[-1]["target"]["task_id"] == task_id

    other_id = "T-20260609184047Z"
    add_board_task(tmp_path, f"{other_id}--other.md", task_text(other_id, status="todo"))
    assert main(["task", "resume"]) == 1
    capsys.readouterr()
    assert _events(tmp_path)[-1]["outcome"]["problem_codes"] == ["task_resume_ambiguous"]
    assert main(["debug", "summary", "--json"]) == 0
    resume_stats = json.loads(capsys.readouterr().out)["data"]["commands"]["task.resume"]
    assert (resume_stats["resume_selected"], resume_stats["resume_ambiguous"]) == (1, 1)

    secret = "sentinel-private-task-value"
    assert main(["task", "resume", secret]) == 2
    capsys.readouterr()
    option_shaped_secret = "--sentinel-private-option-shaped-value"
    assert main(["task", "resume", "--", option_shaped_secret]) == 2
    capsys.readouterr()
    unknown_option_secret = "--sentinel-private-token-topsecret"
    assert main(["task", "resume", unknown_option_secret]) == 2
    capsys.readouterr()

    journal = (tmp_path / DEBUG_EVENTS_REL).read_text(encoding="utf-8")
    assert secret not in journal
    assert option_shaped_secret not in journal
    assert unknown_option_secret not in journal
    events = _events(tmp_path)
    assert events[-3]["outcome"]["ok"] is False
    assert events[-1]["outcome"]["problem_codes"] == ["argparse_error"]


def test_debug_summary_correlates_context_features_selection_and_later_success(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "service.py").write_text(
        "from auth import validate_token\n\ndef blue_comet_route(token):\n    return validate_token(token)\n",
        encoding="utf-8",
    )
    (repo / "auth.py").write_text("def validate_token(token):\n    return bool(token)\n", encoding="utf-8")
    commit_all(repo)
    write_settings(tmp_path, {"debug_mode": True})
    assert main(["graph", "query", "--repo-id", "main", "--file", "service.py", "--json"]) == 1
    capsys.readouterr()
    assert main(["context", "query", "blue comet route", "--repo-id", "main", "--full", "--json"]) == 0
    capsys.readouterr()
    before_graph = _events(tmp_path)[-1]["context_sources"]["graph"]
    assert before_graph["available"] is False
    assert before_graph["relation_exposed"] == 0
    _approve_knowledge_source(
        capsys,
        claim="Blue comet routing owns settlement dispatch policy.",
        build_args=["--applies-to", "service.py"],
    )
    _write_completion_receipt(tmp_path)
    _rebuild_completion_history(tmp_path)

    assert main(["graph", "build", "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    assert main(["graph", "query", "--repo-id", "main", "--file", "service.py", "--json"]) == 0
    graph_receipt = json.loads(capsys.readouterr().out)["data"]["result_receipt"]

    task_id = "T-20260609184046Z"
    add_board_task(tmp_path, f"{task_id}--debug-use.md", _repo_task(task_id))
    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()

    query = "blue comet route validate_token"
    assert main(["context", "query", query, "--mode", "call-impact", "--repo-id", "main", "--full", "--json"]) == 0
    context_payload = json.loads(capsys.readouterr().out)
    context_receipt = context_payload["data"]["result_receipt"]
    authorities = {item["authority"] for item in context_receipt["manifest"]["items"]}
    assert {"graph", "knowledge"} <= authorities
    relation_ref = next(
        item["ref"]
        for item in context_receipt["manifest"]["items"]
        if item["authority"] == "graph" and item["ref"].startswith("<graph-relation:")
    )
    _select_result_member(capsys, task_id=task_id, query=query, receipt=context_receipt, authority="graph", ref=relation_ref)
    _select_result_member(capsys, task_id=task_id, query=query, receipt=context_receipt, authority="knowledge")

    history_query = "validate_token token validation"
    assert main(
        ["context", "query", history_query, "--mode", "past-decision", "--repo-id", "main", "--json"]
    ) == 0
    capsys.readouterr()
    compact_history = _events(tmp_path)[-1]["context_sources"]["task_history"]
    assert compact_history["consulted"] is True
    assert compact_history["status"] in {"available", "partial"}
    assert main(
        ["context", "query", history_query, "--mode", "past-decision", "--repo-id", "main", "--full", "--json"]
    ) == 0
    history_payload = json.loads(capsys.readouterr().out)
    history_receipt = history_payload["data"]["result_receipt"]
    assert any(item["authority"] == "task_history" for item in history_receipt["manifest"]["items"])
    _select_result_member(capsys, task_id=task_id, query=history_query, receipt=history_receipt, authority="task_history")
    graph_selection = graph_receipt["selectable"][0]
    _select_result_member(
        capsys,
        task_id=task_id,
        query=history_query,
        receipt=graph_receipt,
        producer="graph",
        authority=graph_selection["authority"],
        ref=graph_selection["ref"],
    )

    assert main(["debug", "summary", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)["data"]

    assert summary["commands"]["graph.query"]["later_same_shape_success_after_failure"] == 1
    assert summary["context_sources"]["graph"]["relations_exposed"] > 0
    assert summary["context_sources"]["graph"]["navigation_exposed"] > 0
    assert summary["context_sources"]["knowledge"]["returned"] > 0
    assert summary["context_sources"]["task_history"]["returned"] > 0
    assert summary["discovery_selections"]["context"]["selected_results"] == 2
    assert summary["discovery_selections"]["context_graph_relation"]["selected_results"] == 1
    assert summary["discovery_selections"]["context_graph_navigation"]["selected_results"] == 0
    assert summary["discovery_selections"]["context_graph_navigation"]["exposed_members"] > 0
    assert summary["discovery_selections"]["context_knowledge"]["selected_results"] == 1
    assert summary["discovery_selections"]["context_task_history"]["selected_results"] == 1
    assert summary["discovery_selections"]["graph_query"]["exposed_results"] == 1
    assert summary["discovery_selections"]["graph_query"]["selected_results"] == 1
    assert main(["debug", "summary"]) == 0
    assert capsys.readouterr().out
    journal = (tmp_path / DEBUG_EVENTS_REL).read_text(encoding="utf-8")
    assert query not in journal
    assert "Blue comet routing owns settlement dispatch policy" not in journal


def test_debug_journal_stays_valid_and_reports_concurrent_bound(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    event = {
        "schema": "repoctl.debug.event",
        "schema_version": 1,
        "timestamp": "2026-09-04T00:00:00+00:00",
        "duration_ms": 1,
        "command": "task.resume",
        "request": {"option_names": [], "argument_count": 2},
        "target": {},
        "outcome": {"exit_code": 0, "ok": True, "problem_codes": [], "warning_codes": []},
    }

    append_debug_event(tmp_path, event)
    line_size = (tmp_path / DEBUG_EVENTS_REL).stat().st_size
    (tmp_path / DEBUG_EVENTS_REL).unlink()
    monkeypatch.setattr("tools.repoctl.debug._MAX_JOURNAL_BYTES", line_size * 8)
    with ThreadPoolExecutor(max_workers=8) as executor:
        list(executor.map(lambda _: append_debug_event(tmp_path, event), range(24)))

    assert len(_events(tmp_path)) == 8
    write_settings(tmp_path, {"debug_mode": True})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["debug", "summary", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["capture"]["incomplete"] is True
    assert {warning["code"] for warning in payload["warnings"]} == {"debug_capture_incomplete"}

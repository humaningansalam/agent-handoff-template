from __future__ import annotations

import json
import shlex
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import digest_data
from tools.repoctl.markdown import find_section, replace_frontmatter_line, replace_section
from tools.repoctl.repositories import require_repo_target
from tools.repoctl.result_receipts import ContextResultRequest, GraphResultRequest, ResultAuthority, ResultProducer, ResultSelection, write_result_receipt
from tools.repoctl.tasks import DiscoveryResultSelection, resolve_task, task_discovery_result_selections
from tests.repoctl.task_lifecycle_helpers import (
    add_board_task,
    init_committed_product_repo,
    init_repo,
    task_text,
    write_workspace,
)


def test_task_start_changes_status_to_doing(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["status"] == "doing"
    text = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "status: doing" in text
    assert "task started" in text
    assert "First command to run: `./scripts/repoctl task list --json`" in text

    assert main(["task", "list", "--json"]) == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert list_payload["command"] == "task.list"
    assert set(list_payload) == {"ok", "command", "data", "warnings", "problems", "next_actions"}
    assert list_payload["data"]["tasks"][0]["id"] == "T-20260609184046Z"


def test_task_show_and_log_append_use_repoctl_lifecycle_boundary(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "log", "append", "T-20260609184046Z", "checked worker output", "--json"]) == 0
    log_payload = json.loads(capsys.readouterr().out)
    assert log_payload["data"]["timestamp"].endswith("Z")
    text = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert f"- {log_payload['data']['timestamp']}: checked worker output" in text

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["ok"] is True
    assert show_payload["data"]["task"]["id"] == "T-20260609184046Z"
    assert "checked worker output" in show_payload["data"]["body"]
    assert set(show_payload) == {"ok", "command", "data", "warnings", "problems", "next_actions"}

    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 0
    summary_payload = json.loads(capsys.readouterr().out)
    assert summary_payload["data"]["task"]["id"] == "T-20260609184046Z"
    assert "body" not in summary_payload["data"]
    assert "frontmatter" not in summary_payload["data"]


def test_task_resume_exposes_only_one_current_live_handoff(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    archived = tmp_path / "docs/archive/tasks/T-20260609184045Z--finished.md"
    archived.write_text(task_text("T-20260609184045Z", status="done"), encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "resume", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"] == {
        "selection": {"status": "no_live", "live_task_count": 0},
        "task": None,
        "resume_guidance": None,
        "candidates": [],
    }

    first = "T-20260609184046Z"
    add_board_task(tmp_path, f"{first}--alpha.md", task_text(first, status="doing"))
    assert main(["task", "resume", "--json"]) == 0
    unbound = json.loads(capsys.readouterr().out)["data"]
    assert unbound["selection"] == {"status": "single_live", "live_task_count": 1}
    assert unbound["resume_guidance"]["status"] == "unbound"
    assert unbound["resume_guidance"]["handoff"]["body"] == ""

    assert main(["task", "handoff", "bind", first, "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "resume", "--json"]) == 0
    current = json.loads(capsys.readouterr().out)["data"]
    assert current["resume_guidance"]["status"] == "current"
    assert current["resume_guidance"]["handoff"]["active"] is True
    assert "Next exact step" in current["resume_guidance"]["handoff"]["body"]

    assert main(["task", "log", "append", first, "changed the live task", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "resume", "--json"]) == 0
    stale = json.loads(capsys.readouterr().out)["data"]
    assert stale["resume_guidance"]["status"] == "stale"
    assert stale["resume_guidance"]["handoff"]["body"] == ""

    second = "T-20260609184047Z"
    second_path = tmp_path / f"docs/tasks/{second}--beta.md"
    second_path.write_text(task_text(second, status="todo"), encoding="utf-8")
    assert main(["task", "resume", "--json"]) == 1
    ambiguous = json.loads(capsys.readouterr().out)
    assert ambiguous["data"]["selection"] == {"status": "ambiguous", "live_task_count": 2}
    assert ambiguous["data"]["task"] is None
    assert ambiguous["data"]["resume_guidance"] is None
    assert [candidate["id"] for candidate in ambiguous["data"]["candidates"]] == [first, second]
    assert ambiguous["problems"][0]["code"] == "task_resume_ambiguous"


def test_task_show_accepts_canonical_id_filename_and_path_with_section_projection(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    filename = "T-20260609184046Z--alpha.md"
    add_board_task(tmp_path, filename, task_text("T-20260609184046Z", status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    for selector in ("T-20260609184046Z", filename, f"docs/tasks/{filename}"):
        assert main(["task", "show", selector, "--section", "Handoff", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["task"]["id"] == "T-20260609184046Z"
        assert payload["data"]["section"]["name"] == "Handoff"
        assert "Next exact step" in payload["data"]["section"]["body"]


def test_task_command_aliases_emit_canonical_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    filename = "T-20260609184046Z--alpha.md"
    task_path = f"docs/tasks/{filename}"
    add_board_task(tmp_path, filename, task_text("T-20260609184046Z", status="todo"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_path, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["task_id"] == "T-20260609184046Z"

    assert main(["task", "discovery", "add", filename, "--query", "canonical identity", "--json"]) == 0
    discovery = json.loads(capsys.readouterr().out)
    assert discovery["data"]["task_id"] == "T-20260609184046Z"

    assert main(["task", "log", "append", task_path, "checked aliases", "--json"]) == 0
    log = json.loads(capsys.readouterr().out)
    assert log["data"]["task_id"] == "T-20260609184046Z"

    assert main(["task", "doctor", filename, "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["data"]["task_id"] == "T-20260609184046Z"


def test_task_show_reports_current_chosen_scope_drift_as_advisory(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"chosen.py": "x = 1\n", "other.py": "y = 1\n"})
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "chosen behavior", "--reviewed", "repos/chosen.py", "--chosen", "repos/chosen.py", "--json"]) == 0
    capsys.readouterr()
    (repo / "other.py").write_text("y = 2\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z--alpha.md", "--summary", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["repo_changes"]["scope"]["unchosen_actual_paths"] == ["other.py"]
    assert payload["data"]["repo_changes"]["scope"]["unused_chosen_paths"] == ["chosen.py"]
    assert payload["warnings"][0]["code"] == "task_chosen_scope_drift"
    assert set(payload["warnings"][0]) == {"severity", "code", "message", "path"}
    scope_action = next(action for action in payload["next_actions"] if action.get("kind") == "task_scope_review")
    assert scope_action["source"] == "data.action_inputs.unchosen_actual_paths"
    assert scope_action["choices"] == ["add_to_chosen", "revert_change", "move_to_follow_up"]
    assert scope_action["target_ref"] == "data.action_inputs.unchosen_actual_paths"
    assert "targets" not in scope_action
    assert payload["data"]["action_inputs"]["unchosen_actual_paths"] == ["other.py"]


def test_task_show_keeps_unused_chosen_paths_informational(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"chosen.py": "x = 1\n"})
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
        .replace("- pending", "- Command: pytest\n- Result: pass")
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "chosen behavior", "--reviewed", "repos/chosen.py", "--chosen", "repos/chosen.py", "--json"]) == 0
    capsys.readouterr()

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["finish_ready"] is True
    assert payload["data"]["repo_changes"]["scope"]["unchosen_actual_paths"] == []
    assert payload["data"]["repo_changes"]["scope"]["unused_chosen_paths"] == ["chosen.py"]
    assert not any(warning["code"] == "task_chosen_scope_drift" for warning in payload["warnings"])
    assert not any(action.get("kind") == "task_scope_review" for action in payload["next_actions"])


def test_task_show_exposes_complete_scope_action_inputs_when_summary_is_truncated(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    files = {"chosen.py": "value = 1\n", **{f"other_{index:02d}.py": "value = 1\n" for index in range(25)}}
    init_committed_product_repo(repo, files)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "chosen behavior", "--reviewed", "repos/chosen.py", "--chosen", "repos/chosen.py", "--json"]) == 0
    capsys.readouterr()
    for path in sorted(files):
        if path != "chosen.py":
            (repo / path).write_text("value = 2\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    scope = payload["data"]["repo_changes"]["scope"]
    assert scope["unchosen_actual_paths_count"] == 25
    assert len(scope["unchosen_actual_paths"]) == 20
    assert scope["unchosen_actual_paths_truncated"] is True
    action = next(action for action in payload["next_actions"] if action.get("kind") == "task_scope_review")
    assert action["source"] == "data.action_inputs.unchosen_actual_paths"
    assert action["target_ref"] == "data.action_inputs.unchosen_actual_paths"
    assert "targets" not in action
    assert len(payload["data"]["action_inputs"]["unchosen_actual_paths"]) == 25


def test_task_start_and_summary_bound_large_path_collections(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    files = {f"file_{index:02d}.py": "value = 1\n" for index in range(25)}
    init_committed_product_repo(repo, files)
    for path in files:
        (repo / path).write_text("value = 2\n", encoding="utf-8")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert start_payload["data"]["dirty_count"] == 25
    assert len(start_payload["data"]["dirty"]) == 20
    assert start_payload["data"]["dirty_truncated"] is True

    for path in files:
        (repo / path).write_text("value = 3\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    summary = payload["data"]["repo_changes"]
    assert summary["task_new"] == 25
    assert len(summary["task_new_files"]) == 20
    assert summary["task_new_files_truncated"] is True
    assert summary["baseline_conflict_count"] == 25
    assert len(summary["baseline_conflicts"]) == 20
    assert summary["baseline_conflicts_truncated"] is True
    action = next(action for action in payload["next_actions"] if action.get("kind") == "baseline_ownership_resolution")
    assert action["source"] == "data.action_inputs.baseline_conflicts"
    assert action["target_ref"] == "data.action_inputs.baseline_conflicts"
    assert "targets" not in action
    assert len(payload["data"]["action_inputs"]["baseline_conflicts"]) == 25

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    full_summary = json.loads(capsys.readouterr().out)["data"]["repo_changes"]
    assert full_summary["baseline_conflict_count"] == 25
    assert len(full_summary["baseline_conflicts"]) == 25
    assert full_summary["baseline_conflicts_truncated"] is False


def test_task_start_types_unborn_repository_observation(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)["data"]["repo_changes"]
    assert summary["repo_head_state"] == "unborn"
    assert summary["observed_since_baseline"] == "observed"
    assert "repo_head" not in summary
    assert "baseline_available" not in summary


def test_task_doctor_builds_one_typed_batch_action_for_all_baseline_conflicts(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"a.py": "a = 1\n", "b.py": "b = 1\n"})
    (repo / "a.py").write_text("a = 2\n", encoding="utf-8")
    (repo / "b.py").write_text("b = 2\n", encoding="utf-8")
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
        .replace("- pending", "- Command: pytest\n- Result: pass")
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--query",
            "two file change",
            "--reviewed",
            "repos/a.py",
            "--reviewed",
            "repos/b.py",
            "--chosen",
            "repos/a.py",
            "--chosen",
            "repos/b.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    (repo / "a.py").write_text("a = 3\n", encoding="utf-8")
    (repo / "b.py").write_text("b = 3\n", encoding="utf-8")

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    action = next(action for action in payload["next_actions"] if action.get("kind") == "baseline_ownership_resolution")
    assert action["source"] == "data.action_inputs.baseline_conflicts"
    assert action["choices"] == ["task", "preexisting"]
    assert action["target_ref"] == "data.action_inputs.baseline_conflicts"
    assert "targets" not in action
    assert payload["data"]["action_inputs"]["baseline_conflicts"] == ["a.py", "b.py"]
    command = shlex.split(action["command"])
    resolutions = [command[index + 1] for index, token in enumerate(command) if token == "--resolution"]
    assert resolutions == ["<path>=<task|preexisting>"]
    assert "--preview" in command
    assert "--ownership" not in command

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2
    finish_payload = json.loads(capsys.readouterr().out)
    assert finish_payload["problems"][0]["code"] == "baseline_conflict"
    assert not any(action.get("kind") == "baseline_ownership_resolution" for action in finish_payload["next_actions"])
    assert any(action["label"] == "Inspect task repo changes" for action in finish_payload["next_actions"])




def test_task_discovery_add_records_structured_scope_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--query",
            "checkout retry behavior",
            "--reviewed",
            "repos/src/checkout.py",
            "--reviewed",
            "repos/tests/test_checkout.py",
            "--chosen",
            "repos/src/checkout.py",
            "--note",
            "retry behavior lives in checkout service",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "task.discovery.add"
    assert payload["data"]["update"]["reviewed_files"]["added"] == ["repos/src/checkout.py", "repos/tests/test_checkout.py"]
    assert payload["data"]["update"]["chosen_files"]["added"] == ["repos/src/checkout.py"]
    assert payload["data"]["totals"]["chosen_file_count"] == 1
    assert "discovery" not in payload["data"]
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "- Candidate query: `checkout retry behavior`" in task_body
    assert "  - `repos/tests/test_checkout.py`" in task_body
    assert "- Notes: `retry behavior lives in checkout service`" in task_body

    assert main(["check", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert not any(warning["code"] == "missing_discovery_evidence" for warning in check_payload["warnings"])


def test_task_discovery_starts_a_new_episode_and_replaces_active_chosen_with_reason(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "q1", "--reviewed", "repos/a.py", "--chosen", "repos/a.py", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "q2", "--reviewed", "repos/b.py", "--json"]) == 0
    query_update = json.loads(capsys.readouterr().out)
    assert query_update["data"]["update"]["chosen_files"] == {
        "mode": "unchanged",
        "added": [],
        "removed": [],
        "already_present": [],
    }
    assert "discovery" not in query_update["data"]
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--replace-chosen", "repos/b.py", "--json"]) == 2
    missing_reason = json.loads(capsys.readouterr().out)
    assert missing_reason["problems"][0]["code"] == "missing_scope_change_reason"

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--replace-chosen", "repos/b.py", "--reason", "implementation moved", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["discovery"]["candidate_query_history"] == ["q2"]
    assert payload["data"]["discovery"]["candidate_files_reviewed"] == ["repos/b.py"]
    assert payload["data"]["discovery"]["chosen_files"] == ["repos/b.py"]
    assert payload["data"]["update"]["chosen_files"] == {
        "mode": "replace",
        "added": ["repos/b.py"],
        "removed": ["repos/a.py"],
        "already_present": [],
    }
    assert all("context pack" not in action["command"] for action in payload["next_actions"])
    assert any(action["command"] == "./scripts/repoctl task doctor T-20260609184046Z --json" for action in payload["next_actions"])
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "scope changed: removed repos/a.py; added repos/b.py; reason=implementation moved" in task_body


def test_task_discovery_accepts_only_receipt_backed_selected_result_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    result_id = digest_data({"context": "selected source"})

    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--result-producer",
            "context",
            "--result-id",
            result_id,
            "--result-authority",
            "source",
            "--result-ref",
            "repos/lib/client.dart",
            "--json",
        ]
    ) == 2
    missing = json.loads(capsys.readouterr().out)
    assert missing["problems"][0]["code"] == "result_receipt_missing"

    target = require_repo_target(tmp_path, repo_id="main")
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
        request=ContextResultRequest(query="selected source", mode="auto"),
        selections=[
            ResultSelection(ResultAuthority.SOURCE, "repos/lib/Client.dart"),
            ResultSelection(ResultAuthority.SOURCE, "repos/lib/client.dart"),
        ],
    )

    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--result-producer",
            "context",
            "--result-id",
            result_id,
            "--result-authority",
            "source",
            "--result-ref",
            "repos/lib/Client.dart",
            "--result-ref",
            "repos/lib/client.dart",
            "--full",
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    selected = payload["data"]["discovery"]["selected_result_evidence"]
    assert len(selected) == 2
    assert {item["ref"] for item in selected} == {
        "repos/lib/Client.dart",
        "repos/lib/client.dart",
    }
    assert all(
        item
        | {
            "episode_id": "<episode>",
            "ref": "<case-sensitive-ref>",
        }
        == {
            "schema_version": 2,
            "producer": "context",
            "result_id": result_id,
            "episode_id": "<episode>",
            "request": {"kind": "context_query", "query": "selected source", "mode": "auto"},
            "authority": "source",
            "ref": "<case-sensitive-ref>",
        }
        for item in selected
    )
    assert len({item["episode_id"] for item in selected}) == 1
    assert selected[0]["episode_id"].startswith("sha256:")
    reloaded = task_discovery_result_selections(resolve_task(tmp_path, "T-20260609184046Z"))
    assert {item.ref for item in reloaded} == {
        "repos/lib/Client.dart",
        "repos/lib/client.dart",
    }
    body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "Selected result evidence" in body
    assert '"authority":"source"' in body

    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--result-producer",
            "graph",
            "--result-id",
            result_id,
            "--json",
        ]
    ) == 2
    failure = json.loads(capsys.readouterr().out)
    assert failure["problems"][0]["code"] == "incomplete_discovery_result_evidence"


def test_task_discovery_reads_legacy_result_evidence_without_inventing_request_ownership() -> None:
    legacy_text = json.dumps(
        {
            "producer": "context",
            "result_id": "sha256:" + ("1" * 64),
            "authority": "source",
            "ref": "repos/legacy.py",
        },
        separators=(",", ":"),
        sort_keys=True,
    )

    selection = DiscoveryResultSelection.from_text(legacy_text)

    assert selection.schema_version == 1
    assert selection.episode_id == ""
    assert selection.request is None
    assert selection.to_text() == legacy_text


def test_task_discovery_result_evidence_round_trips_markdown_sensitive_refs() -> None:
    selection = DiscoveryResultSelection(
        producer=ResultProducer.CONTEXT,
        result_id="sha256:" + ("1" * 64),
        episode_id="sha256:" + ("2" * 64),
        request={"kind": "context_query", "query": "owner`file.py", "mode": "auto"},
        authority=ResultAuthority.SOURCE,
        ref="repos/owner`file.py",
    )

    encoded = selection.to_text()

    assert "`" not in encoded
    assert DiscoveryResultSelection.from_text(f"`{encoded}`") == selection


def test_task_discovery_uses_context_receipt_as_episode_owner_and_accumulates_graph_followups(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    task_id = "T-20260609184046Z"
    text = task_text(task_id, status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, f"{task_id}--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    target = require_repo_target(tmp_path, repo_id="main")

    def add_receipt_result(
        producer: ResultProducer,
        result_id: str,
        authority: ResultAuthority,
        ref: str,
        *,
        query: str = "",
        note: str = "",
    ) -> tuple[int, dict[str, object]]:
        args = [
            "task", "discovery", "add", task_id,
            "--result-producer", producer.value,
            "--result-id", result_id,
            "--result-authority", authority.value,
            "--result-ref", ref,
            "--full", "--json",
        ]
        if query:
            args.extend(["--query", query])
        if note:
            args.extend(["--note", note])
        code = main(args)
        return code, json.loads(capsys.readouterr().out)

    context_q1_id = digest_data({"context": "q1"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=context_q1_id,
        request=ContextResultRequest(query="q1", mode="auto"),
        selections=[ResultSelection(ResultAuthority.SOURCE, "repos/q1.py")],
    )
    code, q1 = add_receipt_result(ResultProducer.CONTEXT, context_q1_id, ResultAuthority.SOURCE, "repos/q1.py", note="context q1")
    assert code == 0
    context_episode_id = q1["data"]["discovery"]["selected_result_evidence"][0]["episode_id"]

    graph_requests = [
        GraphResultRequest.from_query({"type": "symbol", "symbol": "owner"}),
        GraphResultRequest.from_query({"type": "callers_of", "symbol": "owner", "in_file": "src/service.py"}),
    ]
    for index, request in enumerate(graph_requests, start=1):
        graph_id = digest_data({"graph": index})
        write_result_receipt(
            tmp_path,
            target=target,
            producer=ResultProducer.GRAPH,
            result_id=graph_id,
            request=request,
            selections=[ResultSelection(ResultAuthority.GRAPH, f"graph-ref-{index}")],
        )
        code, graph_payload = add_receipt_result(
            ResultProducer.GRAPH,
            graph_id,
            ResultAuthority.GRAPH,
            f"graph-ref-{index}",
            note=f"graph follow-up {index}",
        )
        assert code == 0

    discovery = graph_payload["data"]["discovery"]
    assert discovery["candidate_query_history"] == ["q1"]
    assert discovery["notes"] == ["context q1", "graph follow-up 1", "graph follow-up 2"]
    assert len(discovery["selected_result_evidence"]) == 3
    assert {item["episode_id"] for item in discovery["selected_result_evidence"]} == {context_episode_id}

    context_q1_impact_id = digest_data({"context": "q1-file-impact"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=context_q1_impact_id,
        request=ContextResultRequest(query="q1", mode="file_impact"),
        selections=[ResultSelection(ResultAuthority.SOURCE, "repos/q1-impact.py")],
    )
    code, q1_impact = add_receipt_result(
        ResultProducer.CONTEXT,
        context_q1_impact_id,
        ResultAuthority.SOURCE,
        "repos/q1-impact.py",
        note="context q1 file impact",
    )
    assert code == 0
    q1_impact_discovery = q1_impact["data"]["discovery"]
    assert q1_impact_discovery["candidate_query_history"] == ["q1"]
    assert len(q1_impact_discovery["selected_result_evidence"]) == 4
    assert {item["episode_id"] for item in q1_impact_discovery["selected_result_evidence"]} == {context_episode_id}
    assert {
        item["request"]["mode"]
        for item in q1_impact_discovery["selected_result_evidence"]
        if item["producer"] == "context"
    } == {"auto", "file_impact"}

    context_q2_id = digest_data({"context": "q2"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=context_q2_id,
        request=ContextResultRequest(query="q2", mode="code_location"),
        selections=[ResultSelection(ResultAuthority.SOURCE, "repos/q2.py")],
    )
    code, mismatch = add_receipt_result(
        ResultProducer.CONTEXT,
        context_q2_id,
        ResultAuthority.SOURCE,
        "repos/q2.py",
        query="q1",
    )
    assert code == 2
    assert mismatch["problems"][0]["code"] == "discovery_result_episode_mismatch"

    code, q2 = add_receipt_result(ResultProducer.CONTEXT, context_q2_id, ResultAuthority.SOURCE, "repos/q2.py", note="context q2")
    assert code == 0
    assert q2["data"]["discovery"]["candidate_query_history"] == ["q2"]
    assert q2["data"]["discovery"]["notes"] == ["context q2"]
    assert [item["result_id"] for item in q2["data"]["discovery"]["selected_result_evidence"]] == [context_q2_id]


def test_task_discovery_seeds_graph_only_episode_from_selector_and_context_adopts_exact_query(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    task_id = "T-20260609184046Z"
    text = task_text(task_id, status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, f"{task_id}--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    target = require_repo_target(tmp_path, repo_id="main")

    graph_id = digest_data({"graph": "graph-only"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.GRAPH,
        result_id=graph_id,
        request=GraphResultRequest.from_query({"type": "callers_of", "symbol": "resolve_owner", "in_file": "src/service.py"}),
        selections=[ResultSelection(ResultAuthority.GRAPH, "resolve_owner")],
    )
    assert main(
        [
            "task", "discovery", "add", task_id,
            "--result-producer", "graph",
            "--result-id", graph_id,
            "--result-authority", "graph",
            "--result-ref", "resolve_owner",
            "--note", "graph-only seed",
            "--full", "--json",
        ]
    ) == 0
    graph_discovery = json.loads(capsys.readouterr().out)["data"]["discovery"]
    assert graph_discovery["candidate_query_history"] == ["resolve_owner"]
    graph_episode_id = graph_discovery["selected_result_evidence"][0]["episode_id"]

    context_id = digest_data({"context": "adopt graph-only"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=context_id,
        request=ContextResultRequest(query="resolve_owner", mode="auto"),
        selections=[ResultSelection(ResultAuthority.SOURCE, "repos/service.py")],
    )
    assert main(
        [
            "task", "discovery", "add", task_id,
            "--result-producer", "context",
            "--result-id", context_id,
            "--result-authority", "source",
            "--result-ref", "repos/service.py",
            "--note", "context owner adopted",
            "--full", "--json",
        ]
    ) == 0
    adopted = json.loads(capsys.readouterr().out)["data"]["discovery"]
    assert adopted["candidate_query_history"] == ["resolve_owner"]
    assert adopted["notes"] == ["graph-only seed", "context owner adopted"]
    assert len(adopted["selected_result_evidence"]) == 2
    adopted_episode_ids = {item["episode_id"] for item in adopted["selected_result_evidence"]}
    assert len(adopted_episode_ids) == 1
    assert graph_episode_id not in adopted_episode_ids


def test_task_discovery_query_returns_compact_context_next_action(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "TokenFlow.validate callers", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["next_actions"] == [
        {
            "label": "Find likely product files",
            "command": "./scripts/repoctl context query 'TokenFlow.validate callers' --repo-id main --json",
        }
    ]


def test_task_discovery_rejects_existing_directories_but_allows_future_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "src").mkdir()
    (repo / "existing.py").write_text("value = 1\n", encoding="utf-8")
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "future file", "--reviewed", "repos/src", "--chosen", "repos/new.py", "--json"]) == 2
    reviewed_error = json.loads(capsys.readouterr().out)
    assert reviewed_error["problems"][0]["code"] == "discovery_path_is_directory"

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "future file", "--reviewed", "repos/existing.py", "--chosen", "repos/new.py", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["update"]["chosen_files"]["added"] == ["repos/new.py"]


def test_task_create_print_id_and_root_work_area(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "root-note", "Root Note", "--print-id"]) == 0

    output = capsys.readouterr().out.strip()
    assert output.startswith("T-")
    task_path = next((tmp_path / "docs/tasks").glob(f"{output}--root-note.md"))
    text = task_path.read_text(encoding="utf-8")
    assert "- Product repository: none selected" in text
    assert "Repository: `repos/`" not in text
    assert "Do not touch product files under `repos/`" in text


def test_task_create_start_returns_started_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "started-task", "--start", "Started Task", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["started"] is True
    assert payload["data"]["status"] == "doing"
    assert "status: doing" in (tmp_path / payload["data"]["path"]).read_text(encoding="utf-8")


def test_repo_scoped_task_start_reports_structured_discovery_next_action(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    text = task_text("T-20260609184046Z").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    commands = [action.get("command", "") for action in payload["next_actions"]]
    assert any("task discovery add T-20260609184046Z" in command for command in commands)
    assert any("context query '<query>' --repo-id main" in command for command in commands)


def test_task_create_blocks_when_repo_ref_uses_non_repo_area(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "repo-docs", "--area", "docs", "--repo-ref", "repos", "Update repo docs", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_ref_non_repo_area"


def test_task_create_blocks_root_repo_ref_alias(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "root-ref", "--area", "ops", "--repo-ref", "root", "Root ref", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_repo_ref"




def test_task_start_blocks_repo_scoped_task_without_repo_git(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "repos").mkdir()
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"
    assert "status: todo" in (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")


def test_task_start_fails_on_dirty_repo_by_default_for_repo_scoped_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_dirty"
    assert "status: todo" in (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")


def test_task_start_records_dirty_repo_for_root_task_without_force(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "docs"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["status"] == "doing"
    assert payload["data"]["repo_changes"]["preexisting_dirty"] == 1
    assert payload["data"]["repo_changes"]["task_new"] == 0
    assert payload["warnings"][0]["code"] == "root_task_repo_dirty_recorded"
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "dirty repo state recorded" in task_body
    assert (tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json").is_file()


def test_root_task_records_readable_baseline_for_unbound_collection_candidates(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "docs"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    init_repo(tmp_path / "repos/web")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["repo_changes"]["observed_since_baseline"] == "observed"
    state = json.loads((tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json").read_text(encoding="utf-8"))
    assert state["initial"]["repositories"][0]["repo_id"] == ""
    assert state["initial"]["repositories"][0]["identity_source"] == "unbound"


def test_task_state_schema_version_requires_an_exact_json_integer(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    state_path = tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["schema_version"] = 4.0
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "task_state_schema_unsupported"


def test_task_start_force_dirty_records_dirty_files(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty"]) == 0

    text = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "dirty repo state recorded" in text
    assert "dirty.txt" in text
    assert (tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json").is_file()


def test_task_show_and_doctor_report_task_new_changed_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"'))
    repo = tmp_path / "repos"
    init_committed_product_repo(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    (repo / "changed.py").write_text("print('changed')\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["data"]["repo_changes"]["task_new_files"] == ["changed.py"]

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 0
    doctor_payload = json.loads(capsys.readouterr().out)
    assert doctor_payload["data"]["repo_changes"]["task_new_files"] == ["changed.py"]
    assert doctor_payload["problems"] == []


def test_task_lifecycle_keeps_created_document_language_when_workspace_setting_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/repoctl.json").write_text('{"document_language":"ko"}\n', encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "korean-lifecycle", "Korean Lifecycle", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    task_id = payload["data"]["task_id"]
    task_path = tmp_path / payload["data"]["path"]
    assert 'document_language: "ko"' in task_path.read_text(encoding="utf-8")

    (tmp_path / "docs/repoctl.json").write_text('{"document_language":"en"}\n', encoding="utf-8")

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    started = task_path.read_text(encoding="utf-8")
    assert "작업을 시작" in started
    assert "구현을 계속한다" in started
    assert "task started." not in started

    verification = tmp_path / "verification.md"
    verification.write_text("- Command: pytest\n- Result: pass\n", encoding="utf-8")
    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0
    finish_payload = json.loads(capsys.readouterr().out)
    archived = (tmp_path / finish_payload["data"]["new_path"]).read_text(encoding="utf-8")
    assert "작업을 검증하고 완료함" in archived
    assert "Repoctl 게이트 요약" not in archived
    assert "- Command: pytest" in archived
    assert "## Last Active Handoff" in archived
    assert "## Closure" in archived
    assert "repoctl 관리 범위가 아님" in archived
    assert "task finished and verified" not in archived


def test_json_argparse_errors_are_machine_readable(capsys) -> None:
    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "task_not_found"


def test_task_start_force_dirty_rejects_doing_task_and_preserves_initial_state(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    state_path = tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json"
    initial_state = state_path.read_bytes()

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "task_already_started"
    assert state_path.read_bytes() == initial_state


def test_task_block_resume_preserves_initial_head_and_dirty_baseline(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "old\n"})
    blocker = tmp_path / "blocker.md"
    blocker.write_text("waiting for review\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    state_path = tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json"
    initial_state = state_path.read_bytes()
    (repo / "app.py").write_text("new\n", encoding="utf-8")

    assert main(["task", "block", "T-20260609184046Z", "--verification-file", str(blocker), "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0

    capsys.readouterr()
    assert state_path.read_bytes() == initial_state


def _start_repo_task_with_resume_surface(tmp_path: Path, monkeypatch, capsys) -> tuple[Path, Path, Path]:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    task_path = add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    receipt = tmp_path / "docs/tasks/.repoctl-state/resume/T-20260609184046Z.json"
    return task_path, repo, receipt


def _show_resume_guidance(capsys) -> dict:
    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 0
    return json.loads(capsys.readouterr().out)["data"]["resume_guidance"]


def _bind_handoff(capsys, *extra: str) -> dict:
    assert main(["task", "handoff", "bind", "T-20260609184046Z", *extra, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def test_task_handoff_is_readable_but_inactive_until_explicit_bind(tmp_path: Path, monkeypatch, capsys) -> None:
    task_path, _repo, receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)

    assert not receipt.exists()
    before = task_path.read_bytes()
    guidance = _show_resume_guidance(capsys)
    assert guidance["status"] == "unbound"
    assert guidance["handoff"]["active"] is False
    assert "Next exact step" in guidance["handoff"]["body"]

    payload = _bind_handoff(capsys)
    assert payload["data"]["resume_guidance"]["status"] == "current"
    assert payload["data"]["resume_guidance"]["context_pack"]["status"] == "not_bound"
    assert receipt.is_file()
    assert task_path.read_bytes() == before

    bound_receipt = receipt.read_bytes()
    blocker = tmp_path / "blocker.md"
    blocker.write_text("Waiting for an external decision.\n", encoding="utf-8")
    assert main(["task", "block", "T-20260609184046Z", "--verification-file", str(blocker), "--json"]) == 0
    capsys.readouterr()
    assert receipt.read_bytes() == bound_receipt
    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert receipt.read_bytes() == bound_receipt
    assert start_payload["data"]["resume_guidance"]["status"] == "stale"


def test_task_handoff_binding_tracks_each_structured_task_input(tmp_path: Path, monkeypatch, capsys) -> None:
    task_path, _repo, _receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    _bind_handoff(capsys)

    def assert_stale(input_name: str) -> None:
        guidance = _show_resume_guidance(capsys)
        assert guidance["status"] == "stale"
        assert input_name in guidance["changed_inputs"]
        _bind_handoff(capsys)
        assert _show_resume_guidance(capsys)["status"] == "current"

    text = task_path.read_text(encoding="utf-8")
    task_path.write_text(
        replace_section(
            text,
            "Handoff",
            "- Next exact step: inspect the changed owner.\n"
            "- First file to open: `repos/app.py`\n"
            "- First command to run: `git -C repos diff -- app.py`\n"
            "- Done when: the owner is verified.\n",
        ),
        encoding="utf-8",
    )
    assert_stale("handoff")

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "app owner", "--json"]) == 0
    capsys.readouterr()
    assert_stale("discovery")

    assert main(["task", "log", "append", "T-20260609184046Z", "reviewed app owner", "--json"]) == 0
    capsys.readouterr()
    assert_stale("execution_log")

    text = task_path.read_text(encoding="utf-8")
    task_path.write_text(replace_section(text, "Verification", "- Command: pytest\n- Result: pass\n"), encoding="utf-8")
    assert_stale("verification")

    text = task_path.read_text(encoding="utf-8")
    task_path.write_text(replace_frontmatter_line(text, "status", "blocked"), encoding="utf-8")
    guidance = _show_resume_guidance(capsys)
    assert guidance["status"] == "stale"
    assert "task_contract" in guidance["changed_inputs"]


def test_task_handoff_repository_digest_detects_same_head_content_drift_but_not_touch(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _task_path, repo, _receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    app = repo / "app.py"
    app.write_text("value = 2\n", encoding="utf-8")
    _bind_handoff(capsys)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    app.write_text("value = 3\n", encoding="utf-8")
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == head
    guidance = _show_resume_guidance(capsys)
    assert guidance["status"] == "stale"
    assert "repository" in guidance["changed_inputs"]

    _bind_handoff(capsys)
    app.touch()
    assert _show_resume_guidance(capsys)["status"] == "current"


def test_task_handoff_invalid_receipt_fails_closed_and_archived_handoff_is_historical(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_path, _repo, receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    _bind_handoff(capsys)
    receipt.write_text("{}\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 1
    show_payload = json.loads(capsys.readouterr().out)
    guidance = show_payload["data"]["resume_guidance"]
    assert show_payload["ok"] is False
    assert show_payload["problems"][0]["code"] == "task_resume_binding_invalid"
    assert guidance["status"] == "unknown"
    assert guidance["handoff"]["reason_codes"] == ["resume_binding_invalid"]
    assert guidance["handoff"]["active"] is False
    assert main(["check", "--json"]) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "task_resume_binding_invalid" for problem in check_payload["problems"])

    _bind_handoff(capsys)
    archived = tmp_path / "docs/archive/tasks" / task_path.name
    archived.write_text(replace_frontmatter_line(task_path.read_text(encoding="utf-8"), "status", "done"), encoding="utf-8")
    task_path.unlink()
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n## Backlog\n", encoding="utf-8")

    guidance = _show_resume_guidance(capsys)
    assert guidance["status"] == "historical"
    assert guidance["handoff"]["active"] is False


def test_task_handoff_bind_rejects_missing_empty_and_duplicate_canonical_fields(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_path, _repo, receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    original = task_path.read_text(encoding="utf-8")
    canonical = {
        "Next exact step": "inspect the owner",
        "First file to open": "`repos/app.py`",
        "First command to run": "`git -C repos diff -- app.py`",
        "Done when": "the owner is verified",
    }

    for omitted in canonical:
        body = "".join(f"- {label}: {value}\n" for label, value in canonical.items() if label != omitted)
        task_path.write_text(replace_section(original, "Handoff", body), encoding="utf-8")
        assert main(["task", "handoff", "bind", "T-20260609184046Z", "--json"]) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["problems"][0]["code"] == "invalid_handoff_structure"
        assert not receipt.exists()

    empty = "".join(
        f"- {label}: {'' if label == 'Next exact step' else value}\n"
        for label, value in canonical.items()
    )
    task_path.write_text(replace_section(original, "Handoff", empty), encoding="utf-8")
    assert main(["task", "handoff", "bind", "T-20260609184046Z", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == "invalid_handoff_structure"

    duplicate = "".join(f"- {label}: {value}\n" for label, value in canonical.items()) + "- Done when: duplicate\n"
    task_path.write_text(replace_section(original, "Handoff", duplicate), encoding="utf-8")
    assert main(["task", "handoff", "bind", "T-20260609184046Z", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == "invalid_handoff_structure"


def test_task_show_rejects_strictly_malformed_resume_receipts(tmp_path: Path, monkeypatch, capsys) -> None:
    _task_path, _repo, receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    _bind_handoff(capsys)
    valid = json.loads(receipt.read_text(encoding="utf-8"))
    invalid_receipts = []

    boolean_version = dict(valid)
    boolean_version["schema_version"] = True
    invalid_receipts.append(boolean_version)

    wrong_task = dict(valid)
    wrong_task["task_id"] = "T-20260609184047Z"
    invalid_receipts.append(wrong_task)

    extra_field = dict(valid)
    extra_field["unexpected"] = True
    invalid_receipts.append(extra_field)

    noncanonical_pack_path = dict(valid)
    noncanonical_pack_path["context_pack"] = {
        "path": "./.repoctl-state/context-pack/pack.json",
        "artifact_sha256": "sha256:" + "1" * 64,
        "input_digest": "sha256:" + "2" * 64,
    }
    invalid_receipts.append(noncanonical_pack_path)

    for invalid in invalid_receipts:
        receipt.write_text(json.dumps(invalid, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["problems"][0]["code"] == "task_resume_binding_invalid"
        assert payload["data"]["resume_guidance"]["status"] == "unknown"


def test_task_show_rejects_missing_handoff_and_keeps_summary_inputs_bounded(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_path, _repo, _receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    _bind_handoff(capsys)
    text = task_path.read_text(encoding="utf-8")
    long_query = "query-sentinel-" + "x" * 4000
    long_verification = "verification-sentinel-" + "y" * 4000
    text = replace_section(
        text,
        "Discovery",
        f"- Candidate query: {long_query}\n- Candidate files reviewed: `repos/app.py`\n- Chosen files: `repos/app.py`\n",
    )
    text = replace_section(text, "Verification", f"- {long_verification}\n")
    task_path.write_text(text, encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 0
    summary_text = capsys.readouterr().out
    assert "query-sentinel" not in summary_text
    assert "verification-sentinel" not in summary_text
    assert len(summary_text) < 12000

    text = task_path.read_text(encoding="utf-8")
    handoff_section = find_section(text, "Handoff")
    task_path.write_text((text[: handoff_section.start] + text[handoff_section.end :]).rstrip() + "\n", encoding="utf-8")
    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["problems"][0]["code"] == "missing_handoff"
    assert payload["data"]["resume_guidance"]["status"] == "unknown"

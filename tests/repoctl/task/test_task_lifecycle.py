from __future__ import annotations

import json
import shlex
from pathlib import Path

from tools.repoctl.cli import main
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
    assert scope_action["source"] == "data.repo_changes.scope.unchosen_actual_paths"
    assert scope_action["choices"] == ["add_to_chosen", "revert_change", "move_to_follow_up"]
    assert scope_action["targets"] == ["other.py"]


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
    summary = json.loads(capsys.readouterr().out)["data"]["repo_changes"]
    assert summary["task_new"] == 25
    assert len(summary["task_new_files"]) == 20
    assert summary["task_new_files_truncated"] is True
    assert summary["baseline_conflict_count"] == 25
    assert len(summary["baseline_conflicts"]) == 20
    assert summary["baseline_conflicts_truncated"] is True

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    full_summary = json.loads(capsys.readouterr().out)["data"]["repo_changes"]
    assert full_summary["baseline_conflict_count"] == 25
    assert len(full_summary["baseline_conflicts"]) == 25
    assert full_summary["baseline_conflicts_truncated"] is False


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
    assert action["source"] == "data.repo_changes.baseline_conflicts"
    assert action["choices"] == ["task", "preexisting"]
    assert action["targets"] == ["a.py", "b.py"]
    command = shlex.split(action["command"])
    resolutions = [command[index + 1] for index, token in enumerate(command) if token == "--resolution"]
    assert resolutions == ["a.py=<task|preexisting>", "b.py=<task|preexisting>"]
    assert "--preview" in command
    assert "--ownership" not in command




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


def test_task_discovery_keeps_query_history_and_replaces_active_chosen_with_reason(tmp_path: Path, monkeypatch, capsys) -> None:
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
    assert payload["data"]["discovery"]["candidate_query_history"] == ["q1", "q2"]
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

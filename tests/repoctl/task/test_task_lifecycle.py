from __future__ import annotations

import json
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
    assert payload["status"] == "doing"
    text = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "status: doing" in text
    assert "task started" in text
    assert "First command to run: `./scripts/repoctl task list --json`" in text


def test_task_show_and_log_append_use_repoctl_lifecycle_boundary(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "log", "append", "T-20260609184046Z", "checked worker output", "--json"]) == 0
    log_payload = json.loads(capsys.readouterr().out)
    assert log_payload["timestamp"].endswith("Z")
    text = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert f"- {log_payload['timestamp']}: checked worker output" in text

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["ok"] is True
    assert show_payload["task"]["id"] == "T-20260609184046Z"
    assert "checked worker output" in show_payload["body"]

    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 0
    summary_payload = json.loads(capsys.readouterr().out)
    assert summary_payload["data"]["task"]["id"] == "T-20260609184046Z"
    assert "body" not in summary_payload["data"]
    assert "frontmatter" not in summary_payload["data"]


def test_task_commands_accept_slugged_task_file_id(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "show", "T-20260609184046Z--alpha", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["task"]["id"] == "T-20260609184046Z"

    assert main(["task", "start", "T-20260609184046Z--alpha.md", "--json"]) == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert start_payload["status"] == "doing"


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
            "repoctl meta suggest --text checkout retry",
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
    assert payload["data"]["discovery"]["chosen_files"] == ["repos/src/checkout.py"]
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "- Candidate query: `repoctl meta suggest --text checkout retry`" in task_body
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
    capsys.readouterr()
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--replace-chosen", "repos/b.py", "--json"]) == 2
    missing_reason = json.loads(capsys.readouterr().out)
    assert missing_reason["problems"][0]["code"] == "missing_scope_change_reason"

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--replace-chosen", "repos/b.py", "--reason", "implementation moved", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["discovery"]["candidate_query_history"] == ["q1", "q2"]
    assert payload["data"]["discovery"]["chosen_files"] == ["repos/b.py"]
    assert any(
        action["command"]
        == "./scripts/repoctl context pack --task T-20260609184046Z --repo-id main --format markdown --output .repoctl-state/context-pack/T-20260609184046Z.md"
        for action in payload["next_actions"]
    )
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "scope changed: removed repos/a.py; added repos/b.py; reason=implementation moved" in task_body


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
    assert payload["data"]["discovery"]["chosen_files"] == ["repos/new.py"]


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
    assert payload["started"] is True
    assert payload["status"] == "doing"
    assert "status: doing" in (tmp_path / payload["path"]).read_text(encoding="utf-8")


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
    assert any("task doctor T-20260609184046Z" in command for command in commands)


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


def test_backlog_promotion_uses_repo_id_not_repo_ref_as_selector(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "add", "Add product feature", "--json"]) == 0
    backlog_id = json.loads(capsys.readouterr().out)["data"]["item"]["id"]

    assert main(["task", "create", "--backlog-id", backlog_id, "--slug", "product-feature", "--area", "repo", "--repo-id", "main", "Add product feature", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    task_text_body = (tmp_path / payload["path"]).read_text(encoding="utf-8")
    assert 'repo_id: "main"' in task_text_body
    assert 'repo_ref: ""' in task_text_body
    assert "- Repository: `main`" in task_text_body


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
    assert payload["status"] == "doing"
    assert payload["repo_changes"]["preexisting_dirty"] == 1
    assert payload["repo_changes"]["task_new"] == 0
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
    assert show_payload["repo_changes"]["task_new_files"] == ["changed.py"]

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
    task_id = payload["task_id"]
    task_path = tmp_path / payload["path"]
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
    archived = (tmp_path / finish_payload["new_path"]).read_text(encoding="utf-8")
    assert "작업을 검증하고 완료함" in archived
    assert "Repoctl 게이트 요약" in archived
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

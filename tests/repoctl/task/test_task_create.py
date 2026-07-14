from __future__ import annotations
from tests.repoctl.workspace.test_check import add_task, init_repo, task_text, write_workspace

import json
import subprocess
from pathlib import Path

from tools.repoctl.cli import main



def test_task_create_matches_existing_filename_contract(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "backend", "--repo-ref", "repos", "--json", "Example Task"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["path"].startswith("docs/tasks/T-")
    assert payload["path"].endswith("--example-task.md")
    created = tmp_path / payload["path"]
    assert created.is_file()
    text = created.read_text(encoding="utf-8")
    assert f"id: {payload['task_id']}" in text
    assert "YYYYMMDDTHHMMSSZ" not in text
    assert "## Work Area" in text
    assert "## Discovery" in text
    assert "## Verification" in text
    assert "## Handoff" in text
    assert "Repository: `main`" in text
    assert "Repo ref hint: `repos`" in text
    assert 'document_language: "en"' in text


def test_task_create_uses_configured_korean_document_language(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/repoctl.json").write_text('{"document_language":"ko"}\n', encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "korean-doc", "한국어 문서", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    text = (tmp_path / payload["path"]).read_text(encoding="utf-8")
    assert 'document_language: "ko"' in text
    assert "## Work Area" in text
    assert "## Discovery" in text
    assert "## Handoff" in text


def test_task_create_rejects_invalid_document_language_config(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/repoctl.json").write_text('{"document_language":"kr"}\n', encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "bad-language", "Bad Language", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_document_language"


def test_task_create_registers_board_plain_path(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "plain-path", "Plain Path"]) == 0

    out = capsys.readouterr().out
    path = out.split("Created: ", 1)[1].splitlines()[0]
    board = (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    assert f"- {path}\n" in board
    assert "status" not in board.split("## Board", 1)[1].split("## Backlog", 1)[0]


def test_task_create_rejects_multiline_title(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "bad-title", "Bad\nTitle", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_title"


def test_task_create_rejects_unknown_area_and_root_repo_ref(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "app", "--slug", "memo-cli", "Memo CLI", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_area"

    assert main(["task", "create", "--area", "docs", "--repo-ref", ".", "--slug", "memo-cli", "Memo CLI", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_repo_ref"


def test_task_create_with_backlog_id_removes_raw_block_without_parsing_it(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["backlog", "add", "Add discount support", "--json"]) == 0
    backlog_id = json.loads(capsys.readouterr().out)["data"]["item"]["id"]

    assert main(["task", "create", "--backlog-id", backlog_id, "--slug", "discount-support", "--area", "backend", "--repo-id", "main", "Add discount support", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    task = (tmp_path / payload["path"]).read_text(encoding="utf-8")
    board = (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    assert payload["backlog_removed"] is True
    assert payload["backlog_id"] == backlog_id
    assert "Add discount support" not in board.split("## Backlog", 1)[1]
    assert f"- {payload['path']}" in board.split("## Board", 1)[1].split("## Backlog", 1)[0]
    assert "area: \"backend\"" in task
    assert f"- Backlog origin: `{backlog_id}`" in task


def test_task_create_with_backlog_id_requires_explicit_promotion_fields(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["backlog", "add", "Add discount support", "--json"]) == 0
    backlog_id = json.loads(capsys.readouterr().out)["data"]["item"]["id"]

    assert main(["task", "create", "--backlog-id", backlog_id, "--area", "backend", "--repo-ref", "repos", "Add discount support", "--json"]) == 2
    missing_slug = json.loads(capsys.readouterr().out)
    assert missing_slug["problems"][0]["code"] == "missing_slug"

    assert main(["task", "create", "--backlog-id", backlog_id, "--slug", "discount-support", "Add discount support", "--json"]) == 2
    missing_area = json.loads(capsys.readouterr().out)
    assert missing_area["problems"][0]["code"] == "missing_area"

    assert main(["task", "create", "--backlog-id", backlog_id, "--slug", "discount-support", "--area", "backend", "Add discount support", "--json"]) == 0
    promoted = json.loads(capsys.readouterr().out)
    task = (tmp_path / promoted["path"]).read_text(encoding="utf-8")
    assert 'repo_id: "main"' in task
    assert 'repo_ref: ""' in task
    assert "Add discount support" not in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_create_rolls_back_task_file_when_board_write_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["backlog", "add", "Add discount support", "--json"]) == 0
    backlog_id = json.loads(capsys.readouterr().out)["data"]["item"]["id"]

    real_atomic_write = __import__("tools.repoctl.cli", fromlist=["atomic_write"]).atomic_write

    def fail_board_write(path: Path, text: str) -> None:
        if path.name == "BOARD.md":
            raise OSError("simulated board write failure")
        real_atomic_write(path, text)

    monkeypatch.setattr("tools.repoctl.cli.atomic_write", fail_board_write)

    assert main(["task", "create", "--backlog-id", backlog_id, "--slug", "discount-support", "--area", "backend", "--repo-id", "main", "Add discount support", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert not list((tmp_path / "docs/tasks").glob("T-*--discount-support.md"))
    assert "Add discount support" in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_create_start_rolls_back_task_board_and_backlog_when_start_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    (repo / "dirty.py").write_text("print('dirty')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["backlog", "add", "Add discount support", "--json"]) == 0
    backlog_id = json.loads(capsys.readouterr().out)["data"]["item"]["id"]
    board_before = (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")

    assert main(["task", "create", "--backlog-id", backlog_id, "--slug", "discount-support", "--area", "repo", "--repo-id", "main", "--start", "Add discount support", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_dirty"
    assert not list((tmp_path / "docs/tasks").glob("T-*--discount-support.md"))
    assert (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8") == board_before


def test_task_create_validates_board_before_writing_task_file(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "bad-board", "Bad Board", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "missing_section"
    assert not list((tmp_path / "docs/tasks").glob("T-*--bad-board.md"))




def test_task_create_parent_uses_parent_template(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--type", "parent", "--slug", "parent-task", "Parent Task", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    text = (tmp_path / payload["path"]).read_text(encoding="utf-8")
    assert "## Live Child Tasks" in text
    assert 'parent: ""' in text
    assert "T-YYYYMMDDHHMMSSZ--child-task.md" not in text
    assert "Child tasks are discovered from child frontmatter" in text


def test_task_create_rejects_repo_scoped_parent(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--type", "parent", "--area", "backend", "--slug", "repo-parent", "Repo Parent", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "parent_repo_scope_forbidden"


def test_task_create_rejects_missing_parent(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--parent", "T-20260609184046Z", "Child", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "parent_task_not_found"


def test_task_create_follow_up_keeps_completed_task_immutable(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    previous_id = "T-20260609184046Z"
    previous_path = tmp_path / f"docs/archive/tasks/{previous_id}--completed.md"
    previous_text = task_text(previous_id, status="done")
    previous_path.write_text(previous_text, encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--follow-up-of", previous_id, "--slug", "completed-follow-up", "Completed follow-up", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    created = (tmp_path / payload["path"]).read_text(encoding="utf-8")
    assert payload["task_id"] != previous_id
    assert f'follow_up_of: "{previous_id}"' in created
    assert f"- Follow-up of: `{previous_id}`" in created
    assert previous_path.read_text(encoding="utf-8") == previous_text


def test_task_create_rejects_non_ascii_title_without_slug(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "한글 제목", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "missing_slug"


def test_repo_scoped_task_create_reports_structured_discovery_next_action(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "main", "--slug", "repo-work", "Repo Work", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    commands = [action.get("command", "") for action in payload["next_actions"]]
    assert any("task discovery add" in command for command in commands)
    assert any("--reviewed repos/<path> --chosen repos/<path>" in command for command in commands)


def test_task_create_rejects_invalid_parent_id(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--parent", "bad", "Child", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_parent_id"


def test_task_create_rejects_non_parent_parent_target(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--parent", "T-20260609184046Z", "Child", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "parent_target_not_coordinator"

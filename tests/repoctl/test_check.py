from __future__ import annotations

import json
import os
import socket
import subprocess
from hashlib import sha256
from pathlib import Path

from tools.repoctl.board import parse_board, render_board
from tools.repoctl.cli import main
from tools.repoctl.io import RepoctlError, repoctl_lock
from tools.repoctl.markdown import find_section, replace_frontmatter_line
from tools.repoctl.tasks import create_task_file


def write_workspace(root: Path) -> None:
    (root / "docs/tasks").mkdir(parents=True)
    (root / "docs/archive/tasks").mkdir(parents=True)
    (root / "docs/BOARD.md").write_text(
        "# BOARD\n\nintro\n\n## Board\n\n## Backlog\n\n<!-- backlog -->\n",
        encoding="utf-8",
    )
    (root / "AGENTS.md").write_text("rules\n", encoding="utf-8")
    source_root = Path(__file__).resolve().parents[2]
    (root / "docs/tasks/TEMPLATE.md").write_text((source_root / "docs/tasks/TEMPLATE.md").read_text(encoding="utf-8"), encoding="utf-8")
    (root / "docs/tasks/PARENT_TEMPLATE.md").write_text((source_root / "docs/tasks/PARENT_TEMPLATE.md").read_text(encoding="utf-8"), encoding="utf-8")


def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)


def task_text(task_id: str, *, status: str = "todo", parent: str = "") -> str:
    return f'''---
id: {task_id}
title: "Task {task_id}"
status: {status}
owner: "unassigned"
repo_id: ""
repo_ref: ""
created: 20260609T184046Z
area: ""
parent: "{parent}"
depends_on: []
---

# {task_id} - Task

## Execution Log

- created

```text
## Handoff
inside fence
```

## Verification

- pending

## Handoff

- Next exact step: test
- First file to open: `docs/BOARD.md`
- First command to run: `repoctl check`
- Done when: done
'''


def add_task(root: Path, name: str, text: str) -> Path:
    path = root / "docs/tasks" / name
    path.write_text(text, encoding="utf-8")
    return path


def test_section_scanner_ignores_code_fence_heading() -> None:
    text = task_text("T-20260609184046Z")
    section = find_section(text, "Handoff")
    assert text[section.start : section.body_start].strip() == "## Handoff"
    assert "inside fence" not in text[section.body_start : section.end]


def test_repoctl_lock_uses_repoctl_lock_dir_and_times_out(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    try:
        create_task_file(tmp_path, title="No Lock", slug="no-lock")
        raise AssertionError("task creation should require the repoctl lock")
    except RepoctlError as error:
        assert "task creation requires repoctl lock" in str(error)

    lock_dir = tmp_path / "docs/tasks/.repoctl.lock.d"
    lock_dir.mkdir()

    try:
        with repoctl_lock(tmp_path, timeout=0.0, interval=0.0):
            raise AssertionError("lock should not be acquired while the lock directory exists")
    except RepoctlError as error:
        assert "docs/tasks/.repoctl.lock.d" in str(error)


def test_frontmatter_replace_preserves_other_lines() -> None:
    text = task_text("T-20260609184046Z", status="todo")
    replaced = replace_frontmatter_line(text, "status", "doing")
    assert "status: doing" in replaced
    assert "owner: \"unassigned\"" in replaced
    assert "```text\n## Handoff\ninside fence" in replaced


def test_check_reports_board_missing_live_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    exit_code = main(["check", "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["board"]["missing"] == ["docs/tasks/T-20260609184046Z--alpha.md"]
    assert any(problem["code"] == "board_missing_live_task" for problem in payload["problems"])


def test_check_does_not_require_repo_ref_for_repository_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z").replace('area: ""', 'area: "backend"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert not any(problem["code"] == "missing_repo_ref" for problem in payload["problems"])
    assert not any(warning["code"] == "missing_repo_ref" for warning in payload["warnings"])

    assert main(["task", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert not any(warning["code"] == "missing_repo_ref" for warning in payload["warnings"])


def test_check_warns_when_repo_scoped_task_omits_discovery_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    warning = next(warning for warning in payload["warnings"] if warning["code"] == "missing_discovery_evidence")
    assert "structured Discovery fields" in warning["message"]
    assert "repoctl task discovery add" in warning["message"]


def test_check_accepts_structured_discovery_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    text = text.replace(
        "## Execution Log",
        "## Discovery\n\n- Candidate query: `checkout retry`\n- Candidate files reviewed: `repos/src/checkout.py`\n- Chosen files: `repos/src/checkout.py`\n\n## Execution Log",
    )
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert not any(warning["code"] == "missing_discovery_evidence" for warning in payload["warnings"])


def test_check_accepts_multiline_discovery_file_lists(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    text = text.replace(
        "## Execution Log",
        "## Discovery\n\n"
        "- Candidate query: `checkout retry`\n"
        "- Candidate files reviewed:\n"
        "  - `repos/src/checkout.py`\n"
        "  - `repos/tests/test_checkout.py`\n"
        "- Chosen files:\n"
        "  - `repos/src/checkout.py`\n"
        "\n## Execution Log",
    )
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert not any(warning["code"] == "missing_discovery_evidence" for warning in payload["warnings"])


def test_check_does_not_warn_repo_ref_for_docs_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z").replace('area: ""', 'area: "docs"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert not any(problem["code"] == "missing_repo_ref" for problem in payload["problems"])


def test_check_warns_when_context_doc_is_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z").replace("## Execution Log", "## Context Docs\n\n- `docs/MISSING.md`\n- `AGENTS.md`\n\n## Execution Log")
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "missing_context_doc" and problem.get("path") == "docs/MISSING.md" for problem in payload["problems"])
    assert not any(problem["code"] == "missing_context_doc" and problem.get("path") == "AGENTS.md" for problem in payload["problems"])


def test_check_warns_on_execution_log_timestamp_order(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z").replace("- created", "- 20260611T020000Z: later\n- 20260611T010000Z: earlier")
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["severity"] == "warning" and problem["code"] == "execution_log_timestamp_order" for problem in payload["problems"])
    assert any(warning["code"] == "execution_log_timestamp_order" for warning in payload["warnings"])


def test_check_suppresses_archived_warnings_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    archived = task_text("T-20260609184046Z", status="done").replace('area: ""', 'area: "backend"')
    archived = archived.replace("- created", "- 20260611T020000Z: later\n- 20260611T010000Z: earlier")
    archive_path = tmp_path / "docs/archive/tasks/T-20260609184046Z--archived.md"
    archive_path.write_text(archived, encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["warnings"] == []
    assert not any(problem["path"] == "docs/archive/tasks/T-20260609184046Z--archived.md" for problem in payload["problems"])

    assert main(["check", "--include-archived-warnings", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["path"] == "docs/archive/tasks/T-20260609184046Z--archived.md" and problem["code"] == "execution_log_timestamp_order" for problem in payload["problems"])
    assert any(problem["path"] == "docs/archive/tasks/T-20260609184046Z--archived.md" and problem["code"] == "execution_log_timestamp_order" for problem in payload["problems"])


def test_check_keeps_archived_errors_visible(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    archived = task_text("T-20260609184046Z", status="done").replace("id: T-20260609184046Z", "id: T-20260609184047Z")
    archive_path = tmp_path / "docs/archive/tasks/T-20260609184046Z--archived.md"
    archive_path.write_text(archived, encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["path"] == "docs/archive/tasks/T-20260609184046Z--archived.md" and problem["code"] == "id_filename_mismatch" for problem in payload["problems"])


def test_check_fix_board_renders_live_tasks_only(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    add_task(tmp_path, "T-20260609184047Z--done-child.md", task_text("T-20260609184047Z", status="done", parent="T-20260609184046Z"))
    (tmp_path / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184047Z--done-child.md\n\n## Backlog\n\nkeep\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    exit_code = main(["check", "--fix-board"])

    assert exit_code == 0
    board = (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    assert parse_board(board) == ["docs/tasks/T-20260609184046Z--alpha.md"]
    assert "## Backlog\n\nkeep" in board


def test_check_rejects_invalid_status(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="wip"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    exit_code = main(["check", "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "invalid_status" for problem in payload["problems"])


def test_check_rejects_done_standalone_left_in_live_tasks(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="done"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    exit_code = main(["check", "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "done_standalone_in_tasks" for problem in payload["problems"])


def test_check_rejects_archive_task_with_live_status(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    archive = tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md"
    archive.write_text(task_text("T-20260609184046Z", status="todo"), encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    exit_code = main(["check", "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "archive_live_status" for problem in payload["problems"])


def test_check_rejects_board_item_pointing_to_missing_file(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--missing.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    exit_code = main(["check", "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "board_missing_file" for problem in payload["problems"])


def test_check_allows_done_child_to_remain_in_docs_tasks_but_not_board(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="done", parent="T-20260609184046Z"))
    (tmp_path / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 0


def test_task_list_json_reports_board_stale(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    exit_code = main(["task", "list", "--json"])

    assert exit_code == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["tasks"][0]["id"] == "T-20260609184046Z"
    assert payload["board"]["stale"] is True
    assert payload["board"]["missing"] == ["docs/tasks/T-20260609184046Z--alpha.md"]


def test_task_list_json_reports_validation_problems(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="wip"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(problem["code"] == "invalid_status" for problem in payload["problems"])


def test_render_board_replaces_only_board_section() -> None:
    text = "# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--old.md\n\n## Backlog\n\n- keep\n"
    rendered = render_board(text, ["docs/tasks/T-20260609184047Z--new.md"])
    assert "- docs/tasks/T-20260609184047Z--new.md" in rendered
    assert "- docs/tasks/T-20260609184046Z--old.md" not in rendered
    assert "## Backlog\n\n- keep" in rendered


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
    assert "State the outcome" not in text
    assert "Step 1" not in text
    assert "YYYYMMDDTHHMMSSZ" not in text
    assert "## Work Area" in text
    assert "Repository: `main`" in text
    assert "Repo ref hint: `repos`" in text
    assert "Area hint: backend" in text
    assert 'document_language: "en"' in text
    assert "do not guess them from the title alone" in text
    assert "task created via repoctl task create" in text
    assert "repoctl meta check --changed" in text
    assert "Keep `repos/.repometa` annotations valid" in text
    assert "First file to open: `docs/tasks/" in text
    assert f"./scripts/repoctl task start {payload['task_id']} --json" in text


def test_task_create_uses_configured_korean_document_language(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/repoctl.json").write_text('{"document_language":"ko"}\n', encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "korean-doc", "한국어 문서", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    text = (tmp_path / payload["path"]).read_text(encoding="utf-8")
    assert 'document_language: "ko"' in text
    assert "repoctl task create로 작업을 생성함" in text
    assert "가장 작은 검증 가능한 변경" in text
    assert "첫 구현 단계에서 정확한 repo, docs, workspace 파일을 확인" in text
    assert "이 작업에 필요한 최소 context docs" in text
    assert "State the outcome" not in text
    assert "List concrete deliverables" not in text


def test_task_create_allows_explicit_english_document_language(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/repoctl.json").write_text('{"document_language":"en"}\n', encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "english-doc", "English Doc", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    text = (tmp_path / payload["path"]).read_text(encoding="utf-8")
    assert 'document_language: "en"' in text
    assert "task created via repoctl task create" in text
    assert "Deliver `English Doc` as the smallest verified change" in text


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


def test_backlog_list_returns_freeform_items_without_interpreting_fields(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    title = "Add percentage discount support"
    (tmp_path / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n## Backlog\n\n"
        f"- {title}\n"
        "  - Area: backend\n"
        "  - Repo ref: repo\n"
        "  - Likely files: `repos/src/pricing.py`, `repos/tests/test_pricing.py`\n"
        "  - Expected behavior: add apply_discount and reject invalid percentages\n"
        "  - Validation: `cd repos && python -m unittest tests/test_pricing.py`\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    raw = "\n".join(
        [
            f"- {title}",
            "  - Area: backend",
            "  - Repo ref: repo",
            "  - Likely files: `repos/src/pricing.py`, `repos/tests/test_pricing.py`",
            "  - Expected behavior: add apply_discount and reject invalid percentages",
            "  - Validation: `cd repos && python -m unittest tests/test_pricing.py`",
        ]
    )
    assert payload["ok"] is True
    assert payload["command"] == "backlog list"
    assert payload["warnings"] == []
    assert payload["data"]["items"] == [
        {"id": "BL-" + sha256(raw.encode("utf-8")).hexdigest()[:12], "title": title, "raw": raw, "line_start": 7, "line_end": 12}
    ]


def test_backlog_add_show_remove_manage_raw_blocks(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n\n<!-- backlog -->\n", encoding="utf-8")
    body = tmp_path / "backlog.md"
    body.write_text("Area: backend\nLikely files: free text only\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "add", "Add discount support", "--body-file", str(body), "--json"]) == 0
    added = json.loads(capsys.readouterr().out)["data"]["item"]
    assert added["raw"] == "- Add discount support\n  Area: backend\n  Likely files: free text only"

    assert main(["backlog", "show", added["id"], "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)["data"]["item"]
    assert shown == added

    assert main(["backlog", "remove", added["id"], "--json"]) == 0
    removed = json.loads(capsys.readouterr().out)["data"]["removed"]
    assert removed == added
    board = (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    assert "Add discount support" not in board
    assert "- docs/tasks/T-20260609184046Z--alpha.md" in board.split("## Board", 1)[1].split("## Backlog", 1)[0]


def test_backlog_add_rejects_multiline_title(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "add", "Bad\nTitle", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_title"


def test_backlog_add_body_file_keeps_bullets_inside_raw_block(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    body = tmp_path / "body.md"
    body.write_text("- looks like another item\nplain note\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "add", "Parent backlog", "--body-file", str(body), "--json"]) == 0

    added = json.loads(capsys.readouterr().out)["data"]["item"]
    assert added["raw"] == "- Parent backlog\n  - looks like another item\n  plain note"
    assert main(["backlog", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert len(payload["data"]["items"]) == 1
    assert payload["data"]["items"][0] == added


def test_backlog_list_does_not_attach_unindented_comments_to_raw_block(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n## Backlog\n\n- First item\n<!-- separator comment -->\n- Second item\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert [item["raw"] for item in payload["data"]["items"]] == ["- First item", "- Second item"]
    assert payload["data"]["items"][0]["line_end"] == payload["data"]["items"][0]["line_start"]


def test_backlog_duplicate_raw_blocks_warn_and_cannot_be_removed_by_id(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n## Backlog\n\n- Duplicate item\n- Duplicate item\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "list", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    backlog_id = payload["data"]["items"][0]["id"]
    assert payload["data"]["items"][0]["line_start"] == payload["data"]["items"][0]["line_end"]
    assert payload["warnings"] == [{"code": "duplicate_backlog_id", "message": f"Backlog raw block id is ambiguous: {backlog_id}"}]

    assert main(["backlog", "remove", backlog_id, "--json"]) == 2
    error = json.loads(capsys.readouterr().out)
    assert error["problems"][0]["code"] == "duplicate_backlog_id"


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


def test_task_create_rolls_back_task_file_when_board_render_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    def fail_render(_board_text: str, _board_paths: list[str]) -> str:
        raise OSError("simulated board render failure")

    monkeypatch.setattr("tools.repoctl.cli.render_board", fail_render)

    assert main(["task", "create", "--slug", "render-fail", "Render Fail", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert not list((tmp_path / "docs/tasks").glob("T-*--render-fail.md"))


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


def test_task_create_rejects_missing_parent(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--parent", "T-20260609184046Z", "Child", "--json"]) == 2
    assert "parent task not found" in capsys.readouterr().out


def test_task_create_rejects_non_ascii_title_without_slug(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "한글 제목", "--json"]) == 2
    assert "non-ASCII title requires explicit --slug" in capsys.readouterr().out


def test_repoctl_script_falls_back_to_python3_without_uv(tmp_path: Path) -> None:
    root = Path(__file__).resolve().parents[2]
    fake_bin = tmp_path / "bin"
    fake_bin.mkdir()
    for name in ["bash", "dirname", "python3"]:
        target = Path("/usr/bin") / name
        if not target.exists():
            target = Path("/bin") / name
        (fake_bin / name).symlink_to(target)
    env = os.environ.copy()
    env["PATH"] = str(fake_bin)
    env["UV_CACHE_DIR"] = str(tmp_path / "uv-cache")
    env.pop("VIRTUAL_ENV", None)

    result = subprocess.run(
        [str(root / "scripts/repoctl"), "check", "--json"],
        cwd=root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    assert json.loads(result.stdout)["ok"] is True


def test_json_error_contract_includes_next_actions_for_missing_verification(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing")
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "task.finish"
    assert payload["data"]["task_id"] == "T-20260609184046Z"
    assert payload["problems"][0]["code"] == "missing_verification_file"
    assert any(action["label"] == "Create verification evidence" for action in payload["next_actions"])
    assert any(action["command"].endswith("--use-task-verification --json") for action in payload["next_actions"])


def test_repo_scoped_task_create_reports_structured_discovery_next_action(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "main", "--slug", "repo-work", "Repo Work", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    commands = [action.get("command", "") for action in payload["next_actions"]]
    assert any("task discovery add" in command for command in commands)
    assert any("--reviewed repos/<path> --chosen repos/<path>" in command for command in commands)


def test_task_doctor_is_read_only_and_reports_advisory_next_actions(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    Path("/tmp/T-20260609184046Z-verification.md").unlink(missing_ok=True)
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    before = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "task.doctor"
    assert payload["data"]["finish_ready"] is False
    assert "missing_discovery_evidence" in payload["data"]["advisory"]
    assert "missing_verification_file" in payload["data"]["advisory"]
    assert any(action["label"] == "Record task discovery evidence" for action in payload["next_actions"])
    after = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert after == before


def test_repoctl_lock_recovers_dead_owner_on_same_host(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    lock_dir = tmp_path / "docs/tasks/.repoctl.lock.d"
    lock_dir.mkdir()
    (lock_dir / "owner.json").write_text(json.dumps({"pid": 999999999, "hostname": socket.gethostname(), "created_at": "2026-06-22T00:00:00Z"}) + "\n", encoding="utf-8")

    with repoctl_lock(tmp_path, timeout=0.1, interval=0.01):
        assert lock_dir.exists()

    assert not lock_dir.exists()


def test_task_create_rejects_invalid_parent_id(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--parent", "bad", "Child", "--json"]) == 2
    assert "invalid parent id format" in capsys.readouterr().out


def test_task_create_rejects_non_parent_parent_target(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--parent", "T-20260609184046Z", "Child", "--json"]) == 2
    assert "not a live coordinating parent" in capsys.readouterr().out


def test_check_rejects_non_live_parent_left_in_tasks(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    parent_text = (tmp_path / "docs/tasks/PARENT_TEMPLATE.md").read_text(encoding="utf-8")
    parent_text = parent_text.replace("id: T-YYYYMMDDHHMMSSZ", "id: T-20260609184046Z", 1)
    parent_text = parent_text.replace("status: todo", "status: done", 1)
    parent_text = parent_text.replace("created: YYYYMMDDTHHMMSSZ", "created: 20260609T184046Z", 1)
    add_task(tmp_path, "T-20260609184046Z--parent.md", parent_text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "non_live_parent_in_tasks" for problem in payload["problems"])


def test_removed_creator_references_stay_removed() -> None:
    root = Path(__file__).resolve().parents[2]
    forbidden = "new" "-task.sh"
    assert not (root / "scripts" / forbidden).exists()
    paths = [
        *root.glob("AGENTS.md"),
        *root.glob("README.md"),
        *root.glob("docs/**/*.md"),
        *root.glob("tests/**/*.py"),
        *root.glob("tools/repoctl/**/*.py"),
    ]
    paths = [path for path in paths if "docs/archive/tasks" not in path.as_posix()]
    offenders = [path for path in paths if forbidden in path.read_text(encoding="utf-8")]
    assert offenders == []

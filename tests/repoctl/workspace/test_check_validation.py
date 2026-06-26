from __future__ import annotations
from tests.repoctl.workspace.test_check import add_task, task_text, write_workspace

import json
from pathlib import Path

from tools.repoctl.board import parse_board
from tools.repoctl.cli import main



def test_check_reports_board_missing_live_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    exit_code = main(["check", "--json"])

    assert exit_code == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["board"]["missing"] == ["docs/tasks/T-20260609184046Z--alpha.md"]
    assert any(problem["code"] == "board_missing_live_task" for problem in payload["problems"])


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

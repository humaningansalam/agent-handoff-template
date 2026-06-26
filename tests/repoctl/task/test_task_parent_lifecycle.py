from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.task_lifecycle_helpers import (
    add_task,
    task_text,
    write_json,
    write_workspace,
)


def test_task_finish_child_does_not_move_file(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="doing", parent="T-20260609184046Z"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n- docs/tasks/T-20260609184047Z--child.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184047Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["archived"] is False
    assert payload["new_path"] == "docs/tasks/T-20260609184047Z--child.md"
    assert (tmp_path / payload["new_path"]).exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184047Z--child.md").exists()
    assert "docs/tasks/T-20260609184047Z--child.md" not in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_finish_child_rolls_back_task_when_board_write_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    child_path = add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="doing", parent="T-20260609184046Z"))
    original_child = child_path.read_text(encoding="utf-8")
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n- docs/tasks/T-20260609184047Z--child.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    real_atomic_write = __import__("tools.repoctl.cli", fromlist=["atomic_write"]).atomic_write

    def fail_board_write(path: Path, text: str) -> None:
        if path.name == "BOARD.md":
            raise OSError("simulated board write failure")
        real_atomic_write(path, text)

    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.atomic_write", fail_board_write)

    assert main(["task", "finish", "T-20260609184047Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert child_path.read_text(encoding="utf-8") == original_child
    assert "docs/tasks/T-20260609184047Z--child.md" in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_finish_parent_blocks_when_live_child_exists(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="doing", parent="T-20260609184046Z"))
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "live_children_block_finish"


def test_task_finish_parent_archives_non_live_child_with_archive_handoff(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="done", parent="T-20260609184046Z"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("parent verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    child_archive = tmp_path / "docs/archive/tasks/T-20260609184047Z--child.md"
    assert payload["archived"] is True
    assert child_archive.exists()
    assert not (tmp_path / "docs/tasks/T-20260609184047Z--child.md").exists()
    child_text = child_archive.read_text(encoding="utf-8")
    assert "First file to open: `docs/archive/tasks/T-20260609184047Z--child.md`" in child_text


def test_task_finish_parent_restores_child_receipt_when_board_write_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    child_path = add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="done", parent="T-20260609184046Z"))
    child_text = child_path.read_text(encoding="utf-8")
    child_hash = "sha256:" + hashlib.sha256(child_text.encode("utf-8")).hexdigest()
    receipt_path = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184047Z.json"
    write_json(
        receipt_path,
        {
            "schema": "repoctl.task.completion",
            "schema_version": 1,
            "task_id": "T-20260609184047Z",
            "repo_id": "",
            "status": "done",
            "completed_at": "2026-06-09T18:40:47Z",
            "task_path": "docs/tasks/T-20260609184047Z--child.md",
            "archive_path": "",
            "content_sha256": child_hash,
            "changed_entries": [],
            "verification": {
                "task_path": "docs/tasks/T-20260609184047Z--child.md",
                "archive_path": "",
                "content_sha256": child_hash,
            },
        },
    )
    original_receipt = receipt_path.read_text(encoding="utf-8")
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("parent verified\n", encoding="utf-8")
    real_atomic_write = __import__("tools.repoctl.cli", fromlist=["atomic_write"]).atomic_write

    def fail_board_write(path: Path, text: str) -> None:
        if path.name == "BOARD.md":
            raise OSError("simulated board write failure")
        real_atomic_write(path, text)

    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.atomic_write", fail_board_write)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert child_path.exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184047Z--child.md").exists()
    assert receipt_path.read_text(encoding="utf-8") == original_receipt


def test_task_cancel_parent_updates_non_live_child_receipt(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    child_path = add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="done", parent="T-20260609184046Z"))
    child_text = child_path.read_text(encoding="utf-8")
    child_hash = "sha256:" + hashlib.sha256(child_text.encode("utf-8")).hexdigest()
    receipt_path = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184047Z.json"
    write_json(
        receipt_path,
        {
            "schema": "repoctl.task.completion",
            "schema_version": 1,
            "task_id": "T-20260609184047Z",
            "repo_id": "",
            "status": "done",
            "completed_at": "2026-06-09T18:40:47Z",
            "task_path": "docs/tasks/T-20260609184047Z--child.md",
            "archive_path": "",
            "content_sha256": child_hash,
            "changed_entries": [],
            "verification": {
                "task_path": "docs/tasks/T-20260609184047Z--child.md",
                "archive_path": "",
                "content_sha256": child_hash,
            },
        },
    )
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("cancel parent\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "cancel", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    capsys.readouterr()
    child_archive_rel = "docs/archive/tasks/T-20260609184047Z--child.md"
    child_archive = tmp_path / child_archive_rel
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert child_archive.exists()
    assert receipt["task_path"] == child_archive_rel
    assert receipt["archive_path"] == child_archive_rel
    assert receipt["verification"]["task_path"] == child_archive_rel
    assert receipt["verification"]["archive_path"] == child_archive_rel


def test_task_finish_parent_reports_corrupt_child_receipt(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="done", parent="T-20260609184046Z"))
    receipt_path = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184047Z.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("{not json\n", encoding="utf-8")
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("parent verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_completion_receipt"
    assert (tmp_path / "docs/tasks/T-20260609184047Z--child.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184047Z--child.md").exists()

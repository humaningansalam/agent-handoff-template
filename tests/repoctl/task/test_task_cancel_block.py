from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.task_lifecycle_helpers import (
    add_task,
    task_text,
    write_repometa,
    write_workspace,
)


def test_task_cancel_records_verification_and_archives_standalone(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "cancel.md"
    verification.write_text("- Reason: opened by mistake\n- Result: cancel requested\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "cancel", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "canceled"
    assert payload["old_path"] == "docs/tasks/T-20260609184046Z--alpha.md"
    assert payload["new_path"] == "docs/archive/tasks/T-20260609184046Z--alpha.md"
    assert not (tmp_path / payload["old_path"]).exists()
    archived = (tmp_path / payload["new_path"]).read_text(encoding="utf-8")
    assert "status: canceled" in archived
    assert "opened by mistake" in archived
    assert "task canceled with verification evidence" in archived
    assert "- meta gate: skipped (task_canceled)" in archived


def test_task_cancel_blocks_task_scoped_repo_changes_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "cancel.md"
    verification.write_text("- Reason: superseded\n- Result: cancel requested\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / "leftover.py").write_text("print('leftover')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "cancel", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_changes_on_cancel"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_cancel_allows_explicit_dirty_cancel_with_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "cancel.md"
    verification.write_text("- Reason: superseded\n- Residue: repos/leftover.py intentionally remains for operator review\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / "leftover.py").write_text("print('leftover')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "cancel", "T-20260609184046Z", "--verification-file", str(verification), "--allow-dirty-cancel", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "task.cancel"
    assert payload["data"]["cancel_gate"]["task_new_changes"] == 1
    archived = (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "task canceled with verification evidence" in archived
    assert "task_new_changes=1" in archived
    assert "docs/tasks/T-20260609184046Z--alpha.md" not in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_block_records_evidence_and_keeps_board_entry(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "blocker.md"
    verification.write_text("- Blocker: screenshot acceptance failed\n- Evidence: mobile viewport still blank\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "block", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "status: blocked" in task_body
    assert "screenshot acceptance failed" in task_body
    assert "task blocked with evidence" in task_body
    assert "./scripts/repoctl task doctor T-20260609184046Z --json" in task_body
    assert "docs/tasks/T-20260609184046Z--alpha.md" in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")




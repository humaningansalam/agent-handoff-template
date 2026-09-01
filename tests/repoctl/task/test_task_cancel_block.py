from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.markdown import find_section, replace_section
from tests.repoctl.task_lifecycle_helpers import (
    add_task,
    task_text,
    write_repometa,
    write_workspace,
)


def _section_body(text: str, heading: str) -> str:
    section = find_section(text, heading)
    return text[section.body_start : section.end]


def test_task_cancel_preserves_verification_and_archives_reason(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = replace_section(task_text("T-20260609184046Z", status="doing"), "Verification", "- Command: pytest -q\n- Result: passed\n")
    task_path = add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    reason = tmp_path / "cancel.md"
    reason.write_text("  opened by   mistake  \n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    verification_before = _section_body(task_path.read_text(encoding="utf-8"), "Verification")

    assert main(["task", "cancel", "T-20260609184046Z--alpha.md", "--reason-file", str(reason), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["task_id"] == "T-20260609184046Z"
    assert payload["data"]["status"] == "canceled"
    assert payload["data"]["old_path"] == "docs/tasks/T-20260609184046Z--alpha.md"
    assert payload["data"]["new_path"] == "docs/archive/tasks/T-20260609184046Z--alpha.md"
    assert not (tmp_path / payload["data"]["old_path"]).exists()
    archived = (tmp_path / payload["data"]["new_path"]).read_text(encoding="utf-8")
    assert "status: canceled" in archived
    assert "opened by mistake" in archived
    assert _section_body(archived, "Verification") == verification_before
    assert payload["data"]["reason"] == "opened by mistake"
    assert payload["data"]["reason_source"] == "file"
    assert "Repoctl gate summary:" not in archived
    assert payload["data"]["cancel_gate"]["status"] == "passed"
    assert payload["data"]["cancel_gate"]["residue_paths"] == []


def test_task_cancel_blocks_task_scoped_repo_changes_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / "leftover.py").write_text("print('leftover')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "cancel", "T-20260609184046Z", "--reason", "superseded", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["task_id"] == "T-20260609184046Z"
    assert payload["problems"][0]["code"] == "repo_changes_on_cancel"
    assert payload["data"]["cancel_gate"]["residue_paths"] == ["repos/leftover.py"]
    assert payload["data"]["action_inputs"]["cancel_residue_paths"] == ["repos/leftover.py"]
    assert payload["next_actions"] == [{"label": "Revert or finish repos/ changes before canceling", "command": "git -C repos status --short"}]
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_cancel_allows_explicit_dirty_cancel_with_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    (repo / "old.py").write_text("print('leftover')\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "mv", "old.py", "new.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "cancel", "T-20260609184046Z", "--reason", "superseded", "--allow-dirty-cancel", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "task.cancel"
    assert payload["data"]["reason"] == "superseded"
    assert payload["data"]["reason_source"] == "argument"
    assert payload["data"]["cancel_gate"]["status"] == "allowed_dirty"
    assert payload["data"]["cancel_gate"]["task_new_changes"] == 1
    assert payload["data"]["cancel_gate"]["residue_paths"] == ["repos/new.py", "repos/old.py"]
    archived = (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "task canceled: superseded" in archived
    assert 'residue_paths=["repos/new.py", "repos/old.py"]' in archived
    assert "task_new_changes=1" not in archived
    assert "docs/tasks/T-20260609184046Z--alpha.md" not in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_block_records_reason_without_changing_verification(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    original_handoff = (
        "- Next exact step: inspect the failed mobile screenshot.\n"
        "- First file to open: `docs/BOARD.md`\n"
        "- First command to run: `pytest tests/mobile -q`\n"
        "- Done when: the blank viewport cause is understood.\n"
    )
    text = task_text("T-20260609184046Z", status="doing")
    text = replace_section(text, "Handoff", original_handoff)
    text = replace_section(text, "Verification", "- Command: pytest -q\n- Result: failed on mobile\n")
    task_path = add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    verification_before = _section_body(task_path.read_text(encoding="utf-8"), "Verification")

    assert main(["task", "block", "docs/tasks/T-20260609184046Z--alpha.md", "--reason", "screenshot acceptance failed", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["task_id"] == "T-20260609184046Z"
    assert payload["data"]["status"] == "blocked"
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "status: blocked" in task_body
    assert "screenshot acceptance failed" in task_body
    assert _section_body(task_body, "Verification") == verification_before
    assert payload["data"]["reason"] == "screenshot acceptance failed"
    assert payload["data"]["reason_source"] == "argument"
    assert original_handoff in task_body
    assert any(action["command"] == "./scripts/repoctl task doctor T-20260609184046Z --json" for action in payload["next_actions"])
    assert "docs/tasks/T-20260609184046Z--alpha.md" in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")

    blocked_bytes = task_path.read_bytes()
    assert main(["task", "block", "T-20260609184046Z", "--reason", "same blocker", "--json"]) == 2
    retry = json.loads(capsys.readouterr().out)
    assert retry["problems"][0]["code"] == "task_already_blocked"
    assert retry["next_actions"] == [{"label": "Inspect the existing blocker before resuming or canceling", "command": "./scripts/repoctl task show T-20260609184046Z --summary --json"}]
    assert task_path.read_bytes() == blocked_bytes


def test_transition_reason_failures_do_not_mutate_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    task_path = add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    invalid_utf8 = tmp_path / "invalid-reason.md"
    invalid_utf8.write_bytes(b"\xff")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    before = task_path.read_bytes()

    cases = [
        (["task", "block", "T-20260609184046Z", "--json"], "argparse_error"),
        (["task", "block", "T-20260609184046Z", "--reason", "  ", "--json"], "empty_transition_reason"),
        (["task", "block", "T-20260609184046Z", "--reason-file", str(tmp_path / "missing.md"), "--json"], "argparse_error"),
        (["task", "block", "T-20260609184046Z", "--reason-file", str(invalid_utf8), "--json"], "transition_reason_file_unreadable"),
        (["task", "block", "T-20260609184046Z", "--reason", "one", "--reason-file", str(invalid_utf8), "--json"], "argparse_error"),
    ]
    for argv, code in cases:
        assert main(argv) == 2
        assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == code
        assert task_path.read_bytes() == before

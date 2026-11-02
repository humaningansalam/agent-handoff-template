from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.markdown import replace_section
from tests.repoctl.task_lifecycle_helpers import add_board_task, task_text, write_workspace


def test_task_log_rejects_machine_output_and_preserves_source(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    task_path = add_board_task(
        tmp_path,
        "T-20260609184046Z--alpha.md",
        task_text("T-20260609184046Z", status="doing"),
    )
    before = task_path.read_bytes()
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    message = 'residue_paths=["repos/a.py", "repos/b.py"]'
    assert main(["task", "log", "append", "T-20260609184046Z", message, "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "task_log_detail_requires_reference"
    assert task_path.read_bytes() == before


def test_task_audit_reports_known_slop_without_blocking(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing")
    text = replace_section(
        text,
        "Execution Log",
        '- 20260609T184046Z: task canceled: superseded; residue_paths=["repos/a.py"]; baseline_conflicts=[]\n',
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "audit", "T-20260609184046Z", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["finding_count"] == 1
    assert payload["warnings"][0]["code"] == "task_log_raw_cancel_paths"

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) in {0, 1}
    doctor = json.loads(capsys.readouterr().out)
    assert any(item["code"] == "task_log_raw_cancel_paths" for item in doctor["warnings"])
    assert doctor["data"]["content_audit"]["bytes"] > 0


def test_task_slop_migration_is_dry_run_then_applies_only_recognized_lines(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing")
    text = replace_section(
        text,
        "Execution Log",
        '- 20260609T184046Z: task canceled: superseded; residue_paths=["repos/a.py", "repos/b.py"]; baseline_conflicts=[]\n'
        '- 20260609T184047Z: scope changed: removed repos/a.py; added repos/c.py, repos/d.py; reason=moved\n'
        '- authored note that must remain byte-for-byte.\n',
    )
    task_path = add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    before = task_path.read_bytes()

    assert main(["task", "migrate-slop", "T-20260609184046Z", "--json"]) == 0
    dry_run = json.loads(capsys.readouterr().out)
    assert dry_run["data"]["mode"] == "dry_run"
    assert dry_run["data"]["candidate_task_count"] == 1
    assert dry_run["data"]["tasks"][0]["change_count"] == 2
    assert task_path.read_bytes() == before

    assert main(["task", "migrate-slop", "T-20260609184046Z", "--apply", "--json"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["data"]["applied_task_count"] == 1
    migrated = task_path.read_text(encoding="utf-8")
    assert "residue_count=2" in migrated
    assert "removed_count=1; added_count=2" in migrated
    assert "residue_paths=[" not in migrated
    assert "authored note that must remain byte-for-byte" in migrated

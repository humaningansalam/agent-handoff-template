from __future__ import annotations

import json
from pathlib import Path

from tools.hooks import prompt_context
from tools.repoctl.cli import main as repoctl_main
from tests.repoctl.task_lifecycle_helpers import add_board_task, task_text, write_workspace


def _hook_context(root: Path, monkeypatch, capsys) -> dict:
    monkeypatch.setattr(prompt_context, "workspace_root", lambda: root)
    monkeypatch.setattr("sys.stdin.isatty", lambda: True)
    prompt_context.main()
    output = json.loads(capsys.readouterr().out)
    return output["hookSpecificOutput"]


def test_prompt_context_injects_only_current_single_live_handoff(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    archived = tmp_path / "docs/archive/tasks/T-20260609184045Z--finished.md"
    archived.write_text(task_text("T-20260609184045Z", status="done"), encoding="utf-8")

    hook = _hook_context(tmp_path, monkeypatch, capsys)
    assert '"status":"no_live"' in hook["additionalContext"]
    assert "Next exact step" not in hook["additionalContext"]

    task_id = "T-20260609184046Z"
    add_board_task(tmp_path, f"{task_id}--alpha.md", task_text(task_id, status="doing"))
    hook = _hook_context(tmp_path, monkeypatch, capsys)
    assert '"status":"single_live"' in hook["additionalContext"]
    assert '"executable_handoff":null' in hook["additionalContext"]

    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert repoctl_main(["task", "handoff", "bind", task_id, "--json"]) == 0
    capsys.readouterr()
    hook = _hook_context(tmp_path, monkeypatch, capsys)
    assert '"status":"current"' in hook["additionalContext"]
    assert "Next exact step: test" in hook["additionalContext"]

    assert repoctl_main(["task", "log", "append", task_id, "changed task state", "--json"]) == 0
    capsys.readouterr()
    hook = _hook_context(tmp_path, monkeypatch, capsys)
    assert '"status":"stale"' in hook["additionalContext"]
    assert '"executable_handoff":null' in hook["additionalContext"]
    assert "Next exact step: test" not in hook["additionalContext"]

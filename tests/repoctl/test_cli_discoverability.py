from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.context_test_helpers import _setup_context_workspace


def test_repoctl_version_aliases(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip()

    assert main(["version", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "version"
    assert payload["data"]["version"]
    assert payload["data"]["workspace_root"] == tmp_path.as_posix()


def test_repoctl_help_alias(capsys) -> None:
    assert main(["help"]) == 0

    output = capsys.readouterr().out
    assert "usage: repoctl" in output
    assert "context" in output


def test_llmwiki_alias_routes_to_knowledge_render(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["llmwiki", "--repo-id", "main", "--check", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "knowledge render"
    assert payload["data"]["check"]["status"] == "empty_not_initialized"


def test_upgrade_status_guides_to_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["upgrade", "status", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "upgrade status"
    assert payload["data"]["status"] == "source_required_for_upgrade_diff"
    assert "upgrade plan --from" in payload["data"]["next_command"]


def test_format_json_errors_return_json(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["context", "pack", "--task", "T-20260101000000Z", "--repo-id", "main", "--format", "json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "context.pack"
    assert payload["data"] == {"task_id": "T-20260101000000Z", "repo_id": "main"}
    assert payload["problems"][0]["code"] == "task_not_found"

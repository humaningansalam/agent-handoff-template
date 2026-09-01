from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.repoctl.cli import main
from tests.repoctl.context_test_helpers import _setup_context_workspace


def test_repoctl_version_surfaces(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["--version"]) == 0
    assert capsys.readouterr().out.strip()

    assert main(["version", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "version"
    assert payload["data"]["version"]
    assert payload["data"]["workspace_root"] == tmp_path.as_posix()






def test_upgrade_status_guides_to_plan(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["upgrade", "status", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "upgrade status"
    assert payload["data"]["status"] == "no_upgrade_receipts"
    assert payload["data"]["receipt_count"] == 0
    assert "upgrade plan --from" in payload["data"]["next_command"]


def test_format_json_errors_return_json(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["context", "pack", "--task", "T-20260101000000Z", "--repo-id", "main", "--format", "json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "context.pack"
    assert payload["data"] == {"task_id": "T-20260101000000Z", "repo_id": "main"}
    assert payload["problems"][0]["code"] == "task_not_found"
    assert any(action["command"] == "./scripts/repoctl task list --json" for action in payload["next_actions"])


@pytest.mark.parametrize("format_args", [["--format", "json"], ["--format=json"]])
def test_format_json_parse_errors_return_json(tmp_path: Path, monkeypatch, capsys, format_args: list[str]) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["context", "query", "example", "--mode", *format_args]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "context"
    assert payload["problems"][0]["code"] == "argparse_error"


def test_repository_errors_return_next_actions(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["repo", "show", "missing", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_not_found"
    assert any(action["command"] == "./scripts/repoctl repo list --json" for action in payload["next_actions"])

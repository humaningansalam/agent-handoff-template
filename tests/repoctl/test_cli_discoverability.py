from __future__ import annotations

import argparse
import json
from pathlib import Path

import pytest

from tools.repoctl.cli import build_parser, main
from tests.repoctl.context_test_helpers import _setup_context_workspace


def test_repoctl_version_surfaces(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    with pytest.raises(SystemExit) as help_exit:
        build_parser().parse_args(["version", "--help"])
    assert help_exit.value.code == 0
    assert "usage: repoctl version" in capsys.readouterr().out

    assert main(["version"]) == 0
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
    assert payload["command"] == "upgrade.status"
    assert payload["data"]["status"] == "no_upgrade_receipts"
    assert payload["data"]["receipt_count"] == 0
    assert "upgrade plan --from" in payload["data"]["next_command"]


def test_format_json_is_rejected_in_favor_of_json_switch(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["context", "pack", "--task", "T-20260101000000Z", "--repo-id", "main", "--format", "json", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["command"] == "context.pack"
    assert payload["problems"][0]["code"] == "argparse_error"
    assert payload["next_actions"] == [{"label": "Show command help", "command": "./scripts/repoctl context pack --help"}]


def test_command_identity_is_stable_across_success_domain_and_parse_failures(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["repo", "show", "main", "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["command"] == "repo.show"

    assert main(["repo", "show", "missing", "--json"]) == 2
    domain = json.loads(capsys.readouterr().out)
    assert domain["command"] == "repo.show"
    assert domain["problems"][0]["code"] == "repository_not_found"

    assert main(["repo", "show", "--json"]) == 2
    parse = json.loads(capsys.readouterr().out)
    assert parse["command"] == "repo.show"
    assert parse["problems"][0]["code"] == "argparse_error"
    assert parse["next_actions"] == [{"label": "Show command help", "command": "./scripts/repoctl repo show --help"}]

    assert main(["repo", "show", "main", "--bogus", "--json"]) == 2
    trailing = json.loads(capsys.readouterr().out)
    assert trailing["command"] == "repo.show"
    assert trailing["next_actions"] == [{"label": "Show command help", "command": "./scripts/repoctl repo show --help"}]

    assert main(["context", "nope", "--json"]) == 2
    hidden = json.loads(capsys.readouterr().out)
    assert hidden["command"] == "context"
    assert "benchmark" not in hidden["problems"][0]["message"]


def test_visible_command_help_has_purpose_and_required_argument_help() -> None:
    parser = build_parser()
    pending = [parser]
    while pending:
        current = pending.pop()
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                assert all(choice.help for choice in action._choices_actions)
                pending.extend(action.choices.values())
            elif action.required or (not action.option_strings and action.dest != "help"):
                assert action.help, f"missing help for {current.prog} {action.dest}"


def test_repository_errors_return_next_actions(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["repo", "show", "missing", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_not_found"
    assert any(action["command"] == "./scripts/repoctl repo list --json" for action in payload["next_actions"])

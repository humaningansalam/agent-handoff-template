from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from tools.repoctl.cli import _next_actions_for_problems, build_parser, main
from tests.repoctl.context_test_helpers import _setup_context_workspace
from tests.repoctl.repository.test_repositories import init_repo
from tests.repoctl.task_lifecycle_helpers import add_board_task, task_text
from tests.repoctl.workspace.test_check import write_workspace


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
    assert payload["data"]["next_command"] == "./scripts/repoctl upgrade plan --help"


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


def test_emitted_commands_execute_without_unresolved_inputs(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    source_root = Path(__file__).resolve().parents[2]
    runner = (
        "from pathlib import Path; import sys; import tools.repoctl.cli as cli; "
        "cli.find_workspace_root = lambda: Path(sys.argv[1]); "
        "raise SystemExit(cli.main(sys.argv[2:]))"
    )

    def exercise(actions: list[dict[str, object]], *, workspace: Path = tmp_path) -> None:
        for action in actions:
            command = str(action.get("command") or "")
            if not command:
                continue
            assert "<" not in command and ">" not in command and "/tmp/" not in command
            argv = shlex.split(command)
            result = subprocess.run(
                [sys.executable, "-c", runner, str(workspace), *argv[1:]],
                cwd=source_root,
                text=True,
                capture_output=True,
                timeout=30,
                check=False,
            )
            if "--json" in argv:
                assert result.stdout.strip(), result.stderr
                payload = json.loads(result.stdout)
                assert result.returncode == (0 if payload["ok"] else result.returncode)
                assert result.returncode in {0, 1, 2}
                assert all(problem.get("code") != "argparse_error" for problem in payload["problems"])
                if result.returncode:
                    assert payload["problems"]
            else:
                assert result.returncode == 0, result.stderr

    assert main(["repo", "show", "main", "--bogus", "--json"]) == 2
    exercise(json.loads(capsys.readouterr().out)["next_actions"])

    assert main(["upgrade", "postflight", "--workspace-root", str(tmp_path), "--json"]) in {0, 1}
    exercise(json.loads(capsys.readouterr().out)["next_actions"])

    assert main(["context", "query", "where is context", "--repo-id", "main", "--json"]) == 0
    exercise(json.loads(capsys.readouterr().out)["next_actions"])

    exercise(_next_actions_for_problems([{"code": "task_not_found"}], data={"task_id": "T-20260902035959Z"}))
    exercise(_next_actions_for_problems([{"code": "repository_selector_required"}], data={"repository_candidates": [{"id": "main", "path": "repos"}]}))
    exercise(_next_actions_for_problems([{"code": "annotation_required", "path": "repos/app.py"}], data={"repository": {"id": "main", "path": "repos"}}))
    exercise(_next_actions_for_problems([{"code": "graph_snapshot_missing"}], data={"repository": {"id": "main"}}))
    exercise(_next_actions_for_problems([{"code": "knowledge_projection_unavailable"}], data={"repository": {"id": "main"}}))
    catalogue_actions = _next_actions_for_problems(
        [],
        data={
            "bundle": {
                "repository": {"id": "main"},
                "query": {"text": "where is context", "mode": "auto"},
                "completeness": {"prior_task_outcome": {"reason": "completion_catalogue_missing"}},
            }
        },
    )
    assert [action["kind"] for action in catalogue_actions] == ["completion_catalogue_rebuild", "context_resume"]
    exercise(catalogue_actions)
    exercise([{"command": "./scripts/repoctl upgrade plan --help"}])

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--kind", "decision", "--claim", "Context remains non-authoritative.", "--json"]) == 0
    exercise(json.loads(capsys.readouterr().out)["next_actions"])

    first_task = "T-20260902030000Z"
    add_board_task(tmp_path, f"{first_task}--resume.md", task_text(first_task))
    exercise(_next_actions_for_problems([{"code": "repo_head_changed_since_start"}], data={"task_id": first_task}))
    exercise(_next_actions_for_problems([{"code": "task_handoff_stale"}], data={"task_id": first_task, "resume_guidance": {"handoff": {"status": "stale"}}}))
    exercise(
        _next_actions_for_problems(
            [{"code": "context_pack_stale"}],
            data={
                "task_id": first_task,
                "task": {"path": f"docs/tasks/{first_task}--resume.md", "repo_id": "main"},
                "resume_guidance": {"context_pack": {"status": "stale", "path": f".repoctl-state/context-pack/{first_task}.json"}},
            },
        )
    )
    exercise(
        _next_actions_for_problems(
            [{"code": "context_graph_seed_identity_unavailable"}],
            data={
                "bundle": {
                    "repository": {"id": "main"},
                    "query": {"text": "where is context", "mode": "auto"},
                    "completeness": {"graph_anchor": {"identity_coverage": {"recovery_selectors": [{"kind": "file", "value": "docs/contracts/repoctl-context-contract.md"}]}}},
                }
            },
        )
    )

    second_task = "T-20260902030001Z"
    for task_id in (second_task,):
        add_board_task(tmp_path, f"{task_id}--resume.md", task_text(task_id))
    assert main(["task", "resume", "--json"]) == 1
    exercise(json.loads(capsys.readouterr().out)["next_actions"])

    collection = tmp_path / "collection"
    write_workspace(collection)
    init_repo(collection / "repos/api")
    init_repo(collection / "repos/web")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: collection)
    assert main(["repo", "list", "--json"]) == 0
    exercise(json.loads(capsys.readouterr().out)["next_actions"], workspace=collection)


def test_repository_errors_return_next_actions(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["repo", "show", "missing", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_not_found"
    assert any(action["command"] == "./scripts/repoctl repo list --json" for action in payload["next_actions"])

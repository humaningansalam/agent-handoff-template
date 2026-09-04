from __future__ import annotations

import argparse
import json
from pathlib import Path
import shlex
import subprocess
import sys

import pytest

from tools.repoctl.cli import _next_actions_for_problems, _task_next_actions, build_parser, main
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


def test_registered_command_help_executes_and_visible_commands_describe_required_arguments() -> None:
    parser = build_parser()
    pending = [((), parser)]
    commands: list[tuple[str, ...]] = []
    while pending:
        command, current = pending.pop()
        commands.append(command)
        for action in current._actions:
            if isinstance(action, argparse._SubParsersAction):
                assert all(choice.help for choice in action._choices_actions)
                pending.extend(
                    ((*command, name), child)
                    for name, child in action.choices.items()
                )
            elif action.required or (not action.option_strings and action.dest != "help"):
                assert action.help, f"missing help for {current.prog} {action.dest}"

    assert len(commands) == 87
    source_root = Path(__file__).resolve().parents[2]
    for command in commands:
        result = subprocess.run(
            [source_root / "scripts/repoctl", *command, "--help"],
            cwd=source_root,
            text=True,
            capture_output=True,
            timeout=10,
            check=False,
        )
        assert result.returncode == 0, (command, result.stderr)
        assert result.stdout.startswith("usage: repoctl"), command


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

    payloads = []
    for argv in (
        ["repo", "show", "main", "--bogus", "--json"],
        ["repo", "show", "missing", "--json"],
        ["task", "resume", "--json"],
        ["graph", "query", "--repo-id", "main", "--file", "app.py", "--json"],
    ):
        main(argv)
        payloads.append(json.loads(capsys.readouterr().out))

    for title in ("First resume candidate", "Second resume candidate"):
        assert main(["task", "create", title, "--json"]) == 0
        capsys.readouterr()
    assert main(["task", "resume", "--json"]) == 1
    payloads.append(json.loads(capsys.readouterr().out))

    for payload in payloads:
        exercise(payload["next_actions"])

    assert main(["upgrade", "postflight", "--workspace-root", str(tmp_path), "--json"]) in {0, 1}
    exercise(json.loads(capsys.readouterr().out)["next_actions"])
    exercise(_next_actions_for_problems([{"code": "task_not_found"}], data={"task_id": "T-20260902035959Z"}))
    exercise(_next_actions_for_problems([{"code": "repository_selector_required"}], data={"repository_candidates": [{"id": "main", "path": "repos"}]}))
    exercise(_next_actions_for_problems([{"code": "annotation_required", "path": "repos/app.py"}], data={"repository": {"id": "main", "path": "repos"}}))
    exercise(_next_actions_for_problems([{"code": "graph_snapshot_missing"}], data={"repository": {"id": "main"}}))
    exercise(_next_actions_for_problems([{"code": "knowledge_projection_unavailable"}], data={"repository": {"id": "main"}}))
    exercise(_next_actions_for_problems([{"code": "missing_upgrade_plan"}]))

    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--kind", "decision", "--claim", "Context remains non-authoritative.", "--json"]) == 0
    exercise(json.loads(capsys.readouterr().out)["next_actions"])

    task_id = "T-20260902030000Z"
    add_board_task(tmp_path, f"{task_id}--resume.md", task_text(task_id))
    exercise(_task_next_actions([{"code": "repo_head_changed_since_start"}], {"task_id": task_id}))
    exercise(_task_next_actions([{"code": "task_handoff_stale"}], {"task_id": task_id, "resume_guidance": {"handoff": {"status": "stale"}}}))
    exercise(
        _task_next_actions(
            [{"code": "context_pack_stale"}],
            {
                "task_id": task_id,
                "task": {"path": f"docs/tasks/{task_id}--resume.md", "repo_id": "main"},
                "resume_guidance": {"context_pack": {"status": "stale", "path": f".repoctl-state/context-pack/{task_id}.json"}},
            },
        )
    )

    graph_actions = _next_actions_for_problems(
            [{"code": "context_graph_seed_identity_unavailable"}],
            data={
                "bundle": {
                    "repository": {"id": "main"},
                    "query": {"text": "where is context", "mode": "past_decision"},
                    "completeness": {
                        "graph_anchor": {
                            "identity_coverage": {
                                "recovery_selectors": [
                                    {"kind": "file", "value": "docs/contracts/repoctl-context-contract.md"},
                                    {"kind": "symbol", "value": "build_context_bundle", "in_file": "tools/repoctl/context.py"},
                                ]
                            }
                        }
                    },
                }
            },
        )
    assert graph_actions[-1]["command"] == "./scripts/repoctl context query 'where is context' --mode past-decision --repo-id main --json"
    exercise(graph_actions)
    caused_graph_actions = _next_actions_for_problems(
        [{"code": "context_graph_unavailable", "cause_code": "graph_snapshot_missing"}],
        data={"repository": {"id": "main"}},
    )
    assert caused_graph_actions[0]["source"] == "problems_or_warnings[].cause_code"
    exercise(caused_graph_actions)

    catalogue_actions = _next_actions_for_problems(
        [{"code": "completion_catalogue_missing"}],
        data={
            "completion_history": {
                "catalogues": [
                    {"repo_id": "api", "problem_code": "completion_catalogue_missing"},
                    {"repo_id": "web", "problem_code": "completion_catalogue_missing"},
                ]
            }
        },
    )
    assert [action["command"] for action in catalogue_actions] == [
        "./scripts/repoctl history rebuild --repo-id api --json",
        "./scripts/repoctl history rebuild --repo-id web --json",
    ]

    collection = tmp_path / "collection"
    write_workspace(collection)
    init_repo(collection / "repos/api")
    init_repo(collection / "repos/web")
    exercise(catalogue_actions, workspace=collection)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: collection)
    assert main(["repo", "list", "--json"]) == 0
    exercise(json.loads(capsys.readouterr().out)["next_actions"], workspace=collection)

    verification_actions = _task_next_actions(
        [{"code": "verification_file_inside_repo", "path": "repos/report.md"}],
        {"task": {"path": "docs/tasks/T-example.md"}},
    )
    assert verification_actions == [
        {"label": "Move verification evidence to an existing workspace file outside repos/", "path": "repos/report.md"}
    ]
    alignment_actions = _task_next_actions(
        [{"code": "discovery_outcome_chosen_mismatch"}],
        {
            "task_id": task_id,
            "task": {"path": f"docs/tasks/{task_id}--resume.md"},
            "discovery_outcome_alignment": {"invalid_outcome_subject_ids": ["invalid"]},
        },
    )
    assert alignment_actions[0]["target_ref"] == "data.discovery_outcome_alignment.invalid_outcome_subject_ids"
    assert alignment_actions[1]["label"] == "Open the Task Discovery section"


def test_repository_errors_return_next_actions(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)

    assert main(["repo", "show", "missing", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_not_found"
    assert any(action["command"] == "./scripts/repoctl repo list --json" for action in payload["next_actions"])

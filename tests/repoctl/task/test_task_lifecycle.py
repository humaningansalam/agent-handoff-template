from __future__ import annotations

import json
import os
import shlex
import subprocess
from pathlib import Path

import pytest

from tools.repoctl.cli import TaskHealth, main
from tools.repoctl.discovery_outcomes import (
    completion_outcome_projection,
    load_outcome_state,
    structured_verification_coverage,
    validate_completion_outcome,
)
from tools.repoctl.graph_model import digest_data
from tools.repoctl.io import RepoctlError
from tools.repoctl.markdown import find_section, replace_frontmatter_line, replace_section
from tools.repoctl.repositories import require_repo_target
from tools.repoctl.result_receipts import ContextResultRequest, GraphResultRequest, ResultAuthority, ResultProducer, ResultSelection, write_result_receipt
from tools.repoctl.tasks import TASK_DOC_COPY, DiscoveryResultSelection, Problem, TaskHandoffProvenance, resolve_task, task_discovery_result_selections, task_handoff_provenance
from tests.repoctl.io_audit import reject_directory_enumeration
from tests.repoctl.task_lifecycle_helpers import (
    add_board_task,
    init_committed_product_repo,
    init_repo,
    task_text,
    write_workspace,
)


def test_task_start_changes_status_and_preserves_authored_handoff(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["status"] == "doing"
    text = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "status: doing" in text
    assert "task started" in text
    assert "First command to run: `repoctl check`" in text
    assert not any(warning["code"] == "task_handoff_generated_template" for warning in payload["warnings"])
    assert payload["next_actions"][-1]["kind"] == "task_handoff_bind"

    assert main(["task", "list", "--json"]) == 0
    list_payload = json.loads(capsys.readouterr().out)
    assert list_payload["command"] == "task.list"
    assert set(list_payload) == {"ok", "command", "data", "warnings", "problems", "next_actions"}
    assert list_payload["data"]["tasks"][0]["id"] == "T-20260609184046Z"


def test_task_show_and_log_append_use_repoctl_lifecycle_boundary(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "log", "append", "T-20260609184046Z", "checked worker output", "--json"]) == 0
    log_payload = json.loads(capsys.readouterr().out)
    assert log_payload["data"]["timestamp"].endswith("Z")
    text = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert f"- {log_payload['data']['timestamp']}: checked worker output" in text

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["ok"] is True
    assert show_payload["data"]["task"]["id"] == "T-20260609184046Z"
    assert "checked worker output" in show_payload["data"]["body"]
    assert set(show_payload) == {"ok", "command", "data", "warnings", "problems", "next_actions"}

    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 0
    summary_payload = json.loads(capsys.readouterr().out)
    assert summary_payload["data"]["task"]["id"] == "T-20260609184046Z"
    assert "body" not in summary_payload["data"]
    assert "frontmatter" not in summary_payload["data"]


def test_task_resume_exposes_only_one_current_live_handoff(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    archived = tmp_path / "docs/archive/tasks/T-20260609184045Z--finished.md"
    archived.write_text(task_text("T-20260609184045Z", status="done"), encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "resume", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"] == {
        "selection": {"status": "no_live", "live_task_count": 0},
        "task": None,
        "resume_guidance": None,
        "candidates": [],
    }

    first = "T-20260609184046Z"
    add_board_task(tmp_path, f"{first}--alpha.md", task_text(first, status="doing"))
    assert main(["task", "resume", "--json"]) == 0
    unbound = json.loads(capsys.readouterr().out)["data"]
    assert unbound["selection"] == {"status": "single_live", "live_task_count": 1}
    assert unbound["resume_guidance"]["status"] == "unbound"
    assert unbound["resume_guidance"]["executable_handoff"] is None
    assert "body" not in unbound["resume_guidance"]["handoff"]

    assert main(["task", "handoff", "bind", first, "--json"]) == 0
    capsys.readouterr()
    binding = json.loads(
        (tmp_path / f"docs/tasks/.repoctl-state/resume/{first}.json").read_text(encoding="utf-8")
    )
    assert binding["schema_version"] == 3
    assert not (tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{first}.json").exists()
    assert main(["task", "resume", "--json"]) == 0
    current = json.loads(capsys.readouterr().out)["data"]
    assert current["resume_guidance"]["status"] == "current"
    assert current["resume_guidance"]["handoff"]["active"] is True
    assert current["resume_guidance"]["blocked_by_health"] is False
    assert "Next exact step" in current["resume_guidance"]["readable_handoff"]
    assert current["resume_guidance"]["executable_handoff"] == current["resume_guidance"]["readable_handoff"]

    assert main(["task", "log", "append", first, "changed the live task", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "resume", "--json"]) == 0
    stale = json.loads(capsys.readouterr().out)["data"]
    assert stale["resume_guidance"]["status"] == "stale"
    assert stale["resume_guidance"]["readable_handoff"] is None
    assert stale["resume_guidance"]["executable_handoff"] is None

    second = "T-20260609184047Z"
    second_path = tmp_path / f"docs/tasks/{second}--beta.md"
    second_path.write_text(task_text(second, status="todo"), encoding="utf-8")
    assert main(["task", "resume", "--json"]) == 1
    ambiguous = json.loads(capsys.readouterr().out)
    assert ambiguous["data"]["selection"] == {"status": "ambiguous", "live_task_count": 2}
    assert ambiguous["data"]["task"] is None
    assert ambiguous["data"]["resume_guidance"] is None
    assert [candidate["id"] for candidate in ambiguous["data"]["candidates"]] == [first, second]
    assert ambiguous["problems"][0]["code"] == "task_resume_ambiguous"
    assert [action["command"] for action in ambiguous["next_actions"]] == [
        f"./scripts/repoctl task resume {first} --json",
        f"./scripts/repoctl task resume {second} --json",
    ]

    before = {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}
    assert main(["task", "resume", second, "--json"]) == 0
    selected = json.loads(capsys.readouterr().out)
    assert selected["data"]["selection"] == {
        "status": "selected_live",
        "live_task_count": 2,
        "selected_task_id": second,
    }
    assert selected["data"]["task"]["id"] == second
    assert selected["data"]["resume_guidance"]["status"] == "unbound"
    assert before == {path.relative_to(tmp_path): path.read_bytes() for path in tmp_path.rglob("*") if path.is_file()}

    assert main(["task", "resume", "T-20260609184045Z", "--json"]) == 2
    archived_selection = json.loads(capsys.readouterr().out)
    assert archived_selection["command"] == "task.resume"
    assert archived_selection["problems"][0]["code"] == "task_not_found"
    assert archived_selection["next_actions"][0]["command"] == (
        "./scripts/repoctl task list --json"
    )


def test_task_resume_compacts_repeated_health_problems_unless_full_is_requested(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    add_board_task(tmp_path, f"{task_id}--alpha.md", task_text(task_id, status="doing"))
    repeated = tuple(
        Problem(
            "error",
            "transition_evidence_incomplete",
            f"transition evidence is incomplete for path {index}",
            f"repos/path-{index}.py",
        )
        for index in range(5)
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(
        "tools.repoctl.cli._task_lifecycle_observation",
        lambda root, task, **kwargs: (None, None, TaskHealth("unhealthy", repeated)),
    )

    assert main(["task", "resume", "--json"]) == 1
    compact = json.loads(capsys.readouterr().out)
    guidance = compact["data"]["resume_guidance"]
    assert len(compact["problems"]) == 1
    assert guidance["health"]["problem_count"] == 5
    assert guidance["health"]["details_included"] is False
    assert guidance["health"]["problem_summary"] == [
        {
            "code": "transition_evidence_incomplete",
            "count": 5,
            "sample_paths": ["repos/path-0.py", "repos/path-1.py", "repos/path-2.py"],
            "paths_truncated": True,
        }
    ]
    assert guidance["health"]["details_command"] == f"./scripts/repoctl task doctor {task_id} --json"
    assert guidance["health"]["full_command"] == "./scripts/repoctl task resume --full --json"

    assert main(["task", "resume", "--full", "--json"]) == 1
    full = json.loads(capsys.readouterr().out)
    assert len(full["problems"]) == 5
    assert full["data"]["resume_guidance"]["health"]["details_included"] is True


def test_task_resume_preserves_single_live_identity_when_repository_layout_is_invalid(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    add_board_task(tmp_path, f"{task_id}--alpha.md", task_text(task_id, status="doing"))
    (tmp_path / "docs/repoctl.json").write_text("{not-json\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "resume", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["selection"] == {"status": "single_live", "live_task_count": 1}
    assert payload["data"]["task"]["id"] == task_id
    guidance = payload["data"]["resume_guidance"]
    assert guidance["health"]["status"] == "unhealthy"
    assert guidance["health"]["codes"] == ["invalid_repoctl_settings"]
    assert guidance["blocked_by_health"] is True
    assert guidance["executable_handoff"] is None
    assert payload["problems"][0]["code"] == "invalid_repoctl_settings"

    assert main(["task", "show", task_id, "--summary", "--json"]) == 1
    shown = json.loads(capsys.readouterr().out)
    assert shown["data"]["task"]["id"] == task_id
    assert shown["data"]["health"]["status"] == "unhealthy"
    assert "invalid_repoctl_settings" in shown["data"]["health"]["codes"]

    assert main(["task", "doctor", task_id, "--json"]) == 1
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["data"]["task_id"] == task_id
    assert doctor["data"]["health"]["status"] == "unhealthy"
    assert "invalid_repoctl_settings" in doctor["data"]["health"]["codes"]


def test_task_list_does_not_enumerate_archive(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    add_board_task(tmp_path, f"{task_id}--alpha.md", task_text(task_id, status="doing"))
    archived = tmp_path / "docs/archive/tasks"
    (archived / "T-20260609184045Z--cold.md").write_text("not a task\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    with monkeypatch.context() as audit_patch:
        with reject_directory_enumeration(audit_patch, archived) as cold_reads:
            assert main(["task", "list", "--json"]) == 0

    assert cold_reads == []


def test_archived_task_locator_rejects_symlinked_archive_parent(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184045Z"
    archive = tmp_path / "docs/archive/tasks"
    archive.rmdir()
    outside = tmp_path / "outside-archive"
    outside.mkdir()
    archived = outside / f"{task_id}--escaped.md"
    archived.write_text(task_text(task_id, status="done"), encoding="utf-8")
    archive.symlink_to(outside, target_is_directory=True)
    locator = tmp_path / f"docs/tasks/.repoctl-state/archive/{task_id}.json"
    locator.parent.mkdir(parents=True)
    locator.write_text(
        json.dumps(
            {
                "schema": "repoctl.task.archive",
                "schema_version": 1,
                "task_id": task_id,
                "task_path": f"docs/archive/tasks/{archived.name}",
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "show", task_id, "--summary", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "task_not_found"


def test_task_show_accepts_canonical_id_filename_and_path_with_section_projection(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    filename = "T-20260609184046Z--alpha.md"
    add_board_task(tmp_path, filename, task_text("T-20260609184046Z", status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    for selector in ("T-20260609184046Z", filename, f"docs/tasks/{filename}"):
        assert main(["task", "show", selector, "--section", "Handoff", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
        assert payload["data"]["task"]["id"] == "T-20260609184046Z"
        assert payload["data"]["section"]["name"] == "Handoff"
        assert "Next exact step" in payload["data"]["section"]["body"]


def test_task_command_aliases_emit_canonical_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    filename = "T-20260609184046Z--alpha.md"
    task_path = f"docs/tasks/{filename}"
    add_board_task(tmp_path, filename, task_text("T-20260609184046Z", status="todo"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_path, "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["task_id"] == "T-20260609184046Z"

    assert main(["task", "discovery", "add", filename, "--query", "canonical identity", "--json"]) == 0
    discovery = json.loads(capsys.readouterr().out)
    assert discovery["data"]["task_id"] == "T-20260609184046Z"

    assert main(["task", "log", "append", task_path, "checked aliases", "--json"]) == 0
    log = json.loads(capsys.readouterr().out)
    assert log["data"]["task_id"] == "T-20260609184046Z"

    assert main(["task", "doctor", filename, "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["data"]["task_id"] == "T-20260609184046Z"


def test_task_show_reports_current_chosen_scope_drift_as_advisory(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"chosen.py": "x = 1\n", "other.py": "y = 1\n"})
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "chosen behavior", "--reviewed", "repos/chosen.py", "--chosen", "repos/chosen.py", "--json"]) == 0
    capsys.readouterr()
    (repo / "other.py").write_text("y = 2\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z--alpha.md", "--summary", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["repo_changes"]["scope"]["unchosen_actual_paths"] == ["other.py"]
    assert payload["data"]["repo_changes"]["scope"]["unused_chosen_paths"] == ["chosen.py"]
    assert payload["warnings"][0]["code"] == "task_chosen_scope_drift"
    assert set(payload["warnings"][0]) == {"severity", "code", "message", "path"}
    scope_action = next(action for action in payload["next_actions"] if action.get("kind") == "task_scope_review")
    assert scope_action["source"] == "data.action_inputs.unchosen_actual_paths"
    assert scope_action["choices"] == ["add_to_chosen", "revert_change", "move_to_follow_up"]
    assert scope_action["target_ref"] == "data.action_inputs.unchosen_actual_paths"
    assert "targets" not in scope_action
    assert payload["data"]["action_inputs"]["unchosen_actual_paths"] == ["other.py"]


def test_task_show_keeps_unused_chosen_paths_informational(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"chosen.py": "x = 1\n"})
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
        .replace("- pending", "- Command: pytest\n- Result: pass")
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "chosen behavior", "--reviewed", "repos/chosen.py", "--chosen", "repos/chosen.py", "--json"]) == 0
    capsys.readouterr()

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["finish_ready"] is True
    assert payload["data"]["repo_changes"]["scope"]["unchosen_actual_paths"] == []
    assert payload["data"]["repo_changes"]["scope"]["unused_chosen_paths"] == ["chosen.py"]
    assert not any(warning["code"] == "task_chosen_scope_drift" for warning in payload["warnings"])
    assert not any(action.get("kind") == "task_scope_review" for action in payload["next_actions"])


def test_task_doctor_warns_until_current_changed_chosen_subject_has_passed_structured_verification(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    task_id = "T-20260609184046Z"
    text = (
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    task_path = add_board_task(tmp_path, f"{task_id}--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "update app",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    task_path.write_text(
        replace_section(task_path.read_text(encoding="utf-8"), "Verification", "- Command: focused check\n- Result: pass\n"),
        encoding="utf-8",
    )
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")

    assert main(["task", "doctor", task_id, "--json"]) == 0
    missing = json.loads(capsys.readouterr().out)
    assert missing["data"]["finish_ready"] is True
    assert missing["data"]["structured_verification"] == {
        "status": "missing",
        "required_subject_count": 1,
        "passed_subject_count": 0,
        "missing_subject_count": 1,
        "nonpassing_subject_count": 0,
    }
    assert missing["data"]["action_inputs"]["unverified_chosen_subjects"] == ["app.py"]
    assert missing["data"]["action_inputs"]["missing_structured_verification_subjects"] == ["app.py"]
    warning = next(item for item in missing["warnings"] if item["code"] == "task_structured_verification_missing")
    assert warning["path"] == task_path.relative_to(tmp_path).as_posix()
    action = next(item for item in missing["next_actions"] if item.get("kind") == "task_verification_add")
    assert action["target_ref"] == "data.action_inputs.missing_structured_verification_subjects"
    assert "task verification add" in action["command"]

    evidence = tmp_path / "focused-check.log"
    evidence.write_text("PASS app.py\n", encoding="utf-8")
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 0
    recorded = json.loads(capsys.readouterr().out)
    assert recorded["next_actions"] == [
        {
            "label": "Check finish readiness",
            "command": f"./scripts/repoctl task doctor {task_id} --json",
        }
    ]

    assert main(["task", "doctor", task_id, "--json"]) == 0
    covered = json.loads(capsys.readouterr().out)
    assert covered["data"]["structured_verification"]["status"] == "complete"
    assert covered["data"]["structured_verification"]["passed_subject_count"] == 1
    assert not any(item["code"] == "task_structured_verification_missing" for item in covered["warnings"])

    assert main(["task", "finish", task_id, "--json"]) == 0
    finished = json.loads(capsys.readouterr().out)
    assert finished["data"]["structured_verification"] == {
        "status": "complete",
        "required_subject_count": 1,
        "passed_subject_count": 1,
        "missing_subject_count": 0,
        "nonpassing_subject_count": 0,
        "unverified_subjects": [],
        "unverified_subject_count": 0,
        "unverified_subjects_truncated": False,
    }
    assert not any(item["code"].startswith("task_structured_verification_") for item in finished["warnings"])


def test_completion_outcome_retains_verified_chosen_subject_after_scope_replacement(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(
        repo,
        {
            "app.py": "value = 1\n",
            "other.py": "value = 1\n",
        },
    )
    task_id = "T-20260609184046Z"
    task_path = add_board_task(
        tmp_path,
        f"{task_id}--alpha.md",
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"'),
    )
    evidence = tmp_path / "focused-check.log"
    evidence.write_text("PASS app.py v1\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "verify app",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    outcome_path = tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json"
    verified_state = json.loads(outcome_path.read_text(encoding="utf-8"))
    verified_subject = verified_state["active_chosen"][0]

    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--replace-chosen",
            "repos/other.py",
            "--reason",
            "move the approved scope after the app check",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--reviewed",
            "repos/other.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            verified_subject["subject_id"],
            "--json",
        ]
    ) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "verification_subject_unknown"

    projection = completion_outcome_projection(tmp_path, task_id)
    assert projection is not None
    assert validate_completion_outcome(projection) == projection
    subjects = {item["id"]: item for item in projection["subjects"]}
    record = projection["verification_records"][0]
    assert record["subject_ids"]
    assert [subjects[item]["identity"] for item in record["subject_ids"]] == [{"path": "app.py"}]
    assert [subjects[item]["version_digest"] for item in record["subject_ids"]] == [
        verified_subject["version_digest"]
    ]

    task_path.write_text(
        replace_section(
            task_path.read_text(encoding="utf-8"),
            "Verification",
            "- Command: focused check\n- Result: pass\n",
        ),
        encoding="utf-8",
    )
    assert main(["task", "finish", task_id, "--json"]) == 0
    finished = json.loads(capsys.readouterr().out)
    receipt = json.loads((tmp_path / finished["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    assert validate_completion_outcome(receipt["discovery_outcome"]) == receipt["discovery_outcome"]


def test_completion_outcome_preserves_each_exact_verified_file_version(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    task_id = "T-20260609184046Z"
    task_path = add_board_task(
        tmp_path,
        f"{task_id}--alpha.md",
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"'),
    )
    evidence = tmp_path / "focused-check.log"
    evidence.write_text("PASS app.py v1\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    target = require_repo_target(tmp_path, repo_id="main")

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "update app",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    outcome_path = tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json"
    version_one = json.loads(outcome_path.read_text(encoding="utf-8"))["active_chosen"][0]["version_digest"]

    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    missing = structured_verification_coverage(
        tmp_path,
        task_id=task_id,
        target=target,
        subject_refs=["app.py"],
    )
    assert missing["status"] == "missing"

    evidence.write_text("PASS app.py v2\n", encoding="utf-8")
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    version_two = json.loads(outcome_path.read_text(encoding="utf-8"))["active_chosen"][0]["version_digest"]
    assert version_two != version_one

    covered = structured_verification_coverage(
        tmp_path,
        task_id=task_id,
        target=target,
        subject_refs=["app.py"],
    )
    assert covered["status"] == "complete"
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--reviewed",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    task_path.write_text(
        replace_section(
            task_path.read_text(encoding="utf-8"),
            "Verification",
            "- Command: focused checks for V1 and V2\n- Result: pass\n",
        ),
        encoding="utf-8",
    )
    assert main(["task", "finish", task_id, "--json"]) == 0
    finished = json.loads(capsys.readouterr().out)
    receipt = json.loads((tmp_path / finished["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    outcome = receipt["discovery_outcome"]
    assert validate_completion_outcome(outcome) == outcome
    subjects = {item["id"]: item for item in outcome["subjects"]}
    verified_versions = {
        tuple(subjects[item]["version_digest"] for item in record["subject_ids"])
        for record in outcome["verification_records"]
    }
    assert verified_versions == {(version_one,), (version_two,)}


def test_verification_alias_rejects_multiple_citation_versions_without_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    task_id = "T-20260609184046Z"
    add_board_task(
        tmp_path,
        f"{task_id}--alpha.md",
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"'),
    )
    evidence = tmp_path / "focused-check.log"
    evidence.write_text("PASS exact citation V2\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    target = require_repo_target(tmp_path, repo_id="main")

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    for version in (1, 2):
        if version == 2:
            (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
        result_id = digest_data({"context": f"citation version {version}"})
        write_result_receipt(
            tmp_path,
            target=target,
            producer=ResultProducer.CONTEXT,
            result_id=result_id,
            request=ContextResultRequest(query=f"citation version {version}", mode="auto"),
            selections=[ResultSelection(ResultAuthority.SOURCE, "repos/app.py")],
        )
        assert main(
            [
                "task",
                "discovery",
                "add",
                task_id,
                "--result-producer",
                "context",
                "--result-id",
                result_id,
                "--result-authority",
                "source",
                "--result-ref",
                "repos/app.py",
                "--json",
            ]
        ) == 0
        capsys.readouterr()

    outcome_path = tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json"
    state = json.loads(outcome_path.read_text(encoding="utf-8"))
    version_one = state["prior_episodes"][0]["citations"][0]["member"]["subject"]
    version_two = state["active_episode"]["citations"][0]["member"]["subject"]
    assert version_one["subject_id"] != version_two["subject_id"]
    before = outcome_path.read_bytes()

    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 2
    ambiguous = json.loads(capsys.readouterr().out)
    assert ambiguous["problems"][0]["code"] == "verification_subject_ambiguous"
    assert outcome_path.read_bytes() == before

    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            version_two["subject_id"],
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    recorded = json.loads(outcome_path.read_text(encoding="utf-8"))
    assert recorded["verification_records"][0]["subject_ids"] == [version_two["subject_id"]]
    assert [item["subject_id"] for item in recorded["verification_subjects"]] == [version_two["subject_id"]]


def _recorded_verification_state(tmp_path: Path, monkeypatch, capsys) -> tuple[str, Path, Path]:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(
        repo,
        {
            "app.py": "value = 1\n",
            "other.py": "value = 1\n",
        },
    )
    task_id = "T-20260609184046Z"
    add_board_task(
        tmp_path,
        f"{task_id}--alpha.md",
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"'),
    )
    evidence = tmp_path / "focused-check.log"
    evidence.write_text("PASS app.py\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    target = require_repo_target(tmp_path, repo_id="main")
    result_id = digest_data({"context": "recorded verification state"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
        request=ContextResultRequest(query="inspect app", mode="auto"),
        selections=[ResultSelection(ResultAuthority.SOURCE, "repos/app.py")],
    )

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "inspect app",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
            "--result-producer",
            "context",
            "--result-id",
            result_id,
            "--result-authority",
            "source",
            "--result-ref",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--reviewed",
            "repos/other.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    outcome_path = tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json"
    return task_id, outcome_path, evidence


def _write_outcome_state(path: Path, state: dict) -> None:
    basis = {key: value for key, value in state.items() if key != "state_digest"}
    state["state_digest"] = digest_data(basis)
    path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")


@pytest.mark.parametrize(
    "field_path",
    [
        pytest.param(("active_chosen", 0, "kind"), id="subject-kind"),
        pytest.param(("active_episode", "citations", 0, "producer"), id="citation-producer"),
        pytest.param(("active_episode", "citations", 0, "member", "authority"), id="citation-authority"),
        pytest.param(("verification_records", 0, "status"), id="record-status"),
        pytest.param(("verification_records", 0, "evidence", "kind"), id="evidence-kind"),
    ],
)
def test_malformed_outcome_field_types_fail_typed_without_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
    field_path: tuple[str | int, ...],
) -> None:
    task_id, outcome_path, _evidence = _recorded_verification_state(tmp_path, monkeypatch, capsys)
    state = json.loads(outcome_path.read_text(encoding="utf-8"))
    target = state
    for part in field_path[:-1]:
        target = target[part]
    target[field_path[-1]] = []
    _write_outcome_state(outcome_path, state)
    before = outcome_path.read_bytes()

    with pytest.raises(RepoctlError) as caught:
        load_outcome_state(tmp_path, task_id)

    assert caught.value.code == "discovery_outcome_state_invalid"
    assert outcome_path.read_bytes() == before


def test_verification_subject_pool_requires_exact_record_closure(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, outcome_path, _evidence = _recorded_verification_state(tmp_path, monkeypatch, capsys)
    state = json.loads(outcome_path.read_text(encoding="utf-8"))
    state["verification_subjects"] = []
    _write_outcome_state(outcome_path, state)
    missing_before = outcome_path.read_bytes()

    with pytest.raises(RepoctlError) as missing:
        load_outcome_state(tmp_path, task_id)

    assert missing.value.code == "discovery_outcome_verification_reference_invalid"
    assert outcome_path.read_bytes() == missing_before

    state = json.loads(missing_before)
    verified_ids = set(state["verification_records"][0]["subject_ids"])
    extra = next(
        item
        for item in state["active_episode"]["reviewed"]
        if item["subject_id"] not in verified_ids
    )
    state["verification_subjects"] = sorted(
        [state["active_chosen"][0], extra],
        key=lambda item: item["subject_id"],
    )
    _write_outcome_state(outcome_path, state)
    extra_before = outcome_path.read_bytes()

    with pytest.raises(RepoctlError) as extra_error:
        load_outcome_state(tmp_path, task_id)

    assert extra_error.value.code == "discovery_outcome_state_invalid"
    assert outcome_path.read_bytes() == extra_before


def test_invalid_v1_digest_precedes_orphan_reference_migration(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, outcome_path, _evidence = _recorded_verification_state(tmp_path, monkeypatch, capsys)
    state = json.loads(outcome_path.read_text(encoding="utf-8"))
    state.pop("verification_subjects")
    state["schema_version"] = 1
    basis = {key: value for key, value in state.items() if key != "state_digest"}
    state["state_digest"] = digest_data(basis)
    record = state["verification_records"][0]
    record["subject_ids"] = ["sha256:" + ("f" * 64)]
    record["record_id"] = digest_data(
        {key: value for key, value in record.items() if key != "record_id"}
    )
    outcome_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before = outcome_path.read_bytes()

    with pytest.raises(RepoctlError) as caught:
        load_outcome_state(tmp_path, task_id)

    assert caught.value.code == "discovery_outcome_state_invalid"
    assert outcome_path.read_bytes() == before


def test_mixed_valid_and_orphan_verification_references_fail_without_projection(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, outcome_path, _evidence = _recorded_verification_state(tmp_path, monkeypatch, capsys)
    state = json.loads(outcome_path.read_text(encoding="utf-8"))
    record = state["verification_records"][0]
    record["subject_ids"] = sorted([*record["subject_ids"], "sha256:" + ("f" * 64)])
    record["record_id"] = digest_data(
        {key: value for key, value in record.items() if key != "record_id"}
    )
    _write_outcome_state(outcome_path, state)
    before = outcome_path.read_bytes()

    with pytest.raises(RepoctlError) as caught:
        completion_outcome_projection(tmp_path, task_id)

    assert caught.value.code == "discovery_outcome_verification_reference_invalid"
    assert outcome_path.read_bytes() == before


@pytest.mark.parametrize("corruption", ["subject-capsule", "record-id"])
def test_forged_verification_capsules_and_record_ids_fail_typed(
    tmp_path: Path,
    monkeypatch,
    capsys,
    corruption: str,
) -> None:
    task_id, outcome_path, _evidence = _recorded_verification_state(tmp_path, monkeypatch, capsys)
    state = json.loads(outcome_path.read_text(encoding="utf-8"))
    if corruption == "subject-capsule":
        state["verification_subjects"][0]["subject_id"] = "sha256:" + ("e" * 64)
    else:
        state["verification_records"][0]["subject_ids"] = ["sha256:" + ("f" * 64)]
        state["verification_records"][0]["record_id"] = "sha256:" + ("e" * 64)
    _write_outcome_state(outcome_path, state)
    before = outcome_path.read_bytes()

    with pytest.raises(RepoctlError) as caught:
        load_outcome_state(tmp_path, task_id)

    assert caught.value.code == "discovery_outcome_state_invalid"
    assert outcome_path.read_bytes() == before


def test_duplicate_verification_add_keeps_exact_pool_and_record_cardinality(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, outcome_path, evidence = _recorded_verification_state(tmp_path, monkeypatch, capsys)
    before = outcome_path.read_bytes()
    initial = json.loads(before)

    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    repeated = json.loads(outcome_path.read_text(encoding="utf-8"))

    assert len(repeated["verification_subjects"]) == len(initial["verification_subjects"]) == 1
    assert len(repeated["verification_records"]) == len(initial["verification_records"]) == 1
    assert outcome_path.read_bytes() == before


def test_legacy_verification_subject_migrates_in_memory_without_rewriting_state(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    task_id = "T-20260609184046Z"
    add_board_task(
        tmp_path,
        f"{task_id}--alpha.md",
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"'),
    )
    evidence = tmp_path / "focused-check.log"
    evidence.write_text("PASS app.py v1\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "verify app",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    outcome_path = tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json"
    legacy = json.loads(outcome_path.read_text(encoding="utf-8"))
    version_one = legacy["verification_subjects"][0]["version_digest"]
    legacy.pop("verification_subjects")
    legacy["schema_version"] = 1
    legacy["state_digest"] = digest_data(
        {key: value for key, value in legacy.items() if key != "state_digest"}
    )
    outcome_path.write_text(json.dumps(legacy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before = outcome_path.read_bytes()
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")

    migrated = load_outcome_state(tmp_path, task_id)

    assert migrated is not None
    assert migrated["schema_version"] == 2
    assert [item["subject_id"] for item in migrated["verification_subjects"]] == migrated["verification_records"][0]["subject_ids"]
    assert migrated["verification_subjects"][0]["version_digest"] == version_one
    assert outcome_path.read_bytes() == before


def test_claim_only_verification_requires_a_frozen_citation_claim(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    task_id = "T-20260609184046Z"
    add_board_task(
        tmp_path,
        f"{task_id}--alpha.md",
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"'),
    )
    evidence = tmp_path / "focused-check.log"
    evidence.write_text("PASS cited claim\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    target = require_repo_target(tmp_path, repo_id="main")
    result_id = digest_data({"context": "claim-only verification"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
        request=ContextResultRequest(query="inspect app", mode="auto"),
        selections=[ResultSelection(ResultAuthority.SOURCE, "repos/app.py")],
    )

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "inspect app",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
            "--result-producer",
            "context",
            "--result-id",
            result_id,
            "--result-authority",
            "source",
            "--result-ref",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    outcome_path = tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json"
    state = json.loads(outcome_path.read_text(encoding="utf-8"))
    claim_id = state["active_episode"]["citations"][0]["member"]["claims"][0]["evidence_digest"]
    unknown_claim = "sha256:" + ("f" * 64)
    before_unknown = outcome_path.read_bytes()
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--claim-id",
            unknown_claim,
            "--json",
        ]
    ) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "verification_claim_unknown"
    assert outcome_path.read_bytes() == before_unknown

    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--claim-id",
            claim_id,
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    projection = completion_outcome_projection(tmp_path, task_id)
    assert projection is not None
    assert validate_completion_outcome(projection) == projection
    assert projection["verification_records"][0]["subject_ids"] == []
    assert projection["verification_records"][0]["claim_ids"] == [claim_id]

    legacy = json.loads(outcome_path.read_text(encoding="utf-8"))
    legacy.pop("verification_subjects")
    legacy["schema_version"] = 1
    legacy_record = legacy["verification_records"][0]
    legacy_record["claim_ids"] = [unknown_claim]
    legacy_record["record_id"] = digest_data(
        {key: value for key, value in legacy_record.items() if key != "record_id"}
    )
    legacy["state_digest"] = digest_data(
        {key: value for key, value in legacy.items() if key != "state_digest"}
    )
    outcome_path.write_text(json.dumps(legacy, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    before_orphan = outcome_path.read_bytes()

    with pytest.raises(RepoctlError) as caught:
        load_outcome_state(tmp_path, task_id)

    assert caught.value.code == "discovery_outcome_verification_reference_invalid"
    assert outcome_path.read_bytes() == before_orphan


def test_task_chosen_projection_must_match_machine_outcome_before_resume_or_finish(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(
        repo,
        {
            "app.py": "value = 1\n",
            "other.py": "value = 1\n",
        },
    )
    task_id = "T-20260609184046Z"
    text = (
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    task_path = add_board_task(tmp_path, f"{task_id}--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "update app",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    drifted = replace_section(
        task_path.read_text(encoding="utf-8"),
        "Discovery",
        "- Candidate query: `update app`\n"
        "- Candidate files reviewed: `repos/app.py`\n"
        "- Chosen files: `repos/other.py`\n",
    )
    drifted = replace_section(
        drifted,
        "Verification",
        "- Command: focused check\n- Result: pass\n",
    )
    task_path.write_text(drifted, encoding="utf-8")
    (repo / "other.py").write_text("value = 2\n", encoding="utf-8")

    assert main(["check", "--json"]) == 1
    checked = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "discovery_outcome_chosen_mismatch" for problem in checked["problems"])

    assert main(["task", "resume", "--json"]) == 1
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["data"]["resume_guidance"]["health"]["status"] == "unhealthy"
    assert resumed["data"]["resume_guidance"]["executable_handoff"] is None
    assert any(problem["code"] == "discovery_outcome_chosen_mismatch" for problem in resumed["problems"])

    assert main(["task", "doctor", task_id, "--json"]) == 1
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["data"]["finish_ready"] is False
    assert doctor["data"]["structured_verification"]["status"] == "scope_mismatch"
    assert doctor["data"]["discovery_outcome_alignment"] == {
        "status": "mismatch",
        "reason_codes": ["discovery_outcome_chosen_mismatch"],
        "task_chosen_paths": ["other.py"],
        "outcome_chosen_paths": ["app.py"],
        "task_only_paths": ["other.py"],
        "outcome_only_paths": ["app.py"],
        "invalid_task_chosen_values": [],
        "invalid_outcome_subject_ids": [],
    }
    reconcile = next(
        action
        for action in doctor["next_actions"]
        if action["label"].startswith("Reconcile the approved Chosen scope")
    )
    assert "--replace-chosen" in reconcile["command"]

    assert main(["task", "finish", task_id, "--json"]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "discovery_outcome_chosen_mismatch"
    assert task_path.is_file()
    assert not (tmp_path / f"docs/tasks/.repoctl-state/completions/{task_id}.json").exists()

    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--replace-chosen",
            "repos/other.py",
            "--reason",
            "reconcile approved scope after direct Task projection edit",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(["task", "finish", task_id, "--json"]) == 0
    finished = json.loads(capsys.readouterr().out)
    receipt = json.loads((tmp_path / finished["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    subjects = {item["id"]: item for item in receipt["discovery_outcome"]["subjects"]}
    assert [
        subjects[subject_id]["identity"]["path"]
        for subject_id in receipt["discovery_outcome"]["active_chosen"]
    ] == ["other.py"]
    assert receipt["repo_evidence"]["delta"]["scope"]["chosen_paths"] == ["other.py"]


def test_backticked_placeholder_named_root_path_is_not_erased_from_chosen_alignment(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    task_path = add_board_task(tmp_path, f"{task_id}--coordination.md", task_text(task_id, status="todo"))
    artifact = tmp_path / "todo"
    artifact.write_text("review result\n", encoding="utf-8")
    evidence = tmp_path / "artifact-check.log"
    evidence.write_text("PASS todo\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--artifact",
            "todo",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    task_path.write_text(
        replace_section(
            task_path.read_text(encoding="utf-8"),
            "Discovery",
            "- Candidate query: none yet\n"
            "- Candidate files reviewed: none yet\n"
            "- Chosen files: `todo`\n",
        ),
        encoding="utf-8",
    )

    assert main(["task", "doctor", task_id, "--json"]) == 1
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["data"]["discovery_outcome_alignment"] == {
        "status": "mismatch",
        "reason_codes": ["discovery_outcome_chosen_mismatch"],
        "task_chosen_paths": ["todo"],
        "outcome_chosen_paths": [],
        "task_only_paths": ["todo"],
        "outcome_only_paths": [],
        "invalid_task_chosen_values": [],
        "invalid_outcome_subject_ids": [],
    }


def test_invalid_explicit_root_chosen_is_not_silently_erased_from_alignment(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    task_path = add_board_task(tmp_path, f"{task_id}--coordination.md", task_text(task_id, status="todo"))
    artifact = tmp_path / "docs/reviews/ceo-review.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("CEO review: PASS\n", encoding="utf-8")
    evidence = tmp_path / "ceo-review-evidence.log"
    evidence.write_text("PASS docs/reviews/ceo-review.md\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--artifact",
            "docs/reviews/ceo-review.md",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    invalid_values = ["../outside", "/absolute", "bad\\path", "."]
    task_text_with_invalid_chosen = replace_section(
        task_path.read_text(encoding="utf-8"),
        "Discovery",
        "- Candidate query: none yet\n"
        "- Candidate files reviewed: none yet\n"
        "- Chosen files:\n"
        + "".join(f"  - `{value}`\n" for value in invalid_values),
    )
    task_path.write_text(
        replace_section(
            task_text_with_invalid_chosen,
            "Verification",
            "- Command: CEO review\n- Result: pass\n",
        ),
        encoding="utf-8",
    )

    assert main(["task", "doctor", task_id, "--json"]) == 1
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["data"]["finish_ready"] is False
    assert doctor["data"]["structured_verification"]["status"] == "scope_mismatch"
    assert doctor["data"]["discovery_outcome_alignment"] == {
        "status": "mismatch",
        "reason_codes": ["discovery_task_chosen_invalid"],
        "task_chosen_paths": [],
        "outcome_chosen_paths": [],
        "task_only_paths": [],
        "outcome_only_paths": [],
        "invalid_task_chosen_values": sorted(invalid_values),
        "invalid_outcome_subject_ids": [],
    }

    assert main(["task", "finish", task_id, "--json"]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "discovery_task_chosen_invalid"
    assert task_path.is_file()
    assert not (tmp_path / f"docs/tasks/.repoctl-state/completions/{task_id}.json").exists()


def test_task_doctor_does_not_offer_an_ineffective_pass_append_for_nonpassing_current_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    task_id = "T-20260609184046Z"
    text = (
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    task_path = add_board_task(tmp_path, f"{task_id}--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "update app",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    task_path.write_text(
        replace_section(task_path.read_text(encoding="utf-8"), "Verification", "- Command: focused check\n- Result: failed then passed\n"),
        encoding="utf-8",
    )
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
    evidence = tmp_path / "focused-check.log"
    evidence.write_text("FAIL app.py\n", encoding="utf-8")
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "failed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()

    assert main(["task", "doctor", task_id, "--json"]) == 0
    failed = json.loads(capsys.readouterr().out)
    assert failed["data"]["structured_verification"]["status"] == "nonpassing"
    assert failed["data"]["action_inputs"]["nonpassing_structured_verification_subjects"] == ["app.py"]
    assert not any(action.get("kind") == "task_verification_add" for action in failed["next_actions"])
    review = next(action for action in failed["next_actions"] if action.get("target_ref") == "data.action_inputs.nonpassing_structured_verification_subjects")
    assert review["path"] == task_path.relative_to(tmp_path).as_posix()

    evidence.write_text("PASS app.py\n", encoding="utf-8")
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(["task", "doctor", task_id, "--json"]) == 0
    still_nonpassing = json.loads(capsys.readouterr().out)
    assert still_nonpassing["data"]["structured_verification"]["status"] == "nonpassing"
    assert not any(action.get("kind") == "task_verification_add" for action in still_nonpassing["next_actions"])


def test_task_finish_reports_missing_structured_verification_as_immutable_audit_warning(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    task_id = "T-20260609184046Z"
    text = (
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    task_path = add_board_task(tmp_path, f"{task_id}--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "update app",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    task_path.write_text(
        replace_section(task_path.read_text(encoding="utf-8"), "Verification", "- Command: focused check\n- Result: pass\n"),
        encoding="utf-8",
    )
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--json"]) == 0
    finished = json.loads(capsys.readouterr().out)
    assert finished["data"]["structured_verification"] == {
        "status": "missing",
        "required_subject_count": 1,
        "passed_subject_count": 0,
        "missing_subject_count": 1,
        "nonpassing_subject_count": 0,
        "unverified_subjects": ["app.py"],
        "unverified_subject_count": 1,
        "unverified_subjects_truncated": False,
    }
    warning = next(item for item in finished["warnings"] if item["code"] == "task_structured_verification_missing")
    assert warning["path"] == finished["data"]["new_path"]
    assert finished["next_actions"] == []


def test_task_show_and_doctor_recommend_decomposition_only_from_combined_structural_signals(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    files = {f"scope_{index:02d}.py": f"value = {index}\n" for index in range(21)}
    init_committed_product_repo(repo, files)
    task_id = "T-20260609184046Z"
    text = (
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    task_path = add_board_task(tmp_path, f"{task_id}--broad.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    chosen_args = [value for path in files for value in ("--chosen", f"repos/{path}")]
    for index in range(3):
        assert main(
            [
                "task",
                "discovery",
                "add",
                task_id,
                "--query",
                f"milestone {index}",
                "--reviewed",
                f"repos/scope_{index:02d}.py",
                *(chosen_args if index == 0 else []),
                "--json",
            ]
        ) == 0
        capsys.readouterr()
    evidence = tmp_path / "milestone-check.log"
    evidence.write_text("PASS repeated milestones\n", encoding="utf-8")
    for index in range(2):
        assert main(
            [
                "task",
                "verification",
                "add",
                task_id,
                "--status",
                "passed",
                "--evidence-ref",
                evidence.as_posix(),
                "--subject",
                f"scope_{index:02d}.py",
                "--json",
            ]
        ) == 0
        capsys.readouterr()

    expected = {
        "status": "recommended",
        "reason_codes": [
            "chosen_scope_exceeds_compact_window",
            "repeated_discovery_episodes",
            "multiple_structured_verification_records",
        ],
        "chosen_subject_count": 21,
        "discovery_episode_count": 3,
        "prior_discovery_episode_count": 2,
        "structured_verification_record_count": 2,
        "compact_path_limit": 20,
    }
    assert main(["task", "show", task_id, "--summary", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["data"]["decomposition_advisory"] == expected
    assert any(item["code"] == "task_decomposition_recommended" for item in shown["warnings"])
    action = next(item for item in shown["next_actions"] if item["label"].startswith("Review whether"))
    assert action == {
        "label": "Review whether the next independently verifiable milestone belongs in a new task",
        "path": task_path.relative_to(tmp_path).as_posix(),
    }

    assert main(["task", "doctor", task_id, "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["data"]["decomposition_advisory"] == expected
    assert "task_decomposition_recommended" in doctor["data"]["advisory"]
    assert any(item["code"] == "task_decomposition_recommended" for item in doctor["warnings"])


def test_workspace_task_records_typed_artifact_verification_without_product_discovery(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    add_board_task(tmp_path, f"{task_id}--coordination.md", task_text(task_id, status="todo"))
    artifact = tmp_path / "docs/reviews/ceo-review.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("CEO review: PASS\n", encoding="utf-8")
    evidence = tmp_path / "ceo-review-evidence.log"
    evidence.write_text("PASS docs/reviews/ceo-review.md\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()

    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--artifact",
            "docs/reviews/ceo-review.md",
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["record_count"] == 1
    state = json.loads(
        (tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json").read_text(encoding="utf-8")
    )
    assert state["repository"] is None
    assert state["active_chosen"] == []
    artifact_subject = state["active_episode"]["reviewed"][0]
    assert artifact_subject["kind"] == "artifact"
    assert artifact_subject["identity"] == {"path": "docs/reviews/ceo-review.md"}
    assert artifact_subject["version_digest"] == digest_data(
        {"kind": "artifact", "identity": {"path": "docs/reviews/ceo-review.md"}}
    )
    assert state["verification_records"][0]["subject_ids"] == [artifact_subject["subject_id"]]
    completion = completion_outcome_projection(tmp_path, task_id)
    assert completion is not None
    assert validate_completion_outcome(completion) == completion
    assert completion["repository"] is None
    assert completion["active_chosen"] == []

    product_artifact = tmp_path / "repos/product.py"
    product_artifact.parent.mkdir()
    product_artifact.write_text("value = 1\n", encoding="utf-8")
    product_link = tmp_path / "docs/reviews/product-link.py"
    product_link.symlink_to(product_artifact)
    outside_artifact = tmp_path.parent / f"{tmp_path.name}-outside-review.md"
    outside_artifact.write_text("outside\n", encoding="utf-8")
    outside_link = tmp_path / "docs/reviews/outside-link.md"
    outside_link.symlink_to(outside_artifact)
    for invalid_artifact in (
        "repos/product.py",
        product_artifact.as_posix(),
        "docs/reviews/../reviews/ceo-review.md",
        "docs/reviews/missing.md",
        "docs/reviews",
        "docs/reviews/product-link.py",
        "docs/reviews/outside-link.md",
    ):
        assert main(
            [
                "task",
                "verification",
                "add",
                task_id,
                "--status",
                "passed",
                "--evidence-ref",
                evidence.as_posix(),
                "--artifact",
                invalid_artifact,
                "--json",
            ]
        ) == 2
        rejected = json.loads(capsys.readouterr().out)
        assert rejected["problems"][0]["code"] == "workspace_verification_artifact_invalid"

    repo_task_id = "T-20260609184047Z"
    repo_task = (
        task_text(repo_task_id, status="doing")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    add_board_task(tmp_path, f"{repo_task_id}--product.md", repo_task)
    assert main(
        [
            "task",
            "verification",
            "add",
            repo_task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--artifact",
            "docs/reviews/ceo-review.md",
            "--json",
        ]
    ) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "workspace_verification_artifact_invalid"
    assert not (
        tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{repo_task_id}.json"
    ).exists()


def test_started_workspace_task_freezes_artifact_verification_into_a_current_completion_receipt(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    task_path = add_board_task(tmp_path, f"{task_id}--coordination.md", task_text(task_id, status="todo"))
    artifact = tmp_path / "docs/reviews/ceo-review.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("CEO review: PASS\n", encoding="utf-8")
    evidence = tmp_path / "ceo-review-evidence.log"
    evidence.write_text("PASS docs/reviews/ceo-review.md\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--artifact",
            "docs/reviews/ceo-review.md",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    task_path.write_text(
        replace_section(task_path.read_text(encoding="utf-8"), "Verification", "- Command: CEO review\n- Result: pass\n"),
        encoding="utf-8",
    )

    assert main(["task", "finish", task_id, "--json"]) == 0
    finished = json.loads(capsys.readouterr().out)
    receipt = json.loads((tmp_path / finished["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["schema_version"] == 4
    assert receipt["repo_id"] == ""
    assert receipt["discovery_outcome"]["repository"] is None
    assert receipt["discovery_outcome"]["active_chosen"] == []
    assert validate_completion_outcome(receipt["discovery_outcome"]) == receipt["discovery_outcome"]
    assert main(["check", "--json"]) == 0
    capsys.readouterr()


def test_workspace_artifact_outcome_rejects_legacy_task_before_mutation_without_current_start_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    text = replace_section(
        task_text(task_id, status="doing"),
        "Verification",
        "- Command: CEO review\n- Result: pass\n",
    )
    task_path = add_board_task(tmp_path, f"{task_id}--legacy-coordination.md", text)
    artifact = tmp_path / "docs/reviews/ceo-review.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("CEO review: PASS\n", encoding="utf-8")
    evidence = tmp_path / "ceo-review-evidence.log"
    evidence.write_text("PASS docs/reviews/ceo-review.md\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--artifact",
            "docs/reviews/ceo-review.md",
            "--json",
        ]
    ) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "transition_evidence_incomplete"
    assert task_path.is_file()
    assert not (tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json").exists()
    assert not (tmp_path / f"docs/tasks/.repoctl-state/completions/{task_id}.json").exists()


def test_workspace_artifact_outcome_rejects_todo_task_before_start_without_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    add_board_task(tmp_path, f"{task_id}--coordination.md", task_text(task_id, status="todo"))
    artifact = tmp_path / "docs/reviews/ceo-review.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("CEO review: PASS\n", encoding="utf-8")
    evidence = tmp_path / "ceo-review-evidence.log"
    evidence.write_text("PASS docs/reviews/ceo-review.md\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--artifact",
            "docs/reviews/ceo-review.md",
            "--json",
        ]
    ) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "transition_evidence_incomplete"
    assert not (tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json").exists()


def test_workspace_artifact_outcome_rejects_malformed_current_start_state_before_mutation(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    add_board_task(tmp_path, f"{task_id}--coordination.md", task_text(task_id, status="todo"))
    artifact = tmp_path / "docs/reviews/ceo-review.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("CEO review: PASS\n", encoding="utf-8")
    evidence = tmp_path / "ceo-review-evidence.log"
    evidence.write_text("PASS docs/reviews/ceo-review.md\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    state_path = tmp_path / f"docs/tasks/.repoctl-state/{task_id}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["initial"].pop("repositories")
    state_path.write_text(json.dumps(state, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--artifact",
            "docs/reviews/ceo-review.md",
            "--json",
        ]
    ) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "task_state_invalid"
    assert not (tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json").exists()


def test_workspace_artifact_outcome_rejects_repository_start_reclassified_as_root(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    init_committed_product_repo(tmp_path / "repos", {"app.py": "value = 1\n"})
    task_id = "T-20260609184046Z"
    text = (
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    task_path = add_board_task(tmp_path, f"{task_id}--product.md", text)
    artifact = tmp_path / "docs/reviews/ceo-review.md"
    artifact.parent.mkdir(parents=True)
    artifact.write_text("CEO review: PASS\n", encoding="utf-8")
    evidence = tmp_path / "ceo-review-evidence.log"
    evidence.write_text("PASS docs/reviews/ceo-review.md\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    reclassified = task_path.read_text(encoding="utf-8")
    reclassified = replace_frontmatter_line(reclassified, "area", '"docs"')
    reclassified = replace_frontmatter_line(reclassified, "repo_id", '""')
    task_path.write_text(reclassified, encoding="utf-8")

    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--artifact",
            "docs/reviews/ceo-review.md",
            "--json",
        ]
    ) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "transition_evidence_incomplete"
    assert not (tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json").exists()


def test_product_outcome_rejects_root_start_reclassified_as_repository(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    init_committed_product_repo(tmp_path / "repos", {"app.py": "value = 1\n"})
    task_id = "T-20260609184046Z"
    task_path = add_board_task(tmp_path, f"{task_id}--coordination.md", task_text(task_id, status="todo"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    reclassified = task_path.read_text(encoding="utf-8")
    reclassified = replace_frontmatter_line(reclassified, "area", '"repo"')
    reclassified = replace_frontmatter_line(reclassified, "repo_id", '"main"')
    task_path.write_text(reclassified, encoding="utf-8")

    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "update app",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "transition_evidence_incomplete"
    assert not (tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json").exists()


def test_legacy_live_product_task_rejects_discovery_before_mutation_without_current_start_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    init_committed_product_repo(tmp_path / "repos", {"app.py": "value = 1\n"})
    task_id = "T-20260609184046Z"
    text = (
        task_text(task_id, status="doing")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    task_path = add_board_task(tmp_path, f"{task_id}--legacy-product.md", text)
    task_before = task_path.read_bytes()
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "update app",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "transition_evidence_incomplete"
    assert task_path.read_bytes() == task_before
    assert not (tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json").exists()


def test_subject_verification_rejects_legacy_live_outcome_without_current_start_evidence(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    init_committed_product_repo(tmp_path / "repos", {"app.py": "value = 1\n"})
    task_id = "T-20260609184046Z"
    text = (
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    task_path = add_board_task(tmp_path, f"{task_id}--product.md", text)
    evidence = tmp_path / "focused-check.log"
    evidence.write_text("PASS app.py\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    # Discovery is intentionally recordable before a todo task starts.
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "update app",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    task_path.write_text(
        replace_frontmatter_line(task_path.read_text(encoding="utf-8"), "status", "doing"),
        encoding="utf-8",
    )
    outcome_path = tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json"
    outcome_before = outcome_path.read_bytes()

    assert main(
        [
            "task",
            "verification",
            "add",
            task_id,
            "--status",
            "passed",
            "--evidence-ref",
            evidence.as_posix(),
            "--subject",
            "app.py",
            "--json",
        ]
    ) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "transition_evidence_incomplete"
    assert outcome_path.read_bytes() == outcome_before


def test_task_show_exposes_complete_scope_action_inputs_when_summary_is_truncated(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    files = {"chosen.py": "value = 1\n", **{f"other_{index:02d}.py": "value = 1\n" for index in range(25)}}
    init_committed_product_repo(repo, files)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "chosen behavior", "--reviewed", "repos/chosen.py", "--chosen", "repos/chosen.py", "--json"]) == 0
    capsys.readouterr()
    for path in sorted(files):
        if path != "chosen.py":
            (repo / path).write_text("value = 2\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    scope = payload["data"]["repo_changes"]["scope"]
    assert scope["unchosen_actual_paths_count"] == 25
    assert len(scope["unchosen_actual_paths"]) == 20
    assert scope["unchosen_actual_paths_truncated"] is True
    action = next(action for action in payload["next_actions"] if action.get("kind") == "task_scope_review")
    assert action["source"] == "data.action_inputs.unchosen_actual_paths"
    assert action["target_ref"] == "data.action_inputs.unchosen_actual_paths"
    assert "targets" not in action
    assert len(payload["data"]["action_inputs"]["unchosen_actual_paths"]) == 25


def test_task_start_and_summary_bound_large_path_collections(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    files = {f"file_{index:02d}.py": "value = 1\n" for index in range(25)}
    init_committed_product_repo(repo, files)
    for path in files:
        (repo / path).write_text("value = 2\n", encoding="utf-8")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert start_payload["data"]["dirty_count"] == 25
    assert len(start_payload["data"]["dirty"]) == 20
    assert start_payload["data"]["dirty_truncated"] is True
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "dirty_count=25" in task_body
    assert "docs/tasks/.repoctl-state/T-20260609184046Z.json" in task_body
    assert "file_00.py" not in task_body
    assert "... truncated" not in task_body
    state = json.loads((tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json").read_text(encoding="utf-8"))
    assert len(state["initial"]["dirty_entries"]) == 25

    for path in files:
        (repo / path).write_text("value = 3\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    summary = payload["data"]["repo_changes"]
    assert summary["task_new"] == 25
    assert len(summary["task_new_files"]) == 20
    assert summary["task_new_files_truncated"] is True
    assert summary["baseline_conflict_count"] == 25
    assert len(summary["baseline_conflicts"]) == 20
    assert summary["baseline_conflicts_truncated"] is True
    action = next(action for action in payload["next_actions"] if action.get("kind") == "baseline_ownership_resolution")
    assert action["source"] == "data.action_inputs.baseline_conflicts"
    assert action["target_ref"] == "data.action_inputs.baseline_conflicts"
    assert "targets" not in action
    assert len(payload["data"]["action_inputs"]["baseline_conflicts"]) == 25

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    full_summary = json.loads(capsys.readouterr().out)["data"]["repo_changes"]
    assert full_summary["baseline_conflict_count"] == 25
    assert len(full_summary["baseline_conflicts"]) == 25
    assert full_summary["baseline_conflicts_truncated"] is False


def test_task_start_types_unborn_repository_observation(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    summary = json.loads(capsys.readouterr().out)["data"]["repo_changes"]
    assert summary["repo_head_state"] == "unborn"
    assert summary["observed_since_baseline"] == "observed"
    assert "repo_head" not in summary
    assert "baseline_available" not in summary


def test_task_doctor_builds_one_typed_batch_action_for_all_baseline_conflicts(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"a.py": "a = 1\n", "b.py": "b = 1\n"})
    (repo / "a.py").write_text("a = 2\n", encoding="utf-8")
    (repo / "b.py").write_text("b = 2\n", encoding="utf-8")
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
        .replace("- pending", "- Command: pytest\n- Result: pass")
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--query",
            "two file change",
            "--reviewed",
            "repos/a.py",
            "--reviewed",
            "repos/b.py",
            "--chosen",
            "repos/a.py",
            "--chosen",
            "repos/b.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    (repo / "a.py").write_text("a = 3\n", encoding="utf-8")
    (repo / "b.py").write_text("b = 3\n", encoding="utf-8")

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    action = next(action for action in payload["next_actions"] if action.get("kind") == "baseline_ownership_resolution")
    assert action["source"] == "data.action_inputs.baseline_conflicts"
    assert action["choices"] == ["task", "preexisting"]
    assert action["target_ref"] == "data.action_inputs.baseline_conflicts"
    assert "targets" not in action
    assert payload["data"]["action_inputs"]["baseline_conflicts"] == ["a.py", "b.py"]
    command = shlex.split(action["command"])
    resolutions = [command[index + 1] for index, token in enumerate(command) if token == "--resolution"]
    assert resolutions == ["<path>=<task|preexisting>"]
    assert "--preview" in command
    assert "--ownership" not in command

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2
    finish_payload = json.loads(capsys.readouterr().out)
    assert finish_payload["problems"][0]["code"] == "baseline_conflict"
    finish_action = next(
        action for action in finish_payload["next_actions"] if action.get("kind") == "baseline_ownership_resolution"
    )
    assert finish_action == action
    assert finish_payload["data"]["action_inputs"]["baseline_conflicts"] == ["a.py", "b.py"]




def test_task_discovery_add_records_structured_scope_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--query",
            "checkout retry behavior",
            "--reviewed",
            "repos/src/checkout.py",
            "--reviewed",
            "repos/tests/test_checkout.py",
            "--chosen",
            "repos/src/checkout.py",
            "--note",
            "retry behavior lives in checkout service",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "task.discovery.add"
    assert payload["data"]["update"]["reviewed_files"]["added"] == ["repos/src/checkout.py", "repos/tests/test_checkout.py"]
    assert payload["data"]["update"]["chosen_files"]["added"] == ["repos/src/checkout.py"]
    assert payload["data"]["totals"]["chosen_file_count"] == 1
    assert "discovery" not in payload["data"]
    assert not any(action["label"] == "Find likely product files" for action in payload["next_actions"])
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "- Candidate query: `checkout retry behavior`" in task_body
    assert "  - `repos/tests/test_checkout.py`" in task_body
    assert "- Notes: `retry behavior lives in checkout service`" in task_body

    assert main(["check", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert not any(warning["code"] == "missing_discovery_evidence" for warning in check_payload["warnings"])


def test_task_discovery_markdown_and_outcome_state_commit_atomically(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    task_path = tmp_path / "docs/tasks/T-20260609184046Z--alpha.md"
    original_task = task_path.read_bytes()
    outcome_path = tmp_path / "docs/tasks/.repoctl-state/discovery-outcomes/T-20260609184046Z.json"
    real_atomic_write = __import__("tools.repoctl.cli", fromlist=["atomic_write"]).atomic_write

    def fail_task_write(path: Path, value: str) -> None:
        if path == task_path:
            raise OSError("simulated task Markdown write failure")
        real_atomic_write(path, value)

    monkeypatch.setattr("tools.repoctl.cli.atomic_write", fail_task_write)
    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--query",
            "checkout retry behavior",
            "--reviewed",
            "repos/src/checkout.py",
            "--chosen",
            "repos/src/checkout.py",
            "--json",
        ]
    ) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert task_path.read_bytes() == original_task
    assert not outcome_path.exists()


def test_task_discovery_starts_a_new_episode_and_replaces_active_chosen_with_reason(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "q1", "--reviewed", "repos/a.py", "--chosen", "repos/a.py", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "q2", "--reviewed", "repos/b.py", "--json"]) == 0
    query_update = json.loads(capsys.readouterr().out)
    assert query_update["data"]["update"]["chosen_files"] == {
        "mode": "unchanged",
        "added": [],
        "removed": [],
        "already_present": [],
    }
    assert "discovery" not in query_update["data"]
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--replace-chosen", "repos/b.py", "--json"]) == 2
    missing_reason = json.loads(capsys.readouterr().out)
    assert missing_reason["problems"][0]["code"] == "missing_scope_change_reason"

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--replace-chosen", "repos/b.py", "--reason", "implementation moved", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["discovery"]["candidate_query_history"] == ["q2"]
    assert payload["data"]["discovery"]["candidate_files_reviewed"] == ["repos/b.py"]
    assert payload["data"]["discovery"]["chosen_files"] == ["repos/b.py"]
    assert payload["data"]["update"]["chosen_files"] == {
        "mode": "replace",
        "added": ["repos/b.py"],
        "removed": ["repos/a.py"],
        "already_present": [],
    }
    assert all("context pack" not in action["command"] for action in payload["next_actions"])
    assert any(action["command"] == "./scripts/repoctl task doctor T-20260609184046Z --json" for action in payload["next_actions"])
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "scope changed: removed repos/a.py; added repos/b.py; reason=implementation moved" in task_body


def test_task_discovery_accepts_only_receipt_backed_selected_result_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    result_id = digest_data({"context": "selected source"})

    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--result-producer",
            "context",
            "--result-id",
            result_id,
            "--result-authority",
            "source",
            "--result-ref",
            "repos/lib/client.dart",
            "--json",
        ]
    ) == 2
    missing = json.loads(capsys.readouterr().out)
    assert missing["problems"][0]["code"] == "result_receipt_missing"

    target = require_repo_target(tmp_path, repo_id="main")
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=result_id,
        request=ContextResultRequest(query="selected source", mode="auto"),
        selections=[
            ResultSelection(ResultAuthority.SOURCE, "repos/lib/Client.dart"),
            ResultSelection(ResultAuthority.SOURCE, "repos/lib/client.dart"),
        ],
    )

    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--result-producer",
            "context",
            "--result-id",
            result_id,
            "--result-authority",
            "source",
            "--result-ref",
            "repos/lib/Client.dart",
            "--result-ref",
            "repos/lib/client.dart",
            "--full",
            "--json",
        ]
    ) == 0
    payload = json.loads(capsys.readouterr().out)
    selected = payload["data"]["discovery"]["selected_result_evidence"]
    assert len(selected) == 2
    assert {item["ref"] for item in selected} == {
        "repos/lib/Client.dart",
        "repos/lib/client.dart",
    }
    assert all(
        item
        | {
            "episode_id": "<episode>",
            "ref": "<case-sensitive-ref>",
        }
        == {
            "schema_version": 2,
            "producer": "context",
            "result_id": result_id,
            "episode_id": "<episode>",
            "request": {"kind": "context_query", "query": "selected source", "mode": "auto"},
            "authority": "source",
            "ref": "<case-sensitive-ref>",
        }
        for item in selected
    )
    assert len({item["episode_id"] for item in selected}) == 1
    assert selected[0]["episode_id"].startswith("sha256:")
    reloaded = task_discovery_result_selections(resolve_task(tmp_path, "T-20260609184046Z"))
    assert {item.ref for item in reloaded} == {
        "repos/lib/Client.dart",
        "repos/lib/client.dart",
    }
    body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "Selected result evidence" in body
    assert '"authority":"source"' in body

    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--result-producer",
            "graph",
            "--result-id",
            result_id,
            "--json",
        ]
    ) == 2
    failure = json.loads(capsys.readouterr().out)
    assert failure["problems"][0]["code"] == "incomplete_discovery_result_evidence"


def test_task_discovery_reads_legacy_result_evidence_without_inventing_request_ownership() -> None:
    legacy_text = json.dumps(
        {
            "producer": "context",
            "result_id": "sha256:" + ("1" * 64),
            "authority": "source",
            "ref": "repos/legacy.py",
        },
        separators=(",", ":"),
        sort_keys=True,
    )

    selection = DiscoveryResultSelection.from_text(legacy_text)

    assert selection.schema_version == 1
    assert selection.episode_id == ""
    assert selection.request is None
    assert selection.to_text() == legacy_text


def test_task_discovery_result_evidence_round_trips_markdown_sensitive_refs() -> None:
    selection = DiscoveryResultSelection(
        producer=ResultProducer.CONTEXT,
        result_id="sha256:" + ("1" * 64),
        episode_id="sha256:" + ("2" * 64),
        request={"kind": "context_query", "query": "owner`file.py", "mode": "auto"},
        authority=ResultAuthority.SOURCE,
        ref="repos/owner`file.py",
    )

    encoded = selection.to_text()

    assert "`" not in encoded
    assert DiscoveryResultSelection.from_text(f"`{encoded}`") == selection


def test_task_discovery_uses_context_receipt_as_episode_owner_and_accumulates_graph_followups(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    task_id = "T-20260609184046Z"
    text = task_text(task_id, status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, f"{task_id}--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    target = require_repo_target(tmp_path, repo_id="main")

    def add_receipt_result(
        producer: ResultProducer,
        result_id: str,
        authority: ResultAuthority,
        ref: str,
        *,
        query: str = "",
        note: str = "",
    ) -> tuple[int, dict[str, object]]:
        args = [
            "task", "discovery", "add", task_id,
            "--result-producer", producer.value,
            "--result-id", result_id,
            "--result-authority", authority.value,
            "--result-ref", ref,
            "--full", "--json",
        ]
        if query:
            args.extend(["--query", query])
        if note:
            args.extend(["--note", note])
        code = main(args)
        return code, json.loads(capsys.readouterr().out)

    context_q1_id = digest_data({"context": "q1"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=context_q1_id,
        request=ContextResultRequest(query="q1", mode="auto"),
        selections=[ResultSelection(ResultAuthority.SOURCE, "repos/q1.py")],
    )
    code, q1 = add_receipt_result(ResultProducer.CONTEXT, context_q1_id, ResultAuthority.SOURCE, "repos/q1.py", note="context q1")
    assert code == 0
    context_episode_id = q1["data"]["discovery"]["selected_result_evidence"][0]["episode_id"]

    graph_requests = [
        GraphResultRequest.from_query({"type": "symbol", "symbol": "owner"}),
        GraphResultRequest.from_query({"type": "callers_of", "symbol": "owner", "in_file": "src/service.py"}),
    ]
    for index, request in enumerate(graph_requests, start=1):
        graph_id = digest_data({"graph": index})
        write_result_receipt(
            tmp_path,
            target=target,
            producer=ResultProducer.GRAPH,
            result_id=graph_id,
            request=request,
            selections=[ResultSelection(ResultAuthority.GRAPH, f"graph-ref-{index}")],
        )
        code, graph_payload = add_receipt_result(
            ResultProducer.GRAPH,
            graph_id,
            ResultAuthority.GRAPH,
            f"graph-ref-{index}",
            note=f"graph follow-up {index}",
        )
        assert code == 0

    discovery = graph_payload["data"]["discovery"]
    assert discovery["candidate_query_history"] == ["q1"]
    assert discovery["notes"] == ["context q1", "graph follow-up 1", "graph follow-up 2"]
    assert len(discovery["selected_result_evidence"]) == 3
    assert {item["episode_id"] for item in discovery["selected_result_evidence"]} == {context_episode_id}

    context_q1_impact_id = digest_data({"context": "q1-file-impact"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=context_q1_impact_id,
        request=ContextResultRequest(query="q1", mode="file_impact"),
        selections=[ResultSelection(ResultAuthority.SOURCE, "repos/q1-impact.py")],
    )
    code, q1_impact = add_receipt_result(
        ResultProducer.CONTEXT,
        context_q1_impact_id,
        ResultAuthority.SOURCE,
        "repos/q1-impact.py",
        note="context q1 file impact",
    )
    assert code == 0
    q1_impact_discovery = q1_impact["data"]["discovery"]
    assert q1_impact_discovery["candidate_query_history"] == ["q1"]
    assert len(q1_impact_discovery["selected_result_evidence"]) == 4
    assert {item["episode_id"] for item in q1_impact_discovery["selected_result_evidence"]} == {context_episode_id}
    assert {
        item["request"]["mode"]
        for item in q1_impact_discovery["selected_result_evidence"]
        if item["producer"] == "context"
    } == {"auto", "file_impact"}

    context_q2_id = digest_data({"context": "q2"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=context_q2_id,
        request=ContextResultRequest(query="q2", mode="code_location"),
        selections=[ResultSelection(ResultAuthority.SOURCE, "repos/q2.py")],
    )
    code, mismatch = add_receipt_result(
        ResultProducer.CONTEXT,
        context_q2_id,
        ResultAuthority.SOURCE,
        "repos/q2.py",
        query="q1",
    )
    assert code == 2
    assert mismatch["problems"][0]["code"] == "discovery_result_episode_mismatch"

    code, q2 = add_receipt_result(ResultProducer.CONTEXT, context_q2_id, ResultAuthority.SOURCE, "repos/q2.py", note="context q2")
    assert code == 0
    assert q2["data"]["discovery"]["candidate_query_history"] == ["q2"]
    assert q2["data"]["discovery"]["notes"] == ["context q2"]
    assert [item["result_id"] for item in q2["data"]["discovery"]["selected_result_evidence"]] == [context_q2_id]


def test_task_discovery_seeds_graph_only_episode_from_selector_and_context_adopts_exact_query(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    task_id = "T-20260609184046Z"
    text = task_text(task_id, status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, f"{task_id}--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    target = require_repo_target(tmp_path, repo_id="main")

    graph_id = digest_data({"graph": "graph-only"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.GRAPH,
        result_id=graph_id,
        request=GraphResultRequest.from_query({"type": "callers_of", "symbol": "resolve_owner", "in_file": "src/service.py"}),
        selections=[ResultSelection(ResultAuthority.GRAPH, "resolve_owner")],
    )
    assert main(
        [
            "task", "discovery", "add", task_id,
            "--result-producer", "graph",
            "--result-id", graph_id,
            "--result-authority", "graph",
            "--result-ref", "resolve_owner",
            "--note", "graph-only seed",
            "--full", "--json",
        ]
    ) == 0
    graph_discovery = json.loads(capsys.readouterr().out)["data"]["discovery"]
    assert graph_discovery["candidate_query_history"] == ["resolve_owner"]
    graph_episode_id = graph_discovery["selected_result_evidence"][0]["episode_id"]

    context_id = digest_data({"context": "adopt graph-only"})
    write_result_receipt(
        tmp_path,
        target=target,
        producer=ResultProducer.CONTEXT,
        result_id=context_id,
        request=ContextResultRequest(query="resolve_owner", mode="auto"),
        selections=[ResultSelection(ResultAuthority.SOURCE, "repos/service.py")],
    )
    assert main(
        [
            "task", "discovery", "add", task_id,
            "--result-producer", "context",
            "--result-id", context_id,
            "--result-authority", "source",
            "--result-ref", "repos/service.py",
            "--note", "context owner adopted",
            "--full", "--json",
        ]
    ) == 0
    adopted = json.loads(capsys.readouterr().out)["data"]["discovery"]
    assert adopted["candidate_query_history"] == ["resolve_owner"]
    assert adopted["notes"] == ["graph-only seed", "context owner adopted"]
    assert len(adopted["selected_result_evidence"]) == 2
    adopted_episode_ids = {item["episode_id"] for item in adopted["selected_result_evidence"]}
    assert len(adopted_episode_ids) == 1
    assert graph_episode_id not in adopted_episode_ids


def test_task_discovery_query_returns_compact_context_next_action(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "TokenFlow.validate callers", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["next_actions"] == [
        {
            "label": "Find likely product files",
            "command": "./scripts/repoctl context query 'TokenFlow.validate callers' --repo-id main --json",
        }
    ]


def test_task_discovery_rejects_existing_directories_but_allows_future_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "src").mkdir()
    (repo / "existing.py").write_text("value = 1\n", encoding="utf-8")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "future file", "--reviewed", "repos/src", "--chosen", "repos/new.py", "--json"]) == 2
    reviewed_error = json.loads(capsys.readouterr().out)
    assert reviewed_error["problems"][0]["code"] == "discovery_path_is_directory"

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "future file", "--reviewed", "repos/existing.py", "--chosen", "repos/new.py", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["update"]["chosen_files"]["added"] == ["repos/new.py"]


def test_task_create_print_id_and_root_work_area(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "root-note", "Root Note", "--print-id"]) == 0

    output = capsys.readouterr().out.strip()
    assert output.startswith("T-")
    task_path = next((tmp_path / "docs/tasks").glob(f"{output}--root-note.md"))
    text = task_path.read_text(encoding="utf-8")
    assert "- Product repository: none selected" in text
    assert "Repository: `repos/`" not in text
    assert "Do not touch product files under `repos/`" in text


def test_repo_scoped_task_start_reports_structured_discovery_next_action(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    text = task_text("T-20260609184046Z").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    commands = [action.get("command", "") for action in payload["next_actions"]]
    assert any("task discovery add T-20260609184046Z" in command for command in commands)
    assert any("context query '<query>' --repo-id main" in command for command in commands)


def test_task_start_blocks_repo_scoped_task_without_repo_git(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "repos").mkdir()
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"
    assert "status: todo" in (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")


def test_task_start_fails_on_dirty_repo_by_default_for_repo_scoped_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_dirty"
    assert "status: todo" in (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")


def test_task_start_records_dirty_repo_for_root_task_without_force(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "docs"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["status"] == "doing"
    assert payload["data"]["repo_changes"]["preexisting_dirty"] == 1
    assert payload["data"]["repo_changes"]["task_new"] == 0
    assert payload["warnings"][0]["code"] == "root_task_repo_dirty_recorded"
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "dirty repo state recorded" in task_body
    assert (tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json").is_file()


def test_root_task_records_readable_baseline_for_unbound_collection_candidates(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "docs"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    init_repo(tmp_path / "repos/web")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["repo_changes"]["observed_since_baseline"] == "observed"
    state = json.loads((tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json").read_text(encoding="utf-8"))
    assert state["initial"]["repositories"][0]["repo_id"] == ""
    assert state["initial"]["repositories"][0]["identity_source"] == "unbound"


def test_task_state_schema_version_requires_an_exact_json_integer(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    state_path = tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    state["schema_version"] = 4.0
    state_path.write_text(json.dumps(state, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "task_state_schema_unsupported"
    assert payload["data"]["health"]["codes"] == ["task_state_schema_unsupported"]


def test_task_start_force_dirty_records_paths_only_in_machine_baseline(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty"]) == 0

    text = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "dirty repo state recorded" in text
    assert "dirty_count=1" in text
    assert "dirty.txt" not in text
    state_path = tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["initial"]["repositories"][0]["dirty_entries"] == [
        {"change": "untracked", "path": "dirty.txt"}
    ]


def test_task_show_and_doctor_report_task_new_changed_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"'))
    repo = tmp_path / "repos"
    init_committed_product_repo(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    (repo / "changed.py").write_text("print('changed')\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["data"]["repo_changes"]["task_new_files"] == ["changed.py"]

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 0
    doctor_payload = json.loads(capsys.readouterr().out)
    assert doctor_payload["data"]["repo_changes"]["task_new_files"] == ["changed.py"]
    assert doctor_payload["problems"] == []


def test_task_lifecycle_keeps_created_document_language_when_workspace_setting_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/repoctl.json").write_text('{"document_language":"ko"}\n', encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "korean-lifecycle", "Korean Lifecycle", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    task_id = payload["data"]["task_id"]
    task_path = tmp_path / payload["data"]["path"]
    assert 'document_language: "ko"' in task_path.read_text(encoding="utf-8")

    (tmp_path / "docs/repoctl.json").write_text('{"document_language":"en"}\n', encoding="utf-8")

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    started = task_path.read_text(encoding="utf-8")
    assert "작업을 시작" in started
    assert "구현을 계속한다" in started
    assert "task started." not in started

    verification = tmp_path / "verification.md"
    verification.write_text("- Command: pytest\n- Result: pass\n", encoding="utf-8")
    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0
    finish_payload = json.loads(capsys.readouterr().out)
    archived = (tmp_path / finish_payload["data"]["new_path"]).read_text(encoding="utf-8")
    assert "작업을 검증하고 완료함" in archived
    assert "Repoctl 게이트 요약" not in archived
    assert "- Command: pytest" in archived
    assert "## Last Active Handoff" in archived
    assert "## Closure" in archived
    assert "repoctl 관리 범위가 아님" in archived
    assert "task finished and verified" not in archived


def test_json_argparse_errors_are_machine_readable(capsys) -> None:
    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "task_not_found"


def test_task_start_force_dirty_rejects_doing_task_and_preserves_initial_state(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    state_path = tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json"
    initial_state = state_path.read_bytes()

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "task_already_started"
    assert state_path.read_bytes() == initial_state


def test_task_block_resume_preserves_initial_head_and_dirty_baseline(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "old\n"})
    blocker = tmp_path / "blocker.md"
    blocker.write_text("waiting for review\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    state_path = tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json"
    initial_state = state_path.read_bytes()
    (repo / "app.py").write_text("new\n", encoding="utf-8")

    assert main(["task", "block", "T-20260609184046Z", "--verification-file", str(blocker), "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0

    capsys.readouterr()
    assert state_path.read_bytes() == initial_state


def _start_repo_task_with_resume_surface(tmp_path: Path, monkeypatch, capsys) -> tuple[Path, Path, Path]:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    task_path = add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    task_path.write_text(
        replace_section(
            task_path.read_text(encoding="utf-8"),
            "Handoff",
            "- Next exact step: inspect the app owner and continue the recorded implementation.\n"
            "- First file to open: `repos/app.py`\n"
            "- First command to run: `git -C repos diff -- app.py`\n"
            "- Done when: the app change and its focused verification are recorded.\n",
        ),
        encoding="utf-8",
    )
    receipt = tmp_path / "docs/tasks/.repoctl-state/resume/T-20260609184046Z.json"
    return task_path, repo, receipt


def _show_resume_guidance(capsys) -> dict:
    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 0
    return json.loads(capsys.readouterr().out)["data"]["resume_guidance"]


def _bind_handoff(capsys, *extra: str) -> dict:
    assert main(["task", "handoff", "bind", "T-20260609184046Z", *extra, "--json"]) == 0
    return json.loads(capsys.readouterr().out)


def _create_repoctl_generated_task(tmp_path: Path, monkeypatch, capsys) -> tuple[str, Path]:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["task", "create", "--slug", "alpha", "Alpha task", "--json"]) == 0
    created = json.loads(capsys.readouterr().out)
    return created["data"]["task_id"], tmp_path / created["data"]["path"]


def test_repoctl_generated_handoff_is_readable_but_cannot_be_bound(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)

    origin_path = tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json"
    origin = json.loads(origin_path.read_text(encoding="utf-8"))
    created_task = resolve_task(tmp_path, task_id)
    assert origin["template_version"] == 1
    assert len(origin["generated_handoff_digests"]) == 1
    assert created_task.frontmatter["handoff_origin_commitment"] in origin["generated_handoff_digests"]

    assert main(["task", "start", task_id, "--json"]) == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert any(warning["code"] == "task_handoff_generated_template" for warning in start_payload["warnings"])
    assert not any("task handoff bind" in action.get("command", "") for action in start_payload["next_actions"])
    assert start_payload["next_actions"][0]["source"] == "data.resume_guidance.handoff.generated_template"

    assert main(["task", "show", task_id, "--summary", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["data"]["resume_guidance"]["handoff"]["generated_template"] is True
    assert any(warning["code"] == "task_handoff_generated_template" for warning in show_payload["warnings"])

    assert main(["task", "resume", "--json"]) == 0
    resume_payload = json.loads(capsys.readouterr().out)
    assert resume_payload["next_actions"][0] == {
        "label": "Replace the generated Handoff with task-specific restart instructions",
        "path": task_path.relative_to(tmp_path).as_posix(),
    }
    assert not any(action.get("kind") == "task_handoff_bind" for action in resume_payload["next_actions"])

    assert main(["task", "doctor", task_id, "--json"]) == 0
    doctor_payload = json.loads(capsys.readouterr().out)
    assert any(warning["code"] == "task_handoff_generated_template" for warning in doctor_payload["warnings"])
    generated_action = next(
        action
        for action in doctor_payload["next_actions"]
        if action["label"].startswith("Replace the generated Handoff")
    )
    assert generated_action == {
        "label": "Replace the generated Handoff with task-specific restart instructions",
        "path": task_path.relative_to(tmp_path).as_posix(),
    }

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 2
    bind_payload = json.loads(capsys.readouterr().out)
    assert bind_payload["problems"][0]["code"] == "task_handoff_generated_template"
    assert "Next exact step" in task_path.read_text(encoding="utf-8")
    assert not (tmp_path / f"docs/tasks/.repoctl-state/resume/{task_id}.json").exists()
    started_origin = json.loads(origin_path.read_text(encoding="utf-8"))
    started_task = resolve_task(tmp_path, task_id)
    assert len(started_origin["generated_handoff_digests"]) == 2
    assert started_task.frontmatter["handoff_origin_commitment"] in started_origin["generated_handoff_digests"]


def test_generated_handoff_origin_survives_supported_template_version_upgrade(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)
    origin_path = tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json"
    original_origin_text = origin_path.read_text(encoding="utf-8")
    original_origin = json.loads(original_origin_text)
    original_commitment = resolve_task(tmp_path, task_id).frontmatter[
        "handoff_origin_commitment"
    ]
    assert original_origin["template_version"] == 1
    assert original_commitment in original_origin["generated_handoff_digests"]

    monkeypatch.setattr("tools.repoctl.tasks.HANDOFF_TEMPLATE_VERSION", 2)
    monkeypatch.setitem(
        TASK_DOC_COPY["en"],
        "start_handoff_next",
        "Continue renderer-v2 implementation for `{task_path}`.",
    )

    assert main(["task", "show", task_id, "--summary", "--json"]) == 0
    shown = json.loads(capsys.readouterr().out)
    assert shown["data"]["resume_guidance"]["handoff"]["generated_template"] is True
    assert origin_path.read_text(encoding="utf-8") == original_origin_text

    assert main(["task", "resume", "--json"]) == 0
    resumed = json.loads(capsys.readouterr().out)
    guidance = resumed["data"]["resume_guidance"]
    assert guidance["status"] == "unbound"
    assert guidance["handoff"]["generated_template"] is True
    assert guidance["handoff"]["active"] is False
    assert guidance["readable_handoff"] is None
    assert guidance["executable_handoff"] is None
    assert origin_path.read_text(encoding="utf-8") == original_origin_text

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 2
    rejected_bind = json.loads(capsys.readouterr().out)
    assert rejected_bind["problems"][0]["code"] == "task_handoff_generated_template"
    assert origin_path.read_text(encoding="utf-8") == original_origin_text
    assert not (tmp_path / f"docs/tasks/.repoctl-state/resume/{task_id}.json").exists()

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    started_task = resolve_task(tmp_path, task_id)
    migrated_origin = json.loads(origin_path.read_text(encoding="utf-8"))
    started_commitment = started_task.frontmatter["handoff_origin_commitment"]
    assert "Continue renderer-v2 implementation" in task_path.read_text(encoding="utf-8")
    assert migrated_origin["template_version"] == 2
    assert started_commitment != original_commitment
    assert set(migrated_origin["generated_handoff_digests"]) == {
        original_commitment,
        started_commitment,
    }
    assert task_handoff_provenance(tmp_path, started_task) is TaskHandoffProvenance.GENERATED


@pytest.mark.parametrize("interrupt_after", ["origin", "task"])
def test_generated_handoff_template_upgrade_publication_is_interrupt_safe(
    tmp_path: Path,
    monkeypatch,
    capsys,
    interrupt_after: str,
) -> None:
    task_id, task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)
    origin_path = tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json"
    original_task_text = task_path.read_text(encoding="utf-8")
    original_commitment = resolve_task(tmp_path, task_id).frontmatter[
        "handoff_origin_commitment"
    ]
    monkeypatch.setattr("tools.repoctl.tasks.HANDOFF_TEMPLATE_VERSION", 2)
    monkeypatch.setitem(
        TASK_DOC_COPY["en"],
        "start_handoff_next",
        "Continue renderer-v2 implementation for `{task_path}`.",
    )
    cli_module = __import__("tools.repoctl.cli", fromlist=["atomic_write"])
    real_atomic_write = cli_module.atomic_write
    writes: list[Path] = []

    def interrupt_publication(path: Path, value: str) -> None:
        real_atomic_write(path, value)
        writes.append(path)
        if (interrupt_after == "origin" and path == origin_path) or (
            interrupt_after == "task" and path == task_path
        ):
            raise KeyboardInterrupt

    with monkeypatch.context() as interruption:
        interruption.setattr("tools.repoctl.cli.atomic_write", interrupt_publication)
        with pytest.raises(KeyboardInterrupt):
            main(["task", "start", task_id, "--json"])

    expected_writes = [origin_path] if interrupt_after == "origin" else [origin_path, task_path]
    assert writes == expected_writes
    interrupted_origin = json.loads(origin_path.read_text(encoding="utf-8"))
    interrupted_task = resolve_task(tmp_path, task_id)
    assert interrupted_origin["template_version"] == 2
    assert original_commitment in interrupted_origin["generated_handoff_digests"]
    assert task_handoff_provenance(tmp_path, interrupted_task) is TaskHandoffProvenance.GENERATED
    assert not (tmp_path / f"docs/tasks/.repoctl-state/resume/{task_id}.json").exists()

    if interrupt_after == "origin":
        assert interrupted_task.status == "todo"
        assert task_path.read_text(encoding="utf-8") == original_task_text
        assert interrupted_task.frontmatter["handoff_origin_commitment"] == original_commitment
        assert main(["task", "start", task_id, "--json"]) == 0
        capsys.readouterr()
        interrupted_task = resolve_task(tmp_path, task_id)
        interrupted_origin = json.loads(origin_path.read_text(encoding="utf-8"))
    else:
        assert interrupted_task.status == "doing"

    current_commitment = interrupted_task.frontmatter["handoff_origin_commitment"]
    assert interrupted_task.status == "doing"
    assert current_commitment != original_commitment
    assert "Continue renderer-v2 implementation" in task_path.read_text(encoding="utf-8")
    assert set(interrupted_origin["generated_handoff_digests"]) == {
        original_commitment,
        current_commitment,
    }
    assert task_handoff_provenance(tmp_path, interrupted_task) is TaskHandoffProvenance.GENERATED


def test_generated_handoff_origin_rejects_template_version_from_newer_binary(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, _task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)
    origin_path = tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json"
    origin = json.loads(origin_path.read_text(encoding="utf-8"))
    origin["template_version"] = 2
    basis = {key: value for key, value in origin.items() if key != "state_digest"}
    origin["state_digest"] = digest_data(basis)
    origin_path.write_text(json.dumps(origin, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["task", "show", task_id, "--summary", "--json"]) == 1
    rejected = json.loads(capsys.readouterr().out)
    assert any(
        problem["code"] == "task_handoff_origin_invalid"
        for problem in rejected["problems"]
    )


def test_generated_handoff_provenance_fails_closed_with_invalid_document_language(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)
    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    task_path.write_text(
        task_path.read_text(encoding="utf-8").replace(
            'document_language: "en"',
            'document_language: "invalid"',
        ),
        encoding="utf-8",
    )

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "task_handoff_generated_template"


def test_generated_handoff_origin_state_corruption_is_not_treated_as_authored(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, _task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)
    origin_path = tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json"
    origin_path.write_text("{}\n", encoding="utf-8")

    assert main(["task", "resume", "--json"]) == 1
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["data"]["selection"] == {"status": "single_live", "live_task_count": 1}
    assert resumed["data"]["resume_guidance"]["executable_handoff"] is None
    assert any(problem["code"] == "task_handoff_origin_invalid" for problem in resumed["problems"])

    assert main(["task", "start", task_id, "--json"]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "task_handoff_origin_invalid"


@pytest.mark.parametrize(
    "origin_kind",
    ["directory", "dangling_symlink", "regular_symlink", "fifo"],
)
def test_generated_handoff_nonregular_origin_cannot_downgrade_to_legacy(
    tmp_path: Path,
    monkeypatch,
    capsys,
    origin_kind: str,
) -> None:
    task_id, task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)
    origin_path = tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json"
    origin_text = origin_path.read_text(encoding="utf-8")
    origin_path.unlink()
    if origin_kind == "directory":
        origin_path.mkdir()
    elif origin_kind == "dangling_symlink":
        origin_path.symlink_to(tmp_path / "missing-origin.json")
    elif origin_kind == "regular_symlink":
        target = tmp_path / "outside-origin.json"
        target.write_text(origin_text, encoding="utf-8")
        origin_path.symlink_to(target)
    else:
        os.mkfifo(origin_path)
    original_task_text = task_path.read_text(encoding="utf-8")

    assert main(["task", "start", task_id, "--json"]) == 2
    rejected_start = json.loads(capsys.readouterr().out)
    assert rejected_start["problems"][0]["code"] == "task_handoff_origin_invalid"
    assert task_path.read_text(encoding="utf-8") == original_task_text

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 2
    rejected_bind = json.loads(capsys.readouterr().out)
    assert rejected_bind["problems"][0]["code"] == "task_handoff_origin_invalid"
    assert not (tmp_path / f"docs/tasks/.repoctl-state/resume/{task_id}.json").exists()


def test_generated_handoff_missing_origin_remains_generated_and_is_recovered_on_start(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, _task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)
    origin_path = tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json"
    origin_path.unlink()

    assert task_handoff_provenance(tmp_path, resolve_task(tmp_path, task_id)) is TaskHandoffProvenance.GENERATED
    assert main(["task", "handoff", "bind", task_id, "--json"]) == 2
    rejected_bind = json.loads(capsys.readouterr().out)
    assert rejected_bind["problems"][0]["code"] == "task_handoff_generated_template"

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert origin_path.is_file()
    assert not origin_path.is_symlink()
    assert task_handoff_provenance(tmp_path, resolve_task(tmp_path, task_id)) is TaskHandoffProvenance.GENERATED

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 2
    rejected_started_bind = json.loads(capsys.readouterr().out)
    assert rejected_started_bind["problems"][0]["code"] == "task_handoff_generated_template"


def test_authored_handoff_can_migrate_after_generated_origin_is_lost(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)
    task_path.write_text(
        replace_section(
            task_path.read_text(encoding="utf-8"),
            "Handoff",
            "- Next exact step: inspect the authored recovery path.\n"
            "- First file to open: `docs/BOARD.md`\n"
            "- First command to run: `repoctl check`\n"
            "- Done when: the reviewed recovery Handoff is current.\n",
        ),
        encoding="utf-8",
    )
    origin_path = tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json"
    origin_path.unlink()

    assert task_handoff_provenance(tmp_path, resolve_task(tmp_path, task_id)) is TaskHandoffProvenance.AUTHORED_OR_REVIEWED
    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert "inspect the authored recovery path" in task_path.read_text(encoding="utf-8")
    assert not origin_path.exists()

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
    rebound = json.loads(capsys.readouterr().out)
    assert rebound["data"]["resume_guidance"]["status"] == "current"


def test_generated_handoff_commitment_mismatch_fails_closed(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)
    task_path.write_text(
        replace_frontmatter_line(
            task_path.read_text(encoding="utf-8"),
            "handoff_origin_commitment",
            f'"sha256:{"0" * 64}"',
        ),
        encoding="utf-8",
    )

    assert main(["task", "start", task_id, "--json"]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "task_handoff_origin_invalid"


def test_generated_handoff_stale_but_valid_commitment_fails_closed(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)
    origin_path = tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json"
    creation_commitment = resolve_task(tmp_path, task_id).frontmatter["handoff_origin_commitment"]

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    started_task = resolve_task(tmp_path, task_id)
    assert started_task.frontmatter["handoff_origin_commitment"] != creation_commitment
    assert creation_commitment in json.loads(origin_path.read_text(encoding="utf-8"))["generated_handoff_digests"]
    task_path.write_text(
        replace_frontmatter_line(
            task_path.read_text(encoding="utf-8"),
            "handoff_origin_commitment",
            f'"{creation_commitment}"',
        ),
        encoding="utf-8",
    )

    assert main(["task", "resume", "--json"]) == 1
    resumed = json.loads(capsys.readouterr().out)
    assert resumed["data"]["resume_guidance"]["executable_handoff"] is None
    assert any(problem["code"] == "task_handoff_origin_invalid" for problem in resumed["problems"])

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 2
    rejected_bind = json.loads(capsys.readouterr().out)
    assert rejected_bind["problems"][0]["code"] == "task_handoff_origin_invalid"


def test_task_creation_commits_origin_before_publishing_generated_task(
    tmp_path: Path,
    monkeypatch,
) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    task_module = __import__("tools.repoctl.tasks", fromlist=["atomic_write"])
    real_atomic_write = task_module.atomic_write
    writes: list[Path] = []

    def interrupt_after_publish(path: Path, value: str) -> None:
        real_atomic_write(path, value)
        writes.append(path)
        if path.parent == tmp_path / "docs/tasks" and path.name.startswith("T-"):
            raise KeyboardInterrupt

    monkeypatch.setattr("tools.repoctl.tasks.atomic_write", interrupt_after_publish)
    try:
        main(["task", "create", "--slug", "interrupted", "Interrupted create", "--json"])
    except KeyboardInterrupt:
        pass
    else:  # pragma: no cover - the simulated interruption must escape cleanup
        raise AssertionError("task creation was not interrupted")

    task_paths = list((tmp_path / "docs/tasks").glob("T-*--interrupted.md"))
    assert len(task_paths) == 1
    task_id = task_paths[0].name.split("--", 1)[0]
    origin_path = tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json"
    assert writes[:2] == [origin_path, task_paths[0]]
    assert origin_path.is_file()
    assert task_handoff_provenance(tmp_path, resolve_task(tmp_path, task_id)) is TaskHandoffProvenance.GENERATED


def test_handoff_command_text_is_inert_across_resume_lifecycle(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    marker = tmp_path / "handoff-command-must-not-run"
    command = f"touch {marker} && printf should-not-run"
    text = task_text(task_id, status="todo").replace("repoctl check", command)
    add_board_task(tmp_path, f"{task_id}--inert-command.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert not marker.exists()

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
    capsys.readouterr()
    assert not marker.exists()

    for command_args in (
        ["task", "show", task_id, "--summary", "--json"],
        ["task", "resume", "--json"],
    ):
        assert main(command_args) == 0
        payload = json.loads(capsys.readouterr().out)
        assert command in json.dumps(payload, ensure_ascii=False)
        assert not marker.exists()

    assert main(["task", "doctor", task_id, "--json"]) == 0
    capsys.readouterr()
    assert not marker.exists()


def test_preexisting_binding_cannot_activate_an_unchanged_generated_handoff(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id, _task_path = _create_repoctl_generated_task(tmp_path, monkeypatch, capsys)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    with monkeypatch.context() as legacy:
        legacy.setattr(
            "tools.repoctl.tasks.task_handoff_provenance",
            lambda _root, _task: TaskHandoffProvenance.AUTHORED_OR_REVIEWED,
        )
        assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
        capsys.readouterr()

    receipt = tmp_path / f"docs/tasks/.repoctl-state/resume/{task_id}.json"
    assert receipt.is_file()
    with monkeypatch.context() as upgraded_renderer:
        upgraded_renderer.setitem(
            TASK_DOC_COPY["en"],
            "start_handoff_next",
            "A later repoctl renderer uses different generated copy for `{task_path}`.",
        )
        assert main(["task", "resume", "--json"]) == 0
        payload = json.loads(capsys.readouterr().out)
    guidance = payload["data"]["resume_guidance"]
    assert guidance["status"] == "unbound"
    assert guidance["handoff"]["generated_template"] is True
    assert guidance["handoff"]["active"] is False
    assert guidance["handoff"]["reason_codes"] == ["task_handoff_generated_template"]
    assert guidance["readable_handoff"] is None
    assert guidance["executable_handoff"] is None


def test_unknown_legacy_handoff_binding_requires_one_fresh_review_migration(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    add_board_task(tmp_path, f"{task_id}--legacy.md", task_text(task_id, status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
    capsys.readouterr()
    binding_path = tmp_path / f"docs/tasks/.repoctl-state/resume/{task_id}.json"
    old_binding = json.loads(binding_path.read_text(encoding="utf-8"))
    old_binding["schema_version"] = 2
    binding_path.write_text(json.dumps(old_binding, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["task", "resume", "--json"]) == 0
    legacy = json.loads(capsys.readouterr().out)
    guidance = legacy["data"]["resume_guidance"]
    assert guidance["status"] == "unbound"
    assert guidance["handoff"]["reason_codes"] == ["task_handoff_origin_unknown"]
    assert guidance["executable_handoff"] is None
    assert any(item["code"] == "task_handoff_origin_unknown" for item in legacy["warnings"])

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
    rebound = json.loads(capsys.readouterr().out)
    assert rebound["data"]["resume_guidance"]["status"] == "current"
    assert json.loads(binding_path.read_text(encoding="utf-8"))["schema_version"] == 3
    assert not (tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json").exists()


def test_unknown_legacy_handoff_rejects_symlinked_resume_binding(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    add_board_task(tmp_path, f"{task_id}--legacy.md", task_text(task_id, status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
    capsys.readouterr()
    binding_path = tmp_path / f"docs/tasks/.repoctl-state/resume/{task_id}.json"
    outside = tmp_path.parent / f"{tmp_path.name}-outside-binding.json"
    outside.write_bytes(binding_path.read_bytes())
    binding_path.unlink()
    binding_path.symlink_to(outside)

    assert main(["task", "resume", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "task_resume_binding_invalid"
    assert payload["data"]["resume_guidance"]["status"] == "unknown"


def test_originless_placeholder_copy_is_not_used_to_infer_handoff_provenance(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    rel_path = f"docs/tasks/{task_id}--legacy.md"
    copy = TASK_DOC_COPY["en"]
    originless_placeholder = (
        f"- Next exact step: {copy['start_handoff_next'].format(task_path=rel_path)}\n"
        f"- First file to open: `{rel_path}`\n"
        "- First command to run: `./scripts/repoctl task list --json`\n"
        f"- Done when: {copy['start_handoff_done']}\n"
    )
    text = replace_section(
        task_text(task_id, status="doing"),
        "Handoff",
        originless_placeholder,
    )
    add_board_task(tmp_path, f"{task_id}--legacy.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    task = resolve_task(tmp_path, task_id)
    assert task_handoff_provenance(tmp_path, task) is TaskHandoffProvenance.UNKNOWN_LEGACY

    assert main(["task", "resume", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    guidance = payload["data"]["resume_guidance"]
    assert guidance["status"] == "unbound"
    assert guidance["handoff"]["generated_template"] is False
    assert guidance["handoff"]["reason_codes"] == ["task_handoff_origin_unknown"]
    assert payload["next_actions"][0] == {
        "label": "Regenerate or replace the origin-unknown Handoff with task-specific restart instructions",
        "path": rel_path,
    }
    assert any(
        action.get("kind") == "task_handoff_bind"
        for action in payload["next_actions"]
    )

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
    rebound = json.loads(capsys.readouterr().out)
    assert rebound["data"]["resume_guidance"]["status"] == "current"


def test_unknown_legacy_handoff_review_commits_in_one_binding_write(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    add_board_task(tmp_path, f"{task_id}--legacy.md", task_text(task_id, status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    binding_path = tmp_path / f"docs/tasks/.repoctl-state/resume/{task_id}.json"
    origin_path = tmp_path / f"docs/tasks/.repoctl-state/handoff-origins/{task_id}.json"
    task_module = __import__("tools.repoctl.tasks", fromlist=["atomic_write"])
    real_atomic_write = task_module.atomic_write
    writes: list[Path] = []

    def reject_origin_write(path: Path, value: str) -> None:
        writes.append(path)
        if path == origin_path:
            raise OSError("simulated second machine-state write failure")
        real_atomic_write(path, value)

    monkeypatch.setattr("tools.repoctl.tasks.atomic_write", reject_origin_write)
    assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
    bound = json.loads(capsys.readouterr().out)
    assert bound["data"]["resume_guidance"]["status"] == "current"
    assert writes == [binding_path]
    assert json.loads(binding_path.read_text(encoding="utf-8"))["schema_version"] == 3
    assert not origin_path.exists()


def test_invalid_task_document_language_remains_a_check_diagnostic(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    text = task_text(task_id).replace(
        "depends_on: []",
        'depends_on: []\ndocument_language: "invalid"',
    )
    add_board_task(tmp_path, f"{task_id}--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["check", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "check"
    assert any(problem["code"] == "invalid_document_language" for problem in payload["problems"])


def test_task_handoff_is_readable_but_inactive_until_explicit_bind(tmp_path: Path, monkeypatch, capsys) -> None:
    task_path, _repo, receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)

    assert not receipt.exists()
    before = task_path.read_bytes()
    guidance = _show_resume_guidance(capsys)
    assert guidance["status"] == "unbound"
    assert guidance["handoff"]["active"] is False
    assert "Next exact step" in guidance["handoff"]["body"]

    payload = _bind_handoff(capsys)
    assert payload["data"]["resume_guidance"]["status"] == "current"
    assert payload["data"]["resume_guidance"]["context_pack"]["status"] == "not_bound"
    assert receipt.is_file()
    assert task_path.read_bytes() == before

    bound_receipt = receipt.read_bytes()
    blocker = tmp_path / "blocker.md"
    blocker.write_text("Waiting for an external decision.\n", encoding="utf-8")
    assert main(["task", "block", "T-20260609184046Z", "--verification-file", str(blocker), "--json"]) == 0
    capsys.readouterr()
    assert receipt.read_bytes() == bound_receipt
    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert receipt.read_bytes() == bound_receipt
    assert start_payload["data"]["resume_guidance"]["status"] == "stale"


def test_task_handoff_binding_tracks_each_structured_task_input(tmp_path: Path, monkeypatch, capsys) -> None:
    task_path, _repo, _receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    _bind_handoff(capsys)

    def assert_stale(input_name: str) -> None:
        guidance = _show_resume_guidance(capsys)
        assert guidance["status"] == "stale"
        assert input_name in guidance["changed_inputs"]
        _bind_handoff(capsys)
        assert _show_resume_guidance(capsys)["status"] == "current"

    text = task_path.read_text(encoding="utf-8")
    task_path.write_text(
        replace_section(
            text,
            "Handoff",
            "- Next exact step: inspect the changed owner.\n"
            "- First file to open: `repos/app.py`\n"
            "- First command to run: `git -C repos diff -- app.py`\n"
            "- Done when: the owner is verified.\n",
        ),
        encoding="utf-8",
    )
    assert_stale("handoff")

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--query", "app owner", "--json"]) == 0
    capsys.readouterr()
    assert_stale("discovery")

    assert main(["task", "log", "append", "T-20260609184046Z", "reviewed app owner", "--json"]) == 0
    capsys.readouterr()
    assert_stale("execution_log")

    text = task_path.read_text(encoding="utf-8")
    task_path.write_text(replace_section(text, "Verification", "- Command: pytest\n- Result: pass\n"), encoding="utf-8")
    assert_stale("verification")

    text = task_path.read_text(encoding="utf-8")
    task_path.write_text(replace_frontmatter_line(text, "status", "blocked"), encoding="utf-8")
    guidance = _show_resume_guidance(capsys)
    assert guidance["status"] == "stale"
    assert "task_contract" in guidance["changed_inputs"]


def test_parent_handoff_binding_tracks_direct_child_lifecycle(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    parent_id = "T-20260609184046Z"
    child_id = "T-20260609184047Z"
    parent_path = tmp_path / f"docs/tasks/{parent_id}--parent.md"
    child_path = tmp_path / f"docs/tasks/{child_id}--child.md"
    parent_path.write_text(task_text(parent_id, status="doing"), encoding="utf-8")
    child_path.write_text(task_text(child_id, status="todo", parent=parent_id), encoding="utf-8")
    (tmp_path / "docs/BOARD.md").write_text(
        "# BOARD\n\n## Board\n\n"
        f"- docs/tasks/{parent_path.name}\n"
        f"- docs/tasks/{child_path.name}\n\n"
        "## Backlog\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    def bind_parent() -> dict:
        assert main(["task", "handoff", "bind", parent_id, "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    def show_parent() -> dict:
        assert main(["task", "show", parent_id, "--summary", "--json"]) == 0
        return json.loads(capsys.readouterr().out)

    bind_parent()

    child_path.write_text(
        replace_frontmatter_line(child_path.read_text(encoding="utf-8"), "status", "blocked"),
        encoding="utf-8",
    )
    guidance = show_parent()["data"]["resume_guidance"]
    assert guidance["status"] == "stale"
    assert guidance["changed_inputs"] == ["direct_children"]


def test_task_handoff_repository_digest_detects_same_head_content_drift_but_not_touch(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _task_path, repo, _receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    app = repo / "app.py"
    app.write_text("value = 2\n", encoding="utf-8")
    _bind_handoff(capsys)
    head = subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip()

    app.write_text("value = 3\n", encoding="utf-8")
    assert subprocess.check_output(["git", "rev-parse", "HEAD"], cwd=repo, text=True).strip() == head
    guidance = _show_resume_guidance(capsys)
    assert guidance["status"] == "stale"
    assert "repository" in guidance["changed_inputs"]

    _bind_handoff(capsys)
    app.touch()
    assert _show_resume_guidance(capsys)["status"] == "current"


def test_current_handoff_is_readable_but_not_executable_when_repository_lineage_is_unhealthy(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _task_path, repo, _receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    _bind_handoff(capsys)
    binding_path = tmp_path / "docs/tasks/.repoctl-state/resume/T-20260609184046Z.json"
    binding = json.loads(binding_path.read_text(encoding="utf-8"))

    tree = subprocess.check_output(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, text=True).strip()
    rewritten = subprocess.check_output(
        ["git", "commit-tree", tree, "-m", "unrelated root"],
        cwd=repo,
        text=True,
    ).strip()
    subprocess.run(["git", "reset", "--hard", rewritten], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    # Keep only Handoff freshness current to prove it is independent of lifecycle health.
    current_task = resolve_task(tmp_path, "T-20260609184046Z")
    from tools.repoctl.tasks import task_resume_input_digests

    binding["input_digests"] = task_resume_input_digests(tmp_path, current_task)
    binding_path.write_text(json.dumps(binding, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["task", "resume", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    guidance = payload["data"]["resume_guidance"]
    assert guidance["status"] == "current"
    assert guidance["handoff"]["active"] is True
    assert guidance["health"]["status"] == "unhealthy"
    assert guidance["health"]["codes"]
    assert guidance["blocked_by_health"] is True
    assert "Next exact step" in guidance["readable_handoff"]
    assert guidance["executable_handoff"] is None

    assert binding_path.read_bytes() == (json.dumps(binding, indent=2, sort_keys=True) + "\n").encode()


def test_task_handoff_invalid_receipt_fails_closed_and_archived_handoff_is_historical(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_path, _repo, receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    _bind_handoff(capsys)
    receipt.write_text("{}\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 1
    show_payload = json.loads(capsys.readouterr().out)
    guidance = show_payload["data"]["resume_guidance"]
    assert show_payload["ok"] is False
    assert show_payload["problems"][0]["code"] == "task_resume_binding_invalid"
    assert guidance["status"] == "unknown"
    assert guidance["handoff"]["reason_codes"] == ["resume_binding_invalid"]
    assert guidance["handoff"]["active"] is False
    assert main(["check", "--json"]) == 1
    check_payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "task_resume_binding_invalid" for problem in check_payload["problems"])

    _bind_handoff(capsys)
    archived = tmp_path / "docs/archive/tasks" / task_path.name
    archived.write_text(replace_frontmatter_line(task_path.read_text(encoding="utf-8"), "status", "done"), encoding="utf-8")
    locator = tmp_path / "docs/tasks/.repoctl-state/archive/T-20260609184046Z.json"
    locator.parent.mkdir(parents=True, exist_ok=True)
    locator.write_text(
        json.dumps(
            {
                "schema": "repoctl.task.archive",
                "schema_version": 1,
                "task_id": "T-20260609184046Z",
                "task_path": archived.relative_to(tmp_path).as_posix(),
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )
    task_path.unlink()
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n## Backlog\n", encoding="utf-8")

    guidance = _show_resume_guidance(capsys)
    assert guidance["status"] == "historical"
    assert guidance["handoff"]["active"] is False


def test_task_handoff_bind_rejects_missing_empty_and_duplicate_canonical_fields(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_path, _repo, receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    original = task_path.read_text(encoding="utf-8")
    canonical = {
        "Next exact step": "inspect the owner",
        "First file to open": "`repos/app.py`",
        "First command to run": "`git -C repos diff -- app.py`",
        "Done when": "the owner is verified",
    }

    for omitted in canonical:
        body = "".join(f"- {label}: {value}\n" for label, value in canonical.items() if label != omitted)
        task_path.write_text(replace_section(original, "Handoff", body), encoding="utf-8")
        assert main(["task", "handoff", "bind", "T-20260609184046Z", "--json"]) == 2
        payload = json.loads(capsys.readouterr().out)
        assert payload["problems"][0]["code"] == "invalid_handoff_structure"
        assert not receipt.exists()

    empty = "".join(
        f"- {label}: {'' if label == 'Next exact step' else value}\n"
        for label, value in canonical.items()
    )
    task_path.write_text(replace_section(original, "Handoff", empty), encoding="utf-8")
    assert main(["task", "handoff", "bind", "T-20260609184046Z", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == "invalid_handoff_structure"

    duplicate = "".join(f"- {label}: {value}\n" for label, value in canonical.items()) + "- Done when: duplicate\n"
    task_path.write_text(replace_section(original, "Handoff", duplicate), encoding="utf-8")
    assert main(["task", "handoff", "bind", "T-20260609184046Z", "--json"]) == 2
    assert json.loads(capsys.readouterr().out)["problems"][0]["code"] == "invalid_handoff_structure"


def test_task_show_rejects_strictly_malformed_resume_receipts(tmp_path: Path, monkeypatch, capsys) -> None:
    _task_path, _repo, receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    _bind_handoff(capsys)
    valid = json.loads(receipt.read_text(encoding="utf-8"))
    invalid_receipts = []

    boolean_version = dict(valid)
    boolean_version["schema_version"] = True
    invalid_receipts.append(boolean_version)

    wrong_task = dict(valid)
    wrong_task["task_id"] = "T-20260609184047Z"
    invalid_receipts.append(wrong_task)

    extra_field = dict(valid)
    extra_field["unexpected"] = True
    invalid_receipts.append(extra_field)

    noncanonical_pack_path = dict(valid)
    noncanonical_pack_path["context_pack"] = {
        "path": "./.repoctl-state/context-pack/pack.json",
        "artifact_sha256": "sha256:" + "1" * 64,
        "input_digest": "sha256:" + "2" * 64,
    }
    invalid_receipts.append(noncanonical_pack_path)

    for invalid in invalid_receipts:
        receipt.write_text(json.dumps(invalid, indent=2, sort_keys=True) + "\n", encoding="utf-8")
        assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 1
        payload = json.loads(capsys.readouterr().out)
        assert payload["ok"] is False
        assert payload["problems"][0]["code"] == "task_resume_binding_invalid"
        assert payload["data"]["resume_guidance"]["status"] == "unknown"


def test_task_show_rejects_missing_handoff_and_keeps_summary_inputs_bounded(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_path, _repo, _receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    _bind_handoff(capsys)
    text = task_path.read_text(encoding="utf-8")
    long_query = "query-sentinel-" + "x" * 4000
    long_verification = "verification-sentinel-" + "y" * 4000
    text = replace_section(
        text,
        "Discovery",
        f"- Candidate query: {long_query}\n- Candidate files reviewed: `repos/app.py`\n- Chosen files: `repos/app.py`\n",
    )
    text = replace_section(text, "Verification", f"- {long_verification}\n")
    task_path.write_text(text, encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 0
    summary_text = capsys.readouterr().out
    assert "query-sentinel" not in summary_text
    assert "verification-sentinel" not in summary_text
    assert len(summary_text) < 12000

    text = task_path.read_text(encoding="utf-8")
    handoff_section = find_section(text, "Handoff")
    task_path.write_text((text[: handoff_section.start] + text[handoff_section.end :]).rstrip() + "\n", encoding="utf-8")
    assert main(["task", "show", "T-20260609184046Z", "--summary", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["problems"][0]["code"] == "missing_handoff"
    assert payload["data"]["resume_guidance"]["status"] == "unknown"

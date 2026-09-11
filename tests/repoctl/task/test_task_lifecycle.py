from __future__ import annotations

import json
import subprocess
from pathlib import Path

import pytest

from tools.repoctl.cli import TaskHealth, main
from tools.repoctl.markdown import find_section, replace_frontmatter_line, replace_section
from tools.repoctl.tasks import (
    Problem,
    resolve_task,
)
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
    assert payload["next_actions"] == [
        {"label": "Review deferred backlog", "command": "./scripts/repoctl backlog list --json"},
        {"label": "Open the Board", "path": "docs/BOARD.md"},
    ]

    first = "T-20260609184046Z"
    add_board_task(tmp_path, f"{first}--alpha.md", task_text(first, status="doing"))
    assert main(["task", "resume", "--json"]) == 0
    unbound = json.loads(capsys.readouterr().out)["data"]
    assert unbound["selection"] == {"status": "single_live", "live_task_count": 1}
    assert unbound["resume_guidance"]["status"] == "inactive"
    assert unbound["resume_guidance"]["executable_handoff"] is None
    assert "body" not in unbound["resume_guidance"]["handoff"]

    assert main(["task", "handoff", "bind", first, "--json"]) == 0
    capsys.readouterr()
    binding = json.loads(
        (tmp_path / f"docs/tasks/.repoctl-state/resume/{first}.json").read_text(encoding="utf-8")
    )
    assert binding["schema_version"] == 5
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
    assert stale["resume_guidance"]["status"] == "inactive"
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
    assert selected["data"]["resume_guidance"]["status"] == "inactive"
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

    assert main(["task", "discovery", "add", filename, "--note", "canonical identity", "--json"]) == 0
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
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--chosen", "repos/chosen.py", "--json"]) == 0
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
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--chosen", "repos/chosen.py", "--json"]) == 0
    capsys.readouterr()

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["finish_ready"] is True
    assert payload["data"]["repo_changes"]["scope"]["unchosen_actual_paths"] == []
    assert payload["data"]["repo_changes"]["scope"]["unused_chosen_paths"] == ["chosen.py"]
    assert not any(warning["code"] == "task_chosen_scope_drift" for warning in payload["warnings"])
    assert not any(action.get("kind") == "task_scope_review" for action in payload["next_actions"])


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
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "transition_evidence_incomplete"
    assert task_path.read_bytes() == task_before
    assert not (tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json").exists()


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
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--chosen", "repos/chosen.py", "--json"]) == 0
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
    assert "command" not in action

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2
    finish_payload = json.loads(capsys.readouterr().out)
    assert finish_payload["problems"][0]["code"] == "baseline_conflict"
    finish_action = next(
        action for action in finish_payload["next_actions"] if action.get("kind") == "baseline_ownership_resolution"
    )
    assert finish_action == action
    assert finish_payload["data"]["action_inputs"]["baseline_conflicts"] == ["a.py", "b.py"]




def test_task_discovery_add_records_chosen_scope_and_note(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    text = replace_section(
        text,
        "Discovery",
        "- Candidate query: old routing search\n- Chosen files: none yet\n- Notes: none yet\n",
    )
    text = replace_section(
        text,
        "Execution Log",
        "- 2026-06-09T18:30:00Z: accumulated implementation context\n",
    )
    text = replace_section(
        text,
        "Verification",
        "- Previous manual observation remains useful.\n",
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--chosen",
            "repos/src/checkout.py",
            "--note",
            "retry behavior lives in checkout service",
            "--json",
        ]
    ) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "task.discovery.add"
    assert payload["data"]["update"]["chosen_files"]["added"] == ["repos/src/checkout.py"]
    assert payload["data"]["update"]["notes"]["added"] == ["retry behavior lives in checkout service"]
    assert payload["data"]["totals"]["chosen_file_count"] == 1
    assert "discovery" not in payload["data"]
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "- Chosen files: `repos/src/checkout.py`" in task_body
    assert "- Notes: `retry behavior lives in checkout service`" in task_body
    assert "Candidate query" not in task_body
    assert "Candidate files reviewed" not in task_body
    assert "Selected result evidence" not in task_body
    assert "accumulated implementation context" in task_body
    assert "Previous manual observation remains useful." in task_body

    assert main(["check", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert not any(warning["code"] == "missing_discovery_evidence" for warning in check_payload["warnings"])


def test_task_discovery_markdown_write_is_atomic(
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
            "--chosen",
            "repos/src/checkout.py",
            "--json",
        ]
    ) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert task_path.read_bytes() == original_task
    assert not outcome_path.exists()


def test_task_discovery_replaces_chosen_scope_with_reason(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--chosen", "repos/a.py", "--note", "first owner", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "discovery", "add", "T-20260609184046Z", "--replace-chosen", "repos/b.py", "--json"]) == 2
    missing_reason = json.loads(capsys.readouterr().out)
    assert missing_reason["problems"][0]["code"] == "missing_scope_change_reason"

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--replace-chosen", "repos/b.py", "--reason", "implementation moved", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["discovery"] == {
        "chosen_files": ["repos/b.py"],
        "notes": ["first owner"],
    }
    assert payload["data"]["update"]["chosen_files"] == {
        "mode": "replace",
        "added": ["repos/b.py"],
        "removed": ["repos/a.py"],
        "already_present": [],
    }
    assert any(action["command"] == "./scripts/repoctl task doctor T-20260609184046Z --json" for action in payload["next_actions"])
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "scope changed: removed repos/a.py; added repos/b.py; reason=implementation moved" in task_body


def test_task_discovery_rejects_existing_directories_but_allows_future_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "src").mkdir()
    (repo / "existing.py").write_text("value = 1\n", encoding="utf-8")
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--chosen", "repos/src", "--json"]) == 2
    directory_error = json.loads(capsys.readouterr().out)
    assert directory_error["problems"][0]["code"] == "discovery_path_is_directory"

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--chosen", "repos/new.py", "--json"]) == 0
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
    discovery = next(action for action in payload["next_actions"] if action.get("kind") == "discovery_input")
    assert "command" not in discovery
    assert discovery["path"] == "docs/tasks/T-20260609184046Z--alpha.md"
    assert discovery["source"] == "docs/tasks/T-20260609184046Z--alpha.md#Discovery"


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
    created = task_path.read_text(encoding="utf-8")
    created_handoff = find_section(created, "Handoff")
    created_handoff_body = created[created_handoff.body_start : created_handoff.end]
    assert 'document_language: "ko"' in created

    (tmp_path / "docs/repoctl.json").write_text('{"document_language":"en"}\n', encoding="utf-8")

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    started = task_path.read_text(encoding="utf-8")
    started_handoff = find_section(started, "Handoff")
    assert "작업을 시작" in started
    assert started[started_handoff.body_start : started_handoff.end] == created_handoff_body
    assert "task started." not in started

    assert main(["task", "finish", task_id, "--json"]) == 0
    finish_payload = json.loads(capsys.readouterr().out)
    archived = (tmp_path / finish_payload["data"]["new_path"]).read_text(encoding="utf-8")
    assert "작업을 완료함" in archived
    assert "Repoctl 게이트 요약" not in archived
    assert "## Verification\n\n- 선택 메모." in archived
    assert "## Last Active Handoff" in archived
    assert "## Closure" in archived
    assert "repoctl 관리 범위가 아님" in archived
    assert "task finished." not in archived


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
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    state_path = tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json"
    initial_state = state_path.read_bytes()
    (repo / "app.py").write_text("new\n", encoding="utf-8")

    assert main(["task", "block", "T-20260609184046Z", "--reason", "waiting for review", "--json"]) == 0
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

    created = task_path.read_text(encoding="utf-8")
    assert created.count("<!-- repoctl: generated-handoff -->") == 1
    assert not (tmp_path / "docs/tasks/.repoctl-state/handoff-origins").exists()
    assert "handoff_origin_commitment" not in resolve_task(tmp_path, task_id).frontmatter

    assert main(["task", "start", task_id, "--json"]) == 0
    started = json.loads(capsys.readouterr().out)
    assert any(warning["code"] == "task_handoff_generated_template" for warning in started["warnings"])
    assert not any(
        str(action.get("command") or "").startswith("./scripts/repoctl task handoff bind")
        for action in started["next_actions"]
    )
    assert task_path.read_text(encoding="utf-8").count("<!-- repoctl: generated-handoff -->") == 1

    assert main(["task", "handoff", "bind", task_id, "--json"]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "task_handoff_generated_template"
    assert not (tmp_path / f"docs/tasks/.repoctl-state/resume/{task_id}.json").exists()

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
    assert main(["task", "handoff", "bind", task_id, "--json"]) == 0
    rebound = json.loads(capsys.readouterr().out)
    assert rebound["data"]["resume_guidance"]["status"] == "current"



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


@pytest.mark.parametrize("old_version", (1, 2, 3, 4))
def test_old_handoff_binding_is_ignored_until_rebound(
    tmp_path: Path,
    monkeypatch,
    capsys,
    old_version: int,
) -> None:
    task_path, _repo, receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    _bind_handoff(capsys)
    binding = json.loads(receipt.read_text(encoding="utf-8"))
    binding["schema_version"] = old_version
    receipt.write_text(json.dumps(binding, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    guidance = _show_resume_guidance(capsys)
    assert guidance["status"] == "inactive"
    assert guidance["handoff"]["reason_codes"] == ["handoff_unbound"]
    assert guidance["handoff"]["active"] is False

    assert main(["task", "handoff", "bind", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert json.loads(receipt.read_text(encoding="utf-8"))["schema_version"] == 5
    assert task_path.is_file()


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
    assert guidance["status"] == "inactive"
    assert guidance["handoff"]["active"] is False
    assert "Next exact step" in guidance["handoff"]["body"]

    payload = _bind_handoff(capsys)
    assert payload["data"]["resume_guidance"]["status"] == "current"
    assert receipt.is_file()
    assert task_path.read_bytes() == before

    bound_receipt = receipt.read_bytes()
    assert main(["task", "block", "T-20260609184046Z", "--reason", "Waiting for an external decision.", "--json"]) == 0
    capsys.readouterr()
    assert receipt.read_bytes() == bound_receipt
    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert receipt.read_bytes() == bound_receipt
    assert start_payload["data"]["resume_guidance"]["status"] == "inactive"


def test_task_handoff_binding_tracks_each_structured_task_input(tmp_path: Path, monkeypatch, capsys) -> None:
    task_path, _repo, _receipt = _start_repo_task_with_resume_surface(tmp_path, monkeypatch, capsys)
    _bind_handoff(capsys)

    def assert_stale(input_name: str) -> None:
        guidance = _show_resume_guidance(capsys)
        assert guidance["status"] == "inactive"
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

    assert main(["task", "discovery", "add", "T-20260609184046Z", "--note", "app owner", "--json"]) == 0
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
    assert guidance["status"] == "inactive"
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
    assert guidance["status"] == "inactive"
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
    assert guidance["status"] == "inactive"
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
    assert guidance["status"] == "inactive"
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
        assert payload["data"]["resume_guidance"]["status"] == "inactive"


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
    assert payload["data"]["resume_guidance"]["status"] == "inactive"

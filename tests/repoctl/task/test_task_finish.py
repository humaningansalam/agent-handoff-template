from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import digest_data
from tools.repoctl.markdown import replace_section
from tools.repoctl.meta import shard_for_path
from tests.repoctl.task_lifecycle_helpers import (
    add_board_task,
    commit_all,
    init_committed_product_repo,
    init_product_repo,
    record_discovery,
    start_task_for_finish,
    task_text,
    write_json,
    write_repometa,
    write_verification,
    write_workspace,
)


def test_task_finish_uses_task_start_dirty_baseline_for_root_only_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "ops"'))
    repo = tmp_path / "repos"
    verification = write_verification(tmp_path, "non-product update verified\n")
    init_product_repo(repo)
    commit_all(repo)
    (repo / "preexisting.txt").write_text("already dirty before task start\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "finish", "docs/tasks/T-20260609184046Z--alpha.md", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["task_id"] == "T-20260609184046Z"
    assert payload["data"]["meta_gate"]["status"] == "skipped"
    assert payload["data"]["meta_gate"]["reason"] == "no_task_repo_changes"
    assert payload["data"]["meta_gate"]["preexisting_dirty_files"] == 1
    receipt = json.loads((tmp_path / payload["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["repo_id"] == ""
    archived = (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "non-product update verified" in archived


def test_task_finish_rejects_legacy_orphan_verification_without_mutation(
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
    evidence.write_text("PASS app.py\n", encoding="utf-8")
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
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--replace-chosen",
            "repos/other.py",
            "--reason",
            "move approved scope",
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

    task_path.write_text(
        replace_section(
            task_path.read_text(encoding="utf-8"),
            "Verification",
            "- Command: focused check\n- Result: pass\n",
        ),
        encoding="utf-8",
    )
    outcome_path = tmp_path / f"docs/tasks/.repoctl-state/discovery-outcomes/{task_id}.json"
    legacy = json.loads(outcome_path.read_text(encoding="utf-8"))
    legacy.pop("verification_subjects", None)
    legacy["schema_version"] = 1
    basis = {key: value for key, value in legacy.items() if key != "state_digest"}
    legacy["state_digest"] = digest_data(basis)
    outcome_path.write_text(json.dumps(legacy, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    tracked_paths = [
        task_path,
        tmp_path / "docs/BOARD.md",
        outcome_path,
        tmp_path / f"docs/tasks/.repoctl-state/{task_id}.json",
    ]
    before = {path: path.read_bytes() for path in tracked_paths}

    assert main(["task", "doctor", task_id, "--json"]) == 2
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["problems"][0]["code"] == "discovery_outcome_verification_reference_invalid"
    assert {path: path.read_bytes() for path in tracked_paths} == before

    assert main(["task", "finish", task_id, "--json"]) == 2
    rejected = json.loads(capsys.readouterr().out)
    assert rejected["problems"][0]["code"] == "discovery_outcome_verification_reference_invalid"
    assert {path: path.read_bytes() for path in tracked_paths} == before
    assert not (tmp_path / f"docs/tasks/.repoctl-state/completions/{task_id}.json").exists()
    assert not (tmp_path / f"docs/archive/tasks/{task_id}--alpha.md").exists()


def test_task_finish_changed_meta_gate_uses_explicit_task_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    verification = write_verification(tmp_path, "repo update verified\n")
    init_product_repo(repo, coverage=["*.py"])
    (repo / "preexisting.py").write_text("x = 1\n", encoding="utf-8")
    commit_all(repo)
    (repo / "preexisting.py").write_text("x = 2\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    write_repometa(repo, coverage=["*.py"], annotations={"task_new.py": {"role": "service", "purpose": "new task file", "topics": ["task"]}})
    (repo / "task_new.py").write_text("y = 1\n", encoding="utf-8")
    metadata_path = f"repos/.repometa/annotations/{shard_for_path('task_new.py')}.json"
    record_discovery(
        tmp_path,
        "T-20260609184046Z",
        query="task new",
        reviewed=f"repos/task_new.py, {metadata_path}",
        chosen=f"repos/task_new.py, {metadata_path}",
    )

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["meta_gate"]["status"] == "passed"
    assert payload["data"]["meta_gate"]["changed_files"] >= 1


def test_task_finish_allows_root_task_when_repo_head_changes_without_task_repo_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "ops"'))
    repo = tmp_path / "repos"
    verification = write_verification(tmp_path, "non-product update verified\n")
    init_product_repo(repo)
    commit_all(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    (repo / "other.txt").write_text("external commit\n", encoding="utf-8")
    subprocess.run(["git", "add", "other.txt"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "external"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["meta_gate"]["status"] == "skipped"
    assert payload["data"]["meta_gate"]["reason"] == "no_repo_changes"
    assert payload["data"]["finish_summary"]["observed_committed_changes"] == 1
    assert payload["data"]["finish_summary"]["observed_committed_files"] == ["repos/other.txt"]


def test_task_finish_still_blocks_repo_changes_after_dirty_baseline(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "docs"'))
    repo = tmp_path / "repos"
    verification = write_verification(tmp_path, "verified\n")
    init_product_repo(repo)
    commit_all(repo)
    (repo / "preexisting.txt").write_text("already dirty before task start\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    (repo / "new.py").write_text("print('new after task start')\n", encoding="utf-8")
    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_finish_treats_modified_dirty_baseline_file_as_task_change(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    verification = write_verification(tmp_path, "verified\n")
    init_product_repo(repo)
    commit_all(repo)
    (repo / "preexisting.txt").write_text("dirty before task start\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    record_discovery(
        tmp_path,
        "T-20260609184046Z",
        query="update preexisting file",
        reviewed="repos/preexisting.txt",
        chosen="repos/preexisting.txt",
    )
    (repo / "preexisting.txt").write_text("dirty before task start\nchanged during task\n", encoding="utf-8")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "baseline_conflict"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()


def test_repo_task_detects_task_start_dirty_paths_that_disappear_from_git_status(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
        .replace("- pending", "- Command: pytest\n- Result: pass")
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    init_product_repo(repo)
    (repo / "tracked.txt").write_text("committed\n", encoding="utf-8")
    commit_all(repo)
    (repo / "tracked.txt").write_text("dirty before task start\n", encoding="utf-8")
    (repo / "untracked.txt").write_text("dirty before task start\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    subprocess.run(["git", "restore", "tracked.txt"], cwd=repo, check=True)
    (repo / "untracked.txt").unlink()

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 1
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["problems"][0]["code"] == "baseline_conflict"
    assert doctor["data"]["action_inputs"]["baseline_conflicts"] == ["tracked.txt", "untracked.txt"]

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2
    finish = json.loads(capsys.readouterr().out)
    assert finish["problems"][0]["code"] == "baseline_conflict"

    assert main(
        [
            "task",
            "baseline",
            "resolve",
            "T-20260609184046Z",
            "--resolution",
            "tracked.txt=task",
            "--resolution",
            "untracked.txt=task",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 0


def test_workspace_task_blocks_finish_and_cancel_when_dirty_baseline_path_is_lost(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "ops"')
        .replace("- pending", "- Command: pytest\n- Result: pass")
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    init_product_repo(repo)
    (repo / "tracked.txt").write_text("committed\n", encoding="utf-8")
    commit_all(repo)
    (repo / "tracked.txt").write_text("dirty before task start\n", encoding="utf-8")
    verification = write_verification(tmp_path, "explicit cancel evidence\n")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    subprocess.run(["git", "restore", "tracked.txt"], cwd=repo, check=True)

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 1
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["problems"][0]["code"] == "workspace_baseline_conflict"
    assert doctor["data"]["action_inputs"]["baseline_conflicts"] == ["repos/tracked.txt"]
    assert not any("task baseline resolve" in action.get("command", "") for action in doctor["next_actions"])

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2
    finish = json.loads(capsys.readouterr().out)
    assert finish["problems"][0]["code"] == "workspace_baseline_conflict"
    assert not any("task baseline resolve" in action.get("command", "") for action in finish["next_actions"])

    assert main(["task", "cancel", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2
    canceled = json.loads(capsys.readouterr().out)
    assert canceled["problems"][0]["code"] == "workspace_baseline_conflict"

    assert main(
        [
            "task",
            "cancel",
            "T-20260609184046Z",
            "--verification-file",
            str(verification),
            "--allow-dirty-cancel",
            "--json",
        ]
    ) == 0


def test_task_doctor_and_finish_share_baseline_conflict_preflight(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n"})
    (repo / "app.py").write_text("value = 2\n", encoding="utf-8")
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
    record_discovery(tmp_path, "T-20260609184046Z", query="app value", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 3\n", encoding="utf-8")

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 1
    doctor_payload = json.loads(capsys.readouterr().out)
    assert doctor_payload["problems"][0]["code"] == "baseline_conflict"

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2
    finish_payload = json.loads(capsys.readouterr().out)
    assert finish_payload["problems"][0]["code"] == "baseline_conflict"


def test_task_doctor_and_finish_share_actual_scope_preflight(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "value = 1\n", "extra.py": "value = 1\n", "other.py": "value = 1\n"})
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
    record_discovery(tmp_path, "T-20260609184046Z", query="app value", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "extra.py").write_text("value = 2\n", encoding="utf-8")
    (repo / "other.py").write_text("value = 2\n", encoding="utf-8")

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 0
    doctor_payload = json.loads(capsys.readouterr().out)
    assert doctor_payload["problems"] == []
    assert doctor_payload["warnings"][0]["code"] == "task_chosen_scope_drift"
    assert doctor_payload["data"]["finish_ready"] is True

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2
    finish_payload = json.loads(capsys.readouterr().out)
    assert finish_payload["problems"][0]["code"] == "actual_changes_outside_chosen"
    assert not any(action.get("kind") == "task_scope_review" for action in finish_payload["next_actions"])
    assert any(action["label"] == "Inspect task repo changes" for action in finish_payload["next_actions"])


def test_task_finish_records_verification_and_archives_standalone(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z"))
    verification = write_verification(tmp_path, "- Command: pytest\n- Result: pass\n")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["old_path"] == "docs/tasks/T-20260609184046Z--alpha.md"
    assert payload["data"]["new_path"] == "docs/archive/tasks/T-20260609184046Z--alpha.md"
    assert payload["data"]["archived"] is True
    assert payload["data"]["completion_receipt"] == "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json"
    assert not (tmp_path / payload["data"]["old_path"]).exists()
    archived = (tmp_path / payload["data"]["new_path"]).read_text(encoding="utf-8")
    assert "status: done" in archived
    assert "Result: pass" in archived
    assert "Repoctl gate summary:" not in archived
    assert "Repo change evidence:" not in archived
    assert "task finished and verified.\n\n## Verification" in archived
    assert "## Last Active Handoff" in archived
    assert "## Closure" in archived
    assert "docs/tasks/T-20260609184046Z--alpha.md" not in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    receipt = json.loads((tmp_path / payload["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["schema"] == "repoctl.task.completion"
    assert receipt["task_id"] == "T-20260609184046Z"
    assert receipt["status"] == "done"
    assert receipt["task_path_at_completion"] == payload["data"]["new_path"]
    assert receipt["changed_entries"] == []
    assert receipt["schema_version"] == 3
    assert receipt["started_at"].endswith("Z")
    assert receipt["completed_event_at"].endswith("Z")
    assert receipt["repo_evidence"]["mode"] == "none"
    assert receipt["repo_evidence"]["attribution"] == "none"
    assert receipt["repo_evidence"]["path_transitions"] == []
    assert receipt["repo_evidence"]["git_available"] is False
    assert receipt["repo_evidence"]["meta_gate"]["reason"] == "no_repo_directory"
    assert receipt["repo_evidence"]["delta"]["changed_count"] == 0
    assert receipt["verification"]["source"] == "external_file"
    assert receipt["verification"]["source_sha256"].startswith("sha256:")
    assert receipt["verification"]["stored_sha256"].startswith("sha256:")




def test_task_finish_uses_task_verification_section(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing").replace("- pending", "- Command: pytest\n- Result: pass")
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    real_write_text = Path.write_text

    def reject_temp_verification(path: Path, *args: object, **kwargs: object) -> int:
        if path == Path("/tmp/T-20260609184046Z-verification.md"):
            raise AssertionError("task verification must not be copied through /tmp")
        return real_write_text(path, *args, **kwargs)

    monkeypatch.setattr(Path, "write_text", reject_temp_verification)

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    archived = (tmp_path / payload["data"]["new_path"]).read_text(encoding="utf-8")
    assert "Result: pass" in archived
    assert "status: done" in archived
    receipt = json.loads((tmp_path / payload["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["verification"]["source"] == "task_section"


def test_task_finish_strips_verification_artifact_title(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    verification = tmp_path / "verification.md"
    long_result = "verified-output-" + "x" * 5000
    verification.write_text(f"# Verification for T-20260609184046Z\n\n- Command: pytest\n- Result: pass\n- Output: {long_result}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    archived = (tmp_path / payload["data"]["new_path"]).read_text(encoding="utf-8")
    assert "# Verification for T-20260609184046Z" not in archived
    assert "- Command: pytest" in archived
    assert long_result in archived
    assert "status: done" in archived
    receipt = json.loads((tmp_path / payload["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["verification"]["truncated"] is False
    assert receipt["verification"]["normalized_sha256"] == receipt["verification"]["stored_sha256"]


def test_task_finish_blocks_on_changed_file_meta_errors(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    verification = write_verification(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, coverage=["src/**"])
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    record_discovery(
        tmp_path,
        "T-20260609184046Z",
        query="add service",
        reviewed="repos/src/service.py",
        chosen="repos/src/service.py",
    )
    (repo / "src").mkdir()
    (repo / "src/service.py").write_text("def run():\n    return 1\n", encoding="utf-8")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "annotation_required"

    assert any(action["label"] == "Add required metadata annotation" for action in payload["next_actions"])
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_finish_blocks_when_repo_exists_without_git(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    verification = write_verification(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    write_repometa(repo)
    (repo / "src.py").write_text("print('hello')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_git_unavailable"
    assert payload["problems"][0]["path"] == "repos"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_finish_blocks_repo_scoped_task_when_repo_directory_missing(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "backend"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    verification = write_verification(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_not_found"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_finish_blocks_repo_changes_without_area_and_repo_ref(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    verification = write_verification(tmp_path)
    repo = tmp_path / "repos"
    init_product_repo(repo)
    commit_all(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    start_task_for_finish(monkeypatch, capsys, tmp_path)
    (repo / "new.py").write_text("print('new')\n", encoding="utf-8")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_finish_blocks_non_monotonic_execution_log(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing").replace("- created", "- 29990101T000000Z: impossible future entry.")
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    verification = write_verification(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "execution_log_timestamp_order"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_finish_blocks_repo_change_without_discovery(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    verification = write_verification(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    start_task_for_finish(monkeypatch, capsys, tmp_path)
    (repo / "new.py").write_text("print('new')\n", encoding="utf-8")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "placeholder_discovery"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()




def test_task_finish_allows_no_repo_changes_only_when_repo_git_available(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    repo = tmp_path / "repos"
    init_committed_product_repo(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["meta_gate"] == {"status": "skipped", "reason": "no_repo_changes"}
    archived = (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "Repoctl gate summary:" not in archived
    assert "## Verification\n\nok\n" in archived




def test_task_finish_rolls_back_archive_when_board_write_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    real_atomic_write = __import__("tools.repoctl.cli", fromlist=["atomic_write"]).atomic_write

    def fail_board_write(path: Path, text: str) -> None:
        if path.name == "BOARD.md":
            raise OSError("simulated board write failure")
        real_atomic_write(path, text)

    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.atomic_write", fail_board_write)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert "docs/tasks/T-20260609184046Z--alpha.md" in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/tasks/.repoctl-state/archive/T-20260609184046Z.json").exists()


def test_task_finish_rejects_conflicting_archive_locator(tmp_path: Path, monkeypatch, capsys) -> None:
    task_id = "T-20260609184046Z"
    task_path = f"docs/tasks/{task_id}--alpha.md"
    write_workspace(tmp_path)
    add_board_task(tmp_path, f"{task_id}--alpha.md", task_text(task_id, status="doing"))
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    locator_path = tmp_path / f"docs/tasks/.repoctl-state/archive/{task_id}.json"
    locator_path.parent.mkdir(parents=True, exist_ok=True)
    conflicting_locator = {
        "schema": "repoctl.task.archive",
        "schema_version": 1,
        "task_id": task_id,
        "task_path": f"docs/archive/tasks/{task_id}--different.md",
    }
    locator_path.write_text(
        json.dumps(conflicting_locator, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "archive_locator_conflict"
    assert (tmp_path / task_path).exists()
    assert task_path in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    assert not (tmp_path / f"docs/archive/tasks/{task_id}--alpha.md").exists()
    assert json.loads(locator_path.read_text(encoding="utf-8")) == conflicting_locator

def test_task_finish_ignores_unrelated_full_repo_metadata_errors(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    unrelated = "backend/auth/token_service.py"
    annotation = {"role": "service", "purpose": "issue tokens", "topics": ["auth"], "declared_effects": ["crypto"]}
    init_product_repo(repo, annotations={unrelated: annotation})
    (repo / "backend/auth").mkdir(parents=True)
    (repo / unrelated).write_text("def issue():\n    return 'x'\n", encoding="utf-8")
    correct = shard_for_path(unrelated)
    wrong = next(shard for shard in "0123456789abcdef" if shard != correct)
    wrong_path = repo / ".repometa/annotations" / f"{wrong}.json"
    data = json.loads(wrong_path.read_text(encoding="utf-8"))
    data["annotations"][unrelated] = annotation
    write_json(wrong_path, data)
    commit_all(repo)
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    start_task_for_finish(monkeypatch, capsys, tmp_path)
    record_discovery(tmp_path, "T-20260609184046Z", query="repo new file", reviewed="repos/new.py", chosen="repos/new.py")
    (repo / "new.py").write_text("print('new')\n", encoding="utf-8")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["meta_gate"]["status"] == "passed"
    archived = (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "Repoctl gate summary:" not in archived
    assert "## Verification\n\nok\n" in archived


def test_task_finish_missing_verification_file_returns_json_error(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing").replace("- pending", "- Pending.")
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2

    omitted_payload = json.loads(capsys.readouterr().out)
    assert omitted_payload["problems"][0]["code"] == "missing_verification_file"

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(tmp_path / "missing.md"), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "missing_verification_file"
    assert any(action["label"] == "Complete task Verification" for action in payload["next_actions"])


def test_task_finish_rejects_empty_verification_file(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    verification = tmp_path / "verification.md"
    verification.write_text("\n\t\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "empty_verification_file"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_finish_rejects_verification_file_inside_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    repo = tmp_path / "repos"
    repo.mkdir()
    verification = repo / "verification.txt"
    verification.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "verification_file_inside_repo"


def test_task_finish_never_overwrites_existing_completion_receipt(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    verification = write_verification(tmp_path)
    receipt_path = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("existing receipt\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "completion_receipt_exists"
    assert receipt_path.read_text(encoding="utf-8") == "existing receipt\n"


def test_task_finish_blocks_when_repo_head_changed_after_start_with_clean_worktree(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "def run():\n    return 1\n"})
    task = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task)
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "change"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_head_changed_since_start"
    assert any(action["command"].startswith("./scripts/repoctl task finish T-20260609184046Z --use-committed-diff") for action in payload["next_actions"])


def test_task_finish_can_validate_committed_diff_from_recorded_start_head(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "def run():\n    return 1\n"})
    task = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task)
    verification = tmp_path / "verification.md"
    verification.write_text("verified after product commit\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, "T-20260609184046Z", query="app change", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "change"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    assert main(["task", "doctor", "T-20260609184046Z", "--use-committed-diff", "--json"]) == 0
    doctor = json.loads(capsys.readouterr().out)
    assert doctor["data"]["evidence_mode"] == "committed_range"
    assert doctor["data"]["repo_changes"]["scope"]["actual_paths"] == ["app.py"]
    assert doctor["data"]["repo_changes"]["scope"]["chosen_paths"] == ["app.py"]
    assert not any(warning["code"] == "task_chosen_scope_drift" for warning in doctor["warnings"])

    assert main(["task", "finish", "T-20260609184046Z", "--use-committed-diff", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["closure_scope"] == "task"
    assert payload["data"]["product_readiness"] == "not_evaluated"
    assert payload["data"]["meta_gate"]["status"] == "passed"
    assert payload["data"]["finish_summary"]["task_new_changes"] == 1
    assert payload["data"]["finish_summary"]["committed_range"]["base"]
    assert payload["data"]["finish_summary"]["committed_range"]["head"]
    receipt = json.loads((tmp_path / payload["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["changed_entries"] == [{"change": "modified", "path": "app.py"}]
    assert receipt["repo_evidence"]["delta"]["changed_count"] == 1


def test_task_finish_can_validate_first_commit_from_unborn_start(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_product_repo(repo)
    exclude = repo / ".git/info/exclude"
    exclude.write_text(exclude.read_text(encoding="utf-8") + "\n.repometa/\n", encoding="utf-8")
    task = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task)
    verification = write_verification(tmp_path, "verified first commit\n")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, "T-20260609184046Z", query="add app", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "first"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    assert main(["task", "finish", "T-20260609184046Z", "--use-committed-diff", "--verification-file", str(verification), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["finish_summary"]["committed_range"]["base"] == "<unborn>"
    receipt = json.loads((tmp_path / payload["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["changed_entries"] == [{"change": "added", "path": "app.py"}]


def test_task_finish_rejects_index_content_hidden_by_missing_worktree_path(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_product_repo(repo)
    exclude = repo / ".git/info/exclude"
    exclude.write_text(exclude.read_text(encoding="utf-8") + "\n.repometa/\n", encoding="utf-8")
    task = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task)
    verification = write_verification(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, "T-20260609184046Z", query="add app", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / "app.py").unlink()

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "stable_path_transition_noop"
    assert "status: doing" in (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")


def test_task_finish_committed_diff_blocks_initial_dirty_path_until_owned(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "def run():\n    return 1\n"})
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    task = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task)
    verification = tmp_path / "verification.md"
    verification.write_text("verified after product commit\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    record_discovery(tmp_path, "T-20260609184046Z", query="app change", reviewed="repos/app.py", chosen="repos/app.py")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "change"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    assert main(["task", "finish", "T-20260609184046Z", "--use-committed-diff", "--verification-file", str(verification), "--json"]) == 2
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["problems"][0]["code"] == "baseline_conflict"

    assert main(["task", "baseline", "resolve", "T-20260609184046Z", "--path", "repos/app.py", "--ownership", "task", "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "finish", "T-20260609184046Z", "--use-committed-diff", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    receipt = json.loads((tmp_path / payload["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["repo_evidence"]["attribution"] == "range_observed"
    assert receipt["repo_evidence"]["ownership"]["app.py"]["ownership"] == "task"


def test_task_baseline_resolve_previews_and_applies_multiple_paths_atomically(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"a.py": "a = 1\n", "b.py": "b = 1\n"})
    (repo / "a.py").write_text("a = 2\n", encoding="utf-8")
    (repo / "b.py").write_text("b = 2\n", encoding="utf-8")
    task = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    (repo / "a.py").write_text("a = 3\n", encoding="utf-8")
    (repo / "b.py").write_text("b = 3\n", encoding="utf-8")
    state_path = tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json"

    command = [
        "task",
        "baseline",
        "resolve",
        "T-20260609184046Z",
        "--resolution",
        "repos/a.py=task",
        "--resolution",
        "repos/b.py=task",
    ]
    assert main([*command, "--preview", "--json"]) == 0
    preview = json.loads(capsys.readouterr().out)
    assert preview["data"]["applied"] is False
    assert [item["path"] for item in preview["data"]["resolutions"]] == ["a.py", "b.py"]
    assert json.loads(state_path.read_text(encoding="utf-8"))["ownership"] == {}

    assert main([*command, "--resolution", "repos/missing.py=task", "--json"]) == 2
    failed = json.loads(capsys.readouterr().out)
    assert failed["problems"][0]["code"] == "baseline_path_not_initially_dirty"
    assert json.loads(state_path.read_text(encoding="utf-8"))["ownership"] == {}

    assert main([*command, "--json"]) == 0
    applied = json.loads(capsys.readouterr().out)
    assert applied["data"]["applied"] is True
    ownership = json.loads(state_path.read_text(encoding="utf-8"))["ownership"]
    assert {path: item["ownership"] for path, item in ownership.items()} == {"a.py": "task", "b.py": "task"}


def test_task_baseline_resolve_preserves_exact_repo_relative_prefix_collision(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"repos/app.py": "x = 1\n"})
    (repo / "repos/app.py").write_text("x = 2\n", encoding="utf-8")
    task = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    assert main([
        "task",
        "baseline",
        "resolve",
        "T-20260609184046Z",
        "--path",
        "repos/app.py",
        "--ownership",
        "task",
        "--json",
    ]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["resolutions"][0]["path"] == "repos/app.py"


def test_task_finish_committed_diff_blocks_non_ancestor_observed_head(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "def run():\n    return 1\n"})
    task = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task)
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    tree = subprocess.run(["git", "rev-parse", "HEAD^{tree}"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    rewritten = subprocess.run(["git", "commit-tree", tree, "-m", "rewritten root"], cwd=repo, check=True, text=True, stdout=subprocess.PIPE).stdout.strip()
    subprocess.run(["git", "reset", "--hard", rewritten], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    assert main(["task", "doctor", "T-20260609184046Z", "--use-committed-diff", "--json"]) == 1
    doctor_payload = json.loads(capsys.readouterr().out)
    assert doctor_payload["problems"][0]["code"] == "repo_history_rewritten"

    assert main(["task", "finish", "T-20260609184046Z", "--use-committed-diff", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_history_rewritten"
    assert not (tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json").exists()


def test_task_finish_committed_diff_blocks_invalid_start_head(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "def run():\n    return 1\n"})
    task = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task)
    verification = tmp_path / "verification.md"
    verification.write_text("verified after product commit\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    baseline = tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json"
    payload = json.loads(baseline.read_text(encoding="utf-8"))
    payload["initial"]["start_head"] = "deadbeefdeadbeefdeadbeefdeadbeefdeadbeef"
    baseline.write_text(json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    record_discovery(tmp_path, "T-20260609184046Z", query="app change", reviewed="repos/app.py", chosen="repos/app.py")
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "change"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    assert main(["task", "finish", "T-20260609184046Z", "--use-committed-diff", "--verification-file", str(verification), "--json"]) == 2

    error_payload = json.loads(capsys.readouterr().out)
    assert error_payload["problems"][0]["code"] == "repo_commit_range_unavailable"
    assert not (tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json").exists()




def test_task_finish_blocks_repo_scoped_no_changes_without_start_head(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "def run():\n    return 1\n"})
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_head_missing_at_start"

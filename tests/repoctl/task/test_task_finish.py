from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
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
    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["meta_gate"]["status"] == "skipped"
    assert payload["meta_gate"]["reason"] == "no_task_repo_changes"
    assert payload["meta_gate"]["preexisting_dirty_files"] == 1
    archived = (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "non-product update verified" in archived


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
    record_discovery(tmp_path, "T-20260609184046Z", query="task new", reviewed="repos/task_new.py", chosen="repos/task_new.py")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["meta_gate"]["status"] == "passed"
    assert payload["meta_gate"]["changed_files"] >= 1


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
    assert payload["meta_gate"]["status"] == "skipped"
    assert payload["meta_gate"]["reason"] == "no_repo_changes"


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
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "docs"'))
    repo = tmp_path / "repos"
    verification = write_verification(tmp_path, "verified\n")
    init_product_repo(repo)
    commit_all(repo)
    (repo / "preexisting.txt").write_text("dirty before task start\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0
    capsys.readouterr()
    (repo / "preexisting.txt").write_text("dirty before task start\nchanged during task\n", encoding="utf-8")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_finish_records_verification_and_archives_standalone(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    verification = write_verification(tmp_path, "- Command: pytest\n- Result: pass\n")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["old_path"] == "docs/tasks/T-20260609184046Z--alpha.md"
    assert payload["new_path"] == "docs/archive/tasks/T-20260609184046Z--alpha.md"
    assert payload["archived"] is True
    assert payload["completion_receipt"] == "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json"
    assert not (tmp_path / payload["old_path"]).exists()
    archived = (tmp_path / payload["new_path"]).read_text(encoding="utf-8")
    assert "status: done" in archived
    assert "Result: pass" in archived
    assert "- meta gate: skipped (no_repo_directory)" in archived
    assert "task finished and verified.\n\n## Verification" in archived
    assert "First command to run: `./scripts/repoctl check --json`" in archived
    assert f"First file to open: `{payload['new_path']}`" in archived
    assert "docs/tasks/T-20260609184046Z--alpha.md" not in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    receipt = json.loads((tmp_path / payload["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["schema"] == "repoctl.task.completion"
    assert receipt["task_id"] == "T-20260609184046Z"
    assert receipt["status"] == "done"
    assert receipt["task_path"] == payload["new_path"]
    assert receipt["archive_path"] == payload["new_path"]
    assert receipt["changed_entries"] == []
    assert receipt["verification"]["content_sha256"] == receipt["content_sha256"]


def test_task_finish_can_use_task_verification_section(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing").replace("- pending", "- Command: pytest\n- Result: pass")
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--use-task-verification", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    archived = (tmp_path / payload["new_path"]).read_text(encoding="utf-8")
    assert "Result: pass" in archived
    assert "status: done" in archived


def test_task_finish_strips_verification_artifact_title(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    verification = tmp_path / "verification.md"
    verification.write_text("# Verification for T-20260609184046Z\n\n- Command: pytest\n- Result: pass\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    archived = (tmp_path / payload["new_path"]).read_text(encoding="utf-8")
    assert "# Verification for T-20260609184046Z" not in archived
    assert "- Command: pytest" in archived
    assert "status: done" in archived


def test_task_finish_blocks_on_changed_file_meta_errors(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    verification = write_verification(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, coverage=["src/**"])
    (repo / "src").mkdir()
    (repo / "src/service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

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


def test_task_finish_blocks_backlog_origin_repo_change_without_discovery(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')

        .replace('repo_id: ""', 'repo_id: "main"')
        .replace("## Execution Log", "## Work Area\n\n- Backlog origin: `BL-test`\n\n## Execution Log")
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


def test_task_finish_allows_backlog_origin_repo_change_with_recorded_discovery(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    discovery = "## Discovery\n\n- Candidate query: `repoctl meta suggest --text new --json`\n- Candidate files reviewed: `repos/new.py`\n- Chosen files: `repos/new.py`\n\n"
    text = (
        task_text("T-20260609184046Z", status="todo")
        .replace('area: ""', 'area: "repo"')

        .replace('repo_id: ""', 'repo_id: "main"')
        .replace("## Execution Log", f"## Work Area\n\n- Backlog origin: `BL-test`\n\n{discovery}## Execution Log")
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    repo = tmp_path / "repos"
    init_committed_product_repo(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    start_task_for_finish(monkeypatch, capsys, tmp_path)
    (repo / "new.py").write_text("print('new')\n", encoding="utf-8")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["meta_gate"]["status"] == "passed"
    assert (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


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
    assert payload["meta_gate"] == {"status": "skipped", "reason": "no_repo_changes"}
    archived = (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "Repoctl gate summary:" in archived
    assert "- repo git: present" in archived
    assert "- meta gate: skipped (no_repo_changes)" in archived


def test_task_finish_does_not_update_board_before_archive_write_succeeds(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    real_atomic_write = __import__("tools.repoctl.cli", fromlist=["atomic_write"]).atomic_write

    def fail_archive_write(path: Path, text: str) -> None:
        if "docs/archive/tasks" in path.as_posix():
            raise OSError("simulated archive write failure")
        real_atomic_write(path, text)

    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.atomic_write", fail_archive_write)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert "docs/tasks/T-20260609184046Z--alpha.md" in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json").exists()


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


def test_task_finish_rolls_back_archive_when_source_unlink_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    real_unlink = Path.unlink

    def fail_source_unlink(path: Path, *args: object, **kwargs: object) -> None:
        if path.as_posix().endswith("docs/tasks/T-20260609184046Z--alpha.md"):
            raise OSError("simulated source unlink failure")
        real_unlink(path, *args, **kwargs)

    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(Path, "unlink", fail_source_unlink)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert "docs/tasks/T-20260609184046Z--alpha.md" in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json").exists()


def test_task_finish_summarizes_repo_status_for_humans(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"tracked.py": "old\n", "longer_name.py": "old\n"})
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    start_task_for_finish(monkeypatch, capsys, tmp_path)
    record_discovery(tmp_path, "T-20260609184046Z", query="repo status summary", reviewed="repos/tracked.py, repos/longer_name.py", chosen="repos/tracked.py, repos/longer_name.py")
    (repo / "tracked.py").write_text("new\n", encoding="utf-8")
    (repo / "longer_name.py").write_text("new\n", encoding="utf-8")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__/cache.pyc").write_bytes(b"cache")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    archived = (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "Repo status summary" in archived
    assert "Repoctl gate summary:" in archived
    assert "- repo git: present" in archived
    assert "- meta gate: passed (" in archived
    assert "- meta status: total=3 required=0 annotated=0 excluded=1 indexed_only=2" in archived
    assert "changed files checked)" in archived
    assert "Tracked changes" in archived
    assert "Generated residue" in archived
    assert "git diff --stat:\n longer_name.py" in archived
    assert "\n tracked.py" in archived


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
    assert payload["meta_gate"]["status"] == "passed"
    archived = (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "Repoctl gate summary:" in archived
    assert "- meta gate: passed" in archived


def test_task_finish_missing_verification_file_returns_json_error(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2

    omitted_payload = json.loads(capsys.readouterr().out)
    assert omitted_payload["problems"][0]["code"] == "missing_verification_file"

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(tmp_path / "missing.md"), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "missing_verification_file"
    assert any(action["label"] == "Create verification evidence" for action in payload["next_actions"])


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


def test_task_finish_blocks_backlog_origin_placeholder_discovery_variants(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "def run():\n    return 1\n"})
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    text = text.replace(
        "## Execution Log",
        "## Work Area\n\n- Backlog origin: `BL-test`\n\n## Discovery\n\n- Candidate query: N/A\n- Candidate files reviewed: none yet\n- Chosen files: none yet\n\n## Execution Log",
    )
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    start_task_for_finish(monkeypatch, capsys, tmp_path)
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "placeholder_discovery"


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


def test_task_finish_ignores_fake_start_head_outside_execution_log(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "def run():\n    return 1\n"})
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    text = text.replace("## Verification\n", "## Verification\n\n- repo head at start: `fake`\n")
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_head_missing_at_start"


def test_task_finish_blocks_repo_head_changed_even_with_leftover_changed_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_committed_product_repo(repo, {"app.py": "def run():\n    return 1\n"})
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_board_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "app.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "change"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / "notes.txt").write_text("leftover\n", encoding="utf-8")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_head_changed_since_start"

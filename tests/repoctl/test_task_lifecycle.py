from __future__ import annotations

import json
import hashlib
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.meta import shard_for_path
from tests.repoctl.test_check import add_task, init_repo, task_text, write_workspace


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_repometa(repo: Path, *, coverage: list[str] | None = None, annotations: dict[str, dict] | None = None) -> None:
    policy = {
        "schema_version": 1,
        "indexing": {"exclude": [".git/**", ".repometa/**", "__pycache__/**", "**/__pycache__/**"]},
        "vocab": {
            "roles": {"base": ["service", "adapter", "config", "test", "workflow"], "extend": []},
            "declared_effects": {"base": ["none", "db", "net", "fs", "ui", "time", "crypto", "config"], "extend": []},
        },
        "defaults": {"areas": {}, "topics": {}},
        "coverage": {"require_annotations": coverage or []},
    }
    write_json(repo / ".repometa/policy.json", policy)
    for shard in "0123456789abcdef":
        write_json(repo / ".repometa/annotations" / f"{shard}.json", {"schema_version": 1, "annotations": {}, "exclusions": {}})
    for rel, annotation in (annotations or {}).items():
        shard = shard_for_path(rel)
        path = repo / ".repometa/annotations" / f"{shard}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["annotations"][rel] = annotation
        write_json(path, data)


def start_task_for_finish(monkeypatch, capsys, root: Path, task_id: str = "T-20260609184046Z") -> None:
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)
    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()


def record_discovery(root: Path, task_id: str, *, query: str, reviewed: str, chosen: str) -> None:
    task_path = next((root / "docs/tasks").glob(f"{task_id}--*.md"))
    text = task_path.read_text(encoding="utf-8")
    discovery = f"## Discovery\n\n- Candidate query: `{query}`\n- Candidate files reviewed: `{reviewed}`\n- Chosen files: `{chosen}`\n\n"
    if "## Discovery" in text:
        start = text.index("## Discovery")
        end = text.index("## Execution Log")
        text = text[:start] + discovery + text[end:]
    else:
        text = text.replace("## Execution Log", discovery + "## Execution Log", 1)
    task_path.write_text(text, encoding="utf-8")


def test_task_start_changes_status_to_doing(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "doing"
    text = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "status: doing" in text
    assert "task started" in text
    assert "First command to run: `./scripts/repoctl task list --json`" in text


def test_task_show_and_log_append_use_repoctl_lifecycle_boundary(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "log", "append", "T-20260609184046Z", "checked worker output", "--json"]) == 0
    log_payload = json.loads(capsys.readouterr().out)
    assert log_payload["timestamp"].endswith("Z")
    text = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert f"- {log_payload['timestamp']}: checked worker output" in text

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["ok"] is True
    assert show_payload["task"]["id"] == "T-20260609184046Z"
    assert "checked worker output" in show_payload["body"]


def test_task_commands_accept_slugged_task_file_id(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "show", "T-20260609184046Z--alpha", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["task"]["id"] == "T-20260609184046Z"

    assert main(["task", "start", "T-20260609184046Z--alpha.md", "--json"]) == 0
    start_payload = json.loads(capsys.readouterr().out)
    assert start_payload["status"] == "doing"


def test_task_discovery_add_records_structured_scope_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(
        [
            "task",
            "discovery",
            "add",
            "T-20260609184046Z",
            "--query",
            "repoctl meta suggest --text checkout retry",
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
    assert payload["data"]["discovery"]["chosen_files"] == ["repos/src/checkout.py"]
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "- Candidate query: `repoctl meta suggest --text checkout retry`" in task_body
    assert "  - `repos/tests/test_checkout.py`" in task_body
    assert "- Notes: `retry behavior lives in checkout service`" in task_body

    assert main(["check", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    assert not any(warning["code"] == "missing_discovery_evidence" for warning in check_payload["warnings"])


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


def test_task_create_start_returns_started_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "started-task", "--start", "Started Task", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["started"] is True
    assert payload["status"] == "doing"
    assert "status: doing" in (tmp_path / payload["path"]).read_text(encoding="utf-8")


def test_task_create_blocks_when_repo_ref_uses_non_repo_area(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "repo-docs", "--area", "docs", "--repo-ref", "repos", "Update repo docs", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_ref_non_repo_area"


def test_task_create_blocks_root_repo_ref_alias(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "root-ref", "--area", "ops", "--repo-ref", "root", "Root ref", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_repo_ref"


def test_backlog_promotion_uses_repo_id_not_repo_ref_as_selector(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["backlog", "add", "Add product feature", "--json"]) == 0
    backlog_id = json.loads(capsys.readouterr().out)["data"]["item"]["id"]

    assert main(["task", "create", "--backlog-id", backlog_id, "--slug", "product-feature", "--area", "repo", "--repo-id", "main", "Add product feature", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    task_text_body = (tmp_path / payload["path"]).read_text(encoding="utf-8")
    assert 'repo_id: "main"' in task_text_body
    assert 'repo_ref: ""' in task_text_body
    assert "- Repository: `main`" in task_text_body


def test_task_start_blocks_repo_scoped_task_without_repo_git(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "repos").mkdir()
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"
    assert "status: todo" in (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")


def test_task_start_fails_on_dirty_repo_by_default_for_repo_scoped_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_dirty"
    assert "status: todo" in (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")


def test_task_start_records_dirty_repo_for_root_task_without_force(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "docs"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "doing"
    assert payload["repo_changes"]["preexisting_dirty"] == 1
    assert payload["repo_changes"]["task_new"] == 0
    assert payload["warnings"][0]["code"] == "root_task_repo_dirty_recorded"
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "dirty repo state recorded" in task_body
    assert (tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json").is_file()


def test_task_start_force_dirty_records_dirty_files(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / "dirty.txt").write_text("dirty\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty"]) == 0

    text = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "dirty repo state recorded" in text
    assert "dirty.txt" in text
    assert (tmp_path / "docs/tasks/.repoctl-state/T-20260609184046Z.json").is_file()


def test_task_show_and_doctor_report_task_new_changed_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"'))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    (repo / "changed.py").write_text("print('changed')\n", encoding="utf-8")

    assert main(["task", "show", "T-20260609184046Z", "--json"]) == 0
    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["repo_changes"]["task_new_files"] == ["changed.py"]

    assert main(["task", "doctor", "T-20260609184046Z", "--json"]) == 0
    doctor_payload = json.loads(capsys.readouterr().out)
    assert doctor_payload["data"]["repo_changes"]["task_new_files"] == ["changed.py"]


def test_task_finish_uses_task_start_dirty_baseline_for_root_only_task(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "ops"'))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("non-product update verified\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
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


def test_task_finish_allows_root_task_when_repo_head_changes_without_task_repo_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "ops"'))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("non-product update verified\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "docs"'))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "docs"'))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("- Command: pytest\n- Result: pass\n", encoding="utf-8")
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--use-task-verification", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    archived = (tmp_path / payload["new_path"]).read_text(encoding="utf-8")
    assert "Result: pass" in archived
    assert "status: done" in archived


def test_task_finish_strips_verification_artifact_title(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("# Verification for T-20260609184046Z\n\n- Command: pytest\n- Result: pass\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    archived = (tmp_path / payload["new_path"]).read_text(encoding="utf-8")
    assert "# Verification for T-20260609184046Z" not in archived
    assert "- Command: pytest" in archived
    assert "status: done" in archived


def test_task_cancel_records_verification_and_archives_standalone(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "cancel.md"
    verification.write_text("- Reason: opened by mistake\n- Result: cancel requested\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "cancel", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "canceled"
    assert payload["old_path"] == "docs/tasks/T-20260609184046Z--alpha.md"
    assert payload["new_path"] == "docs/archive/tasks/T-20260609184046Z--alpha.md"
    assert not (tmp_path / payload["old_path"]).exists()
    archived = (tmp_path / payload["new_path"]).read_text(encoding="utf-8")
    assert "status: canceled" in archived
    assert "opened by mistake" in archived
    assert "task canceled with verification evidence" in archived
    assert "- meta gate: skipped (task_canceled)" in archived


def test_task_cancel_blocks_task_scoped_repo_changes_by_default(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "cancel.md"
    verification.write_text("- Reason: superseded\n- Result: cancel requested\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / "leftover.py").write_text("print('leftover')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "cancel", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_changes_on_cancel"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_cancel_allows_explicit_dirty_cancel_with_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "cancel.md"
    verification.write_text("- Reason: superseded\n- Residue: repos/leftover.py intentionally remains for operator review\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    (repo / "leftover.py").write_text("print('leftover')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "cancel", "T-20260609184046Z", "--verification-file", str(verification), "--allow-dirty-cancel", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "task.cancel"
    assert payload["data"]["cancel_gate"]["task_new_changes"] == 1
    archived = (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "task canceled with verification evidence" in archived
    assert "task_new_changes=1" in archived
    assert "docs/tasks/T-20260609184046Z--alpha.md" not in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_block_records_evidence_and_keeps_board_entry(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "blocker.md"
    verification.write_text("- Blocker: screenshot acceptance failed\n- Evidence: mobile viewport still blank\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "block", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "blocked"
    task_body = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "status: blocked" in task_body
    assert "screenshot acceptance failed" in task_body
    assert "task blocked with evidence" in task_body
    assert "./scripts/repoctl task doctor T-20260609184046Z --json" in task_body
    assert "docs/tasks/T-20260609184046Z--alpha.md" in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_finish_missing_verification_file_reports_example(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "missing_verification_file"


def test_task_lifecycle_keeps_created_document_language_when_workspace_setting_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "docs/repoctl.json").write_text('{"document_language":"ko"}\n', encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--slug", "korean-lifecycle", "Korean Lifecycle", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    task_id = payload["task_id"]
    task_path = tmp_path / payload["path"]
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
    archived = (tmp_path / finish_payload["new_path"]).read_text(encoding="utf-8")
    assert "작업을 검증하고 완료함" in archived
    assert "Repoctl 게이트 요약" in archived
    assert "추가 작업 없음; 작업이 완료됨" in archived
    assert "task finished and verified" not in archived


def test_task_finish_blocks_on_changed_file_meta_errors(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo, coverage=["src/**"])
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "meta"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_not_found"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_finish_blocks_repo_changes_without_area_and_repo_ref(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    start_task_for_finish(monkeypatch, capsys, tmp_path)
    (repo / "new.py").write_text("print('new')\n", encoding="utf-8")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["meta_gate"]["status"] == "passed"
    assert (tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_finish_allows_no_repo_changes_only_when_repo_git_available(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="todo"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    subprocess.run(["git", "add", ".repometa"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
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


def test_task_finish_summarizes_repo_status_for_humans(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    write_repometa(repo)
    (repo / "tracked.py").write_text("old\n", encoding="utf-8")
    (repo / "longer_name.py").write_text("old\n", encoding="utf-8")
    subprocess.run(["git", "add", ".repometa", "tracked.py", "longer_name.py"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    unrelated = "backend/auth/token_service.py"
    (repo / "backend/auth").mkdir(parents=True)
    (repo / unrelated).write_text("def issue():\n    return 'x'\n", encoding="utf-8")
    annotation = {"role": "service", "purpose": "issue tokens", "topics": ["auth"], "declared_effects": ["crypto"]}
    write_repometa(repo, annotations={unrelated: annotation})
    correct = shard_for_path(unrelated)
    wrong = next(shard for shard in "0123456789abcdef" if shard != correct)
    wrong_path = repo / ".repometa/annotations" / f"{wrong}.json"
    data = json.loads(wrong_path.read_text(encoding="utf-8"))
    data["annotations"][unrelated] = annotation
    write_json(wrong_path, data)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "-c", "user.email=a@example.com", "-c", "user.name=A", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
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
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(tmp_path / "missing.md"), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "missing_verification_file"
    assert payload["problems"][0]["code"] == "missing_verification_file"
    assert any(action["label"] == "Create verification evidence" for action in payload["next_actions"])


def test_task_finish_rejects_empty_verification_file(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    verification = tmp_path / "verification.md"
    verification.write_text("\n\t\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "empty_verification_file"
    assert payload["problems"][0]["code"] == "empty_verification_file"
    assert (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").exists()


def test_task_finish_rejects_verification_file_inside_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task_text("T-20260609184046Z", status="doing"))
    repo = tmp_path / "repos"
    repo.mkdir()
    verification = repo / "verification.txt"
    verification.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "verification_file_inside_repo"
    assert payload["problems"][0]["code"] == "verification_file_inside_repo"


def test_json_argparse_errors_are_machine_readable(capsys) -> None:
    assert main(["task", "finish", "T-20260609184046Z", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "missing_verification_file"
    assert payload["problems"][0]["code"] == "missing_verification_file"


def test_task_finish_child_does_not_move_file(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="doing", parent="T-20260609184046Z"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n- docs/tasks/T-20260609184047Z--child.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184047Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["archived"] is False
    assert payload["new_path"] == "docs/tasks/T-20260609184047Z--child.md"
    assert (tmp_path / payload["new_path"]).exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184047Z--child.md").exists()
    assert "docs/tasks/T-20260609184047Z--child.md" not in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_finish_child_rolls_back_task_when_board_write_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    child_path = add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="doing", parent="T-20260609184046Z"))
    original_child = child_path.read_text(encoding="utf-8")
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n- docs/tasks/T-20260609184047Z--child.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    real_atomic_write = __import__("tools.repoctl.cli", fromlist=["atomic_write"]).atomic_write

    def fail_board_write(path: Path, text: str) -> None:
        if path.name == "BOARD.md":
            raise OSError("simulated board write failure")
        real_atomic_write(path, text)

    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.atomic_write", fail_board_write)

    assert main(["task", "finish", "T-20260609184047Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "io_error"
    assert child_path.read_text(encoding="utf-8") == original_child
    assert "docs/tasks/T-20260609184047Z--child.md" in (tmp_path / "docs/BOARD.md").read_text(encoding="utf-8")


def test_task_finish_parent_blocks_when_live_child_exists(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="doing", parent="T-20260609184046Z"))
    verification = tmp_path / "verification.md"
    verification.write_text("ok\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "live_children_block_finish"


def test_task_finish_parent_archives_non_live_child_with_archive_handoff(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="done", parent="T-20260609184046Z"))
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("parent verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    child_archive = tmp_path / "docs/archive/tasks/T-20260609184047Z--child.md"
    assert payload["archived"] is True
    assert child_archive.exists()
    assert not (tmp_path / "docs/tasks/T-20260609184047Z--child.md").exists()
    child_text = child_archive.read_text(encoding="utf-8")
    assert "First file to open: `docs/archive/tasks/T-20260609184047Z--child.md`" in child_text


def test_task_finish_parent_restores_child_receipt_when_board_write_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    child_path = add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="done", parent="T-20260609184046Z"))
    child_text = child_path.read_text(encoding="utf-8")
    child_hash = "sha256:" + hashlib.sha256(child_text.encode("utf-8")).hexdigest()
    receipt_path = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184047Z.json"
    write_json(
        receipt_path,
        {
            "schema": "repoctl.task.completion",
            "schema_version": 1,
            "task_id": "T-20260609184047Z",
            "repo_id": "",
            "status": "done",
            "completed_at": "2026-06-09T18:40:47Z",
            "task_path": "docs/tasks/T-20260609184047Z--child.md",
            "archive_path": "",
            "content_sha256": child_hash,
            "changed_entries": [],
            "verification": {
                "task_path": "docs/tasks/T-20260609184047Z--child.md",
                "archive_path": "",
                "content_sha256": child_hash,
            },
        },
    )
    original_receipt = receipt_path.read_text(encoding="utf-8")
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("parent verified\n", encoding="utf-8")
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
    assert child_path.exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184047Z--child.md").exists()
    assert receipt_path.read_text(encoding="utf-8") == original_receipt


def test_task_finish_parent_reports_corrupt_child_receipt(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    add_task(tmp_path, "T-20260609184046Z--parent.md", task_text("T-20260609184046Z", status="doing"))
    add_task(tmp_path, "T-20260609184047Z--child.md", task_text("T-20260609184047Z", status="done", parent="T-20260609184046Z"))
    receipt_path = tmp_path / "docs/tasks/.repoctl-state/completions/T-20260609184047Z.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text("{not json\n", encoding="utf-8")
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--parent.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("parent verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_completion_receipt"
    assert (tmp_path / "docs/tasks/T-20260609184047Z--child.md").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260609184047Z--child.md").exists()


def test_task_finish_blocks_when_repo_head_changed_after_start_with_clean_worktree(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    task = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", task)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
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
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    text = text.replace(
        "## Execution Log",
        "## Work Area\n\n- Backlog origin: `BL-test`\n\n## Discovery\n\n- Candidate query: N/A\n- Candidate files reviewed: none yet\n- Chosen files: none yet\n\n## Execution Log",
    )
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    start_task_for_finish(monkeypatch, capsys, tmp_path)
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "placeholder_discovery"


def test_task_finish_blocks_backlog_origin_backticked_placeholder_discovery(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    text = text.replace(
        "## Execution Log",
        "## Work Area\n\n- Backlog origin: `BL-test`\n\n## Discovery\n\n- Candidate query: `repoctl index code`\n- Candidate files reviewed: `repos/app.py`\n- Chosen files: `N/A`\n\n## Execution Log",
    )
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
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
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_head_missing_at_start"


def test_task_finish_allows_non_repo_area_when_repo_head_changed_after_start(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "docs"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--json"]) == 0
    capsys.readouterr()
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "change"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["meta_gate"]["status"] == "skipped"
    assert payload["meta_gate"]["reason"] == "no_repo_changes"


def test_task_finish_ignores_fake_start_head_outside_execution_log(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    text = text.replace("## Verification\n", "## Verification\n\n- repo head at start: `fake`\n")
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("verified\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", "T-20260609184046Z", "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_head_missing_at_start"


def test_task_finish_blocks_repo_head_changed_even_with_leftover_changed_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    text = task_text("T-20260609184046Z", status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    (tmp_path / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n- docs/tasks/T-20260609184046Z--alpha.md\n\n## Backlog\n", encoding="utf-8")
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


def test_task_start_force_dirty_refreshes_doing_task_repo_head(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    text = task_text("T-20260609184046Z", status="doing").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, "T-20260609184046Z--alpha.md", text)
    repo = tmp_path / "repos"
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo)
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", "T-20260609184046Z", "--force-dirty", "--json"]) == 0

    refreshed = (tmp_path / "docs/tasks/T-20260609184046Z--alpha.md").read_text(encoding="utf-8")
    assert "repo head at start:" in refreshed

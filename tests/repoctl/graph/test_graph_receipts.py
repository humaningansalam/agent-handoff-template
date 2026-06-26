from __future__ import annotations
from tests.repoctl.graph.test_graph_build import _sha256_text, _snapshot

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import file_id
from tests.repoctl.workspace.test_check import add_task, task_text, write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import commit_all, init_repo, write_settings


def test_graph_build_consumes_task_completion_receipts(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    commit_all(repo)
    task_id = "T-20260609184046Z"
    task = task_text(task_id, status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, f"{task_id}--alpha.md", task)
    (tmp_path / "docs/BOARD.md").write_text(f"# BOARD\n\n## Board\n\n- docs/tasks/{task_id}--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    task_path = tmp_path / "docs/tasks" / f"{task_id}--alpha.md"
    task_text_value = task_path.read_text(encoding="utf-8")
    discovery = "## Discovery\n\n- Candidate query: `run`\n- Candidate files reviewed: `repos/app.py`\n- Chosen files: `repos/app.py`\n\n"
    task_path.write_text(task_text_value.replace("## Execution Log", discovery + "## Execution Log", 1), encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("- Command: pytest\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0
    finish_payload = json.loads(capsys.readouterr().out)
    receipt = json.loads((tmp_path / finish_payload["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["repo_id"] == "main"
    assert receipt["changed_entries"] == [{"change": "modified", "path": "app.py"}]

    assert main(["graph", "build", "--repo-id", "main", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert any(source["kind"] == "task_completion" and source["assertion"] == "recorded" for source in snapshot["sources"])
    task_node_id = f"task:{task_id}"
    assert any(node["id"] == task_node_id and node["kind"] == "task" for node in snapshot["nodes"])
    assert any(node["kind"] == "change_event" for node in snapshot["nodes"])
    assert any(node["kind"] == "artifact" for node in snapshot["nodes"])
    assert any(edge["kind"] == "TASK_RECORDED_CHANGE" and edge["from"] == task_node_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "CHANGE_AFFECTED_FILE" and edge["to"] == file_id("main", "app.py") for edge in snapshot["edges"])
    assert any(edge["kind"] == "TASK_VERIFIED_BY" and edge["from"] == task_node_id for edge in snapshot["edges"])


def test_graph_receipt_edges_preserve_deleted_and_renamed_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "new.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    archive_path = tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md"
    archive_text = task_text("T-20260609184046Z", status="done")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(archive_text, encoding="utf-8")
    archive_hash = _sha256_text(archive_text)
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True)
    receipt = {
        "schema": "repoctl.task.completion",
        "schema_version": 1,
        "task_id": "T-20260609184046Z",
        "repo_id": "main",
        "status": "done",
        "completed_at": "2026-06-09T18:40:46Z",
        "task_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
        "archive_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
        "content_sha256": archive_hash,
        "changed_entries": [
            {"change": "deleted", "path": "deleted.py"},
            {"change": "renamed", "path": "new.py", "old_path": "old.py"},
        ],
        "verification": {
            "task_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
            "archive_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
            "content_sha256": archive_hash,
        },
    }
    (receipt_dir / "T-20260609184046Z.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert any(node["id"] == file_id("main", "deleted.py") and node["facts"]["receipt"]["present_in_current_inventory"] is False for node in snapshot["nodes"])
    assert any(node["id"] == file_id("main", "old.py") and node["facts"]["receipt"]["present_in_current_inventory"] is False for node in snapshot["nodes"])
    assert any(edge["kind"] == "CHANGE_AFFECTED_FILE" and edge["to"] == file_id("main", "deleted.py") and edge["facts"]["role"] == "path" for edge in snapshot["edges"])
    assert any(edge["kind"] == "CHANGE_AFFECTED_FILE" and edge["to"] == file_id("main", "old.py") and edge["facts"]["role"] == "old_path" for edge in snapshot["edges"])


def test_graph_ignores_invalid_receipt_for_other_repo_but_rejects_selected_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("def web():\n    return 1\n", encoding="utf-8")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "T-20260609184046Z.json").write_text(json.dumps({"schema": "future", "schema_version": 99, "repo_id": "api"}) + "\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--repo-id", "web", "--json"]) == 0
    capsys.readouterr()

    (receipt_dir / "T-20260609184047Z.json").write_text(
        json.dumps({"schema": "repoctl.task.completion", "schema_version": 1, "repo_id": "web", "task_id": "BAD", "status": "banana"}) + "\n",
        encoding="utf-8",
    )
    assert main(["graph", "build", "--repo-id", "web", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_completion_receipt"


def test_graph_rejects_receipt_with_fake_hash(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    archive_path = tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(task_text("T-20260609184046Z", status="done"), encoding="utf-8")
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True)
    receipt = {
        "schema": "repoctl.task.completion",
        "schema_version": 1,
        "task_id": "T-20260609184046Z",
        "repo_id": "main",
        "status": "done",
        "completed_at": "2026-06-09T18:40:46Z",
        "task_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
        "archive_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
        "content_sha256": "sha256:" + "a" * 64,
        "changed_entries": [{"change": "modified", "path": "app.py"}],
        "verification": {
            "task_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
            "archive_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
            "content_sha256": "sha256:" + "a" * 64,
        },
    }
    (receipt_dir / "T-20260609184046Z.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_completion_receipt"


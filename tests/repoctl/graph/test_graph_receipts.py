from __future__ import annotations
from tests.repoctl.graph.test_graph_build import _sha256_text, _snapshot

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import file_id
from tools.repoctl.graph_store import materialize_graph
from tools.repoctl.repositories import require_repo_target
from tests.repoctl.workspace.test_check import add_task, task_text, write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import commit_all, init_repo, write_settings


def _receipt(task_id: str, *, repo_id: str, task_path: str, content_sha256: str, changed_entries: list[dict[str, str]]) -> dict[str, object]:
    return {
        "schema": "repoctl.task.completion",
        "schema_version": 2,
        "task_id": task_id,
        "repo_id": repo_id,
        "status": "done",
        "completed_at": "2026-06-09T18:40:46Z",
        "task_path_at_completion": task_path,
        "content_sha256": content_sha256,
        "changed_entries": changed_entries,
        "repo_evidence": {
            "mode": "working_tree_diff",
            "attribution": "task_working_tree",
            "start_head": "a" * 40,
            "observed_head": "a" * 40,
            "diff_fingerprint_sha256": "sha256:" + "b" * 64,
        },
        "verification": {
            "source": "task_section",
            "source_sha256": "sha256:" + "c" * 64,
            "normalization": "normalize_final_newline",
            "normalized_sha256": "sha256:" + "c" * 64,
            "stored_sha256": "sha256:" + "c" * 64,
            "truncated": False,
        },
    }


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
    assert main(
        [
            "task",
            "discovery",
            "add",
            task_id,
            "--query",
            "run implementation",
            "--reviewed",
            "repos/app.py",
            "--chosen",
            "repos/app.py",
            "--json",
        ]
    ) == 0
    capsys.readouterr()
    verification = tmp_path / "verification.md"
    verification.write_text("- Command: pytest\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0
    finish_payload = json.loads(capsys.readouterr().out)
    receipt = json.loads((tmp_path / finish_payload["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["repo_id"] == "main"
    assert receipt["changed_entries"] == [{"change": "modified", "path": "app.py"}]

    assert main(["graph", "build", "--repo-id", "main", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert any(source["kind"] == "task_completion" and source["assertion"] == "recorded" for source in snapshot["sources"])
    task_node_id = f"task:{task_id}"
    assert any(node["id"] == task_node_id and node["kind"] == "task" for node in snapshot["nodes"])
    assert any(node["kind"] == "change_event" for node in snapshot["nodes"])
    assert any(node["kind"] == "artifact" for node in snapshot["nodes"])
    assert any(edge["kind"] == "TASK_RECORDED_CHANGE" and edge["from"] == task_node_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "TASK_RECORDED_CHANGE" and edge["facts"]["attribution"] == "task_working_tree" for edge in snapshot["edges"])
    assert any(edge["kind"] == "CHANGE_AFFECTED_FILE" and edge["to"] == file_id("main", "app.py") for edge in snapshot["edges"])
    assert any(edge["kind"] == "TASK_VERIFIED_BY" and edge["from"] == task_node_id for edge in snapshot["edges"])

    task_path = receipt["task_path_at_completion"]
    assert main(["graph", "query", "--task", task_id, "--json"]) == 0
    task_result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert any(path["edge"] == "CHANGE_AFFECTED_FILE" for path in task_result["paths"])
    assert any(item["selector"] == {"kind": "document", "value": task_path} for item in task_result["continuations"])
    task_continuation = next(item for item in task_result["continuations"] if item["selector"] == {"kind": "task", "value": task_id})
    assert "task.show" in task_continuation["actions"]

    assert main(["graph", "query", "--artifact", task_path, "--json"]) == 0
    artifact_result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert any(item["selector"] == {"kind": "task", "value": task_id} for item in artifact_result["continuations"])

    artifact = tmp_path / task_path
    artifact.write_text(artifact.read_text(encoding="utf-8") + "\npost-build change\n", encoding="utf-8")
    assert main(["graph", "query", "--task", task_id, "--full", "--json"]) == 0
    stale = json.loads(capsys.readouterr().out)
    assert stale["data"]["freshness"]["status"] == "stale"
    assert stale["data"]["freshness"]["changed_root_paths"] == [task_path]


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
    receipt = _receipt(
        "T-20260609184046Z",
        repo_id="main",
        task_path="docs/tasks/T-20260609184046Z--alpha.md",
        content_sha256=archive_hash,
        changed_entries=[
            {"change": "deleted", "path": "deleted.py"},
            {"change": "renamed", "path": "new.py", "old_path": "old.py"},
        ],
    )
    (receipt_dir / "T-20260609184046Z.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert any(node["id"] == file_id("main", "deleted.py") and node["facts"]["receipt"]["present_in_current_inventory"] is False for node in snapshot["nodes"])
    assert any(node["id"] == file_id("main", "old.py") and node["facts"]["receipt"]["present_in_current_inventory"] is False for node in snapshot["nodes"])
    assert any(edge["kind"] == "CHANGE_AFFECTED_FILE" and edge["to"] == file_id("main", "deleted.py") and edge["facts"]["role"] == "path" for edge in snapshot["edges"])
    assert any(edge["kind"] == "CHANGE_AFFECTED_FILE" and edge["to"] == file_id("main", "old.py") and edge["facts"]["role"] == "old_path" for edge in snapshot["edges"])
    task_node = next(node for node in snapshot["nodes"] if node["id"] == "task:T-20260609184046Z")
    assert task_node["facts"]["receipt"]["task_path_at_completion"] == "docs/tasks/T-20260609184046Z--alpha.md"
    assert any(
        node["kind"] == "artifact"
        and node["identity"]["path"] == "docs/archive/tasks/T-20260609184046Z--alpha.md"
        for node in snapshot["nodes"]
    )

    assert main(["graph", "query", "--task", "T-20260609184046Z", "--json"]) == 0
    task_result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert any(
        item["selector"] == {"kind": "document", "value": "docs/archive/tasks/T-20260609184046Z--alpha.md"}
        for item in task_result["continuations"]
    )

    assert main(
        [
            "graph",
            "query",
            "--artifact",
            "docs/archive/tasks/T-20260609184046Z--alpha.md",
            "--json",
        ]
    ) == 0
    artifact_result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert artifact_result["query_status"] == "found"


def test_graph_localizes_invalid_receipt_to_selected_repo_task_history(tmp_path: Path, monkeypatch, capsys) -> None:
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
    (receipt_dir / "T-20260609184046Z.json").write_text(
        json.dumps(
            {
                "schema": "repoctl.task.completion",
                "schema_version": 2,
                "task_id": "T-20260609184046Z",
                "repo_id": "api",
                "status": "banana",
            }
        )
        + "\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--repo-id", "web", "--full", "--json"]) == 0
    first = json.loads(capsys.readouterr().out)
    assert first["data"]["snapshot"]["completeness"]["capabilities"]["task_history"] == "complete"

    (receipt_dir / "T-20260609184047Z.json").write_text(
        json.dumps({"schema": "repoctl.task.completion", "schema_version": 1, "repo_id": "web", "task_id": "BAD", "status": "banana"}) + "\n",
        encoding="utf-8",
    )
    assert main(["graph", "build", "--repo-id", "web", "--full", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["snapshot"]["completeness"]["capabilities"]["task_history"] == "partial"
    assert any(warning["code"] == "invalid_completion_receipt" for warning in payload["warnings"])

    assert main(["graph", "query", "--repo-id", "web", "--file", "app.py", "--json"]) == 0
    query = json.loads(capsys.readouterr().out)
    assert query["data"]["query_status"] == "found"
    assert query["data"]["completeness"]["capabilities"]["file_inventory"] == "complete"
    assert query["data"]["completeness"]["capabilities"]["task_history"] == "partial"


def test_graph_reports_unknown_scope_invalid_receipt_without_losing_current_inventory(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True)
    receipt_path = receipt_dir / "T-20260609184046Z.json"
    receipt_path.write_text(json.dumps({"broken": True}) + "\n", encoding="utf-8")
    snapshot, problems, _meta = materialize_graph(tmp_path, target=require_repo_target(tmp_path, repo_id="main"))
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--repo-id", "main", "--file", "app.py", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["query_status"] == "found"
    assert payload["data"]["completeness"]["capabilities"]["file_inventory"] == "complete"
    assert payload["data"]["completeness"]["capabilities"]["task_history"] == "partial"
    assert payload["data"]["completeness"]["invalid_completion_receipts"] == 1
    assert payload["data"]["completeness"]["provider_failure_count"] == 0
    assert "warnings" not in payload["data"]["result"]
    assert all(warning["code"] != "graph_provider_failure" for warning in payload["warnings"])
    assert any(
        warning["code"] == "invalid_completion_receipt"
        and warning.get("path") == "docs/tasks/.repoctl-state/completions/T-20260609184046Z.json"
        for warning in payload["warnings"]
    )


def test_graph_excludes_receipt_with_fake_hash_without_losing_current_inventory(tmp_path: Path, monkeypatch, capsys) -> None:
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
    receipt = _receipt(
        "T-20260609184046Z",
        repo_id="main",
        task_path="docs/archive/tasks/T-20260609184046Z--alpha.md",
        content_sha256="sha256:" + "a" * 64,
        changed_entries=[{"change": "modified", "path": "app.py"}],
    )
    (receipt_dir / "T-20260609184046Z.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["snapshot"]["completeness"]["receipt_set_complete"] is False
    assert payload["data"]["snapshot"]["completeness"]["invalid_completion_receipts"] == 1
    assert payload["data"]["snapshot"]["completeness"]["capabilities"]["task_history"] == "partial"
    assert any(warning["code"] == "invalid_completion_receipt" for warning in payload["warnings"])
    assert any(node["id"] == file_id("main", "app.py") for node in payload["data"]["snapshot"]["nodes"])

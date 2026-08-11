from __future__ import annotations
from tests.repoctl.graph.test_graph_build import _sha256_text, _snapshot

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph import project_context_neighborhood
from tools.repoctl.graph_model import GraphContextAnchor, GraphContextAnchorKind, file_id
from tools.repoctl.graph_store import graph_materialization_freshness, materialize_graph
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
    receipt = json.loads((tmp_path / finish_payload["data"]["completion_receipt"]).read_text(encoding="utf-8"))
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
    task_result = {}
    for selector in (task_id, Path(task_path).name, task_path):
        assert main(["graph", "query", "--task", selector, "--json"]) == 0
        task_result = json.loads(capsys.readouterr().out)["data"]["result"]
        assert task_result["query"]["task_id"] == task_id
    assert any(path["edge"] == "TASK_CHANGED_FILE" for path in task_result["paths"])
    recorded_change = next(path for path in task_result["paths"] if path["edge"] == "TASK_RECORDED_CHANGE")
    assert recorded_change["evidence"]["completeness"] == "complete"
    assert any(item["selector"] == {"kind": "document", "value": task_path} for item in task_result["continuations"])
    task_continuation = next(item for item in task_result["continuations"] if item["selector"] == {"kind": "task", "value": task_id})
    assert "task.show" in task_continuation["actions"]

    assert main(["graph", "query", "--artifact", task_path, "--json"]) == 0
    artifact_result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert any(item["selector"] == {"kind": "task", "value": task_id} for item in artifact_result["continuations"])

    artifact = tmp_path / task_path
    artifact.write_text(artifact.read_text(encoding="utf-8") + "\npost-build change\n", encoding="utf-8")
    assert main(["graph", "query", "--task", task_id, "--json"]) == 0
    compact_stale = json.loads(capsys.readouterr().out)["data"]["result"]
    artifact_path = next(path for path in compact_stale["paths"] if path["edge"] == "TASK_VERIFIED_BY")
    assert artifact_path["evidence"]["freshness"] == "stale"
    assert main(["graph", "query", "--task", task_id, "--full", "--json"]) == 0
    stale = json.loads(capsys.readouterr().out)
    assert stale["data"]["freshness"]["status"] == "stale"
    assert stale["data"]["freshness"]["changed_root_paths"] == sorted(
        [finish_payload["data"]["completion_receipt"], task_path]
    )


def test_range_observed_receipt_does_not_claim_task_changed_files(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "range_sibling.py").write_text("value = 2\n", encoding="utf-8")

    task_id = "T-20260609184046Z"
    artifact_rel = f"docs/archive/tasks/{task_id}--range-observation.md"
    artifact_text = "# Range observation\n"
    artifact = tmp_path / artifact_rel
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact.write_text(artifact_text, encoding="utf-8")
    receipt = _receipt(
        task_id,
        repo_id="main",
        task_path=artifact_rel,
        content_sha256=_sha256_text(artifact_text),
        changed_entries=[
            {"change": "modified", "path": "app.py"},
            {"change": "modified", "path": "range_sibling.py"},
        ],
    )
    repo_evidence = receipt["repo_evidence"]
    assert isinstance(repo_evidence, dict)
    repo_evidence["mode"] = "committed_range"
    repo_evidence["attribution"] = "range_observed"
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True, exist_ok=True)
    (receipt_dir / f"{task_id}.json").write_text(
        json.dumps(receipt) + "\n",
        encoding="utf-8",
    )

    snapshot, problems, _meta = materialize_graph(
        tmp_path,
        target=require_repo_target(tmp_path, repo_id="main"),
    )

    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    task_node_id = f"task:{task_id}"
    recorded_changes = [
        edge
        for edge in snapshot.edges
        if edge.kind == "TASK_RECORDED_CHANGE" and edge.from_id == task_node_id
    ]
    assert recorded_changes
    assert all(edge.facts.get("attribution") == "range_observed" for edge in recorded_changes)
    assert any(
        edge.kind == "CHANGE_AFFECTED_FILE"
        and edge.to_id == file_id("main", "range_sibling.py")
        for edge in snapshot.edges
    )
    assert not any(
        edge.kind == "TASK_CHANGED_FILE" and edge.from_id == task_node_id
        for edge in snapshot.edges
    )

    projection = project_context_neighborhood(
        snapshot,
        anchors=[GraphContextAnchor(kind=GraphContextAnchorKind.FILE, path="app.py")],
        mode="auto",
        related_task_ids=[task_id],
    )

    assert projection["history_path_support"] == {}
    assert any(
        item["task_id"] == task_id and item["attribution"] == "range_observed"
        for item in projection["history"]
    )


def test_graph_legacy_receipt_preserves_paths_and_rejects_null_ownership(tmp_path: Path, monkeypatch, capsys) -> None:
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

    receipt["repo_evidence"]["ownership"] = None
    (receipt_dir / "T-20260609184046Z.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert main(["graph", "build", "--full", "--json"]) == 0
    invalid = json.loads(capsys.readouterr().out)
    assert invalid["data"]["snapshot"]["completeness"]["capabilities"]["task_history"] == "partial"
    assert not any(node["id"] == "task:T-20260609184046Z" for node in invalid["data"]["snapshot"]["nodes"])
    assert any(warning["code"] == "invalid_completion_receipt" for warning in invalid["warnings"])


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
    assert query["data"]["completeness"] == {"status": "partial"}
    assert any(warning["code"] == "graph_task_history_partial" for warning in query["warnings"])


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
    assert payload["data"]["completeness"] == {"status": "partial"}
    assert any(warning["code"] == "graph_task_history_partial" for warning in payload["warnings"])
    assert "warnings" not in payload["data"]["result"]
    assert all(warning["code"] != "graph_provider_failure" for warning in payload["warnings"])
    assert all(warning["code"] != "invalid_completion_receipt" for warning in payload["warnings"])


def test_graph_rejects_completion_receipt_symlink_outside_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True)
    outside_receipt = tmp_path.parent / f"{tmp_path.name}-outside-receipt.json"
    outside_receipt.write_text("{}\n", encoding="utf-8")
    (receipt_dir / "T-20260609184046Z.json").symlink_to(outside_receipt)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--repo-id", "main", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    receipt_warning = next(warning for warning in payload["warnings"] if warning["code"] == "invalid_completion_receipt")
    assert "escapes workspace" in receipt_warning["message"]
    assert payload["data"]["snapshot"]["completeness"]["receipt_set_complete"] is False


def test_graph_rejects_completion_receipt_directory_symlink_outside_workspace(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    outside_receipts = tmp_path.parent / f"{tmp_path.name}-outside-completions"
    outside_receipts.mkdir()
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.parent.mkdir(parents=True)
    receipt_dir.symlink_to(outside_receipts, target_is_directory=True)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--repo-id", "main", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    receipt_warning = next(warning for warning in payload["warnings"] if warning["code"] == "invalid_completion_receipt")
    assert receipt_warning["path"] == "docs/tasks/.repoctl-state/completions"
    assert "escapes workspace" in receipt_warning["message"]
    assert payload["data"]["snapshot"]["completeness"]["receipt_set_complete"] is False
    assert payload["data"]["snapshot"]["completeness"]["capabilities"]["task_history"] == "partial"


def test_graph_rejects_completion_receipt_directory_replaced_by_file(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.parent.mkdir(parents=True)
    receipt_dir.write_text("not a receipt directory\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--repo-id", "main", "--full", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    receipt_warning = next(warning for warning in payload["warnings"] if warning["code"] == "invalid_completion_receipt")
    assert receipt_warning["path"] == "docs/tasks/.repoctl-state/completions"
    assert "not a directory" in receipt_warning["message"]
    assert payload["data"]["snapshot"]["completeness"]["receipt_set_complete"] is False
    assert payload["data"]["snapshot"]["completeness"]["capabilities"]["task_history"] == "partial"


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


def test_file_query_does_not_label_task_sibling_changes_as_direct(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    (repo / "sibling.py").write_text("value = 2\n", encoding="utf-8")
    (repo / "newer_task_sibling.py").write_text("value = 3\n", encoding="utf-8")
    artifact_rel = "docs/archive/tasks/T-20260609184046Z--two-files.md"
    artifact = tmp_path / artifact_rel
    artifact.parent.mkdir(parents=True, exist_ok=True)
    artifact_text = "# completed\n"
    artifact.write_text(artifact_text, encoding="utf-8")
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True)
    receipt = _receipt(
        "T-20260609184046Z",
        repo_id="main",
        task_path=artifact_rel,
        content_sha256=_sha256_text(artifact_text),
        changed_entries=[
            {"change": "modified", "path": "app.py"},
            {"change": "modified", "path": "sibling.py"},
            {"change": "deleted", "path": "deleted.py"},
        ],
    )
    (receipt_dir / "T-20260609184046Z.json").write_text(json.dumps(receipt) + "\n", encoding="utf-8")
    newer_task_id = "T-20260610184046Z"
    newer_artifact_rel = f"docs/archive/tasks/{newer_task_id}--newer-two-files.md"
    newer_artifact_text = "# newer completed task\n"
    (tmp_path / newer_artifact_rel).write_text(newer_artifact_text, encoding="utf-8")
    newer_receipt = _receipt(
        newer_task_id,
        repo_id="main",
        task_path=newer_artifact_rel,
        content_sha256=_sha256_text(newer_artifact_text),
        changed_entries=[
            {"change": "modified", "path": "app.py"},
            {"change": "modified", "path": "newer_task_sibling.py"},
        ],
    )
    newer_receipt["completed_at"] = "2026-06-10T18:40:46Z"
    (receipt_dir / f"{newer_task_id}.json").write_text(
        json.dumps(newer_receipt) + "\n",
        encoding="utf-8",
    )
    snapshot, problems, _meta = materialize_graph(tmp_path, target=require_repo_target(tmp_path, repo_id="main"))
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--file", "app.py", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    task_paths = [path for path in result["paths"] if path["edge"] == "TASK_CHANGED_FILE"]
    assert task_paths
    assert all(path["to"]["path"] == "app.py" for path in task_paths)
    assert all(
        path.get("to", {}).get("path") not in {"sibling.py", "newer_task_sibling.py"}
        for path in result["paths"]
    )

    projection = project_context_neighborhood(
        snapshot,
        anchors=[GraphContextAnchor(kind=GraphContextAnchorKind.FILE, path="app.py")],
        mode="auto",
        related_task_ids=["T-20260609184046Z", newer_task_id],
    )
    assert projection["history_path_support"] == {
        "app.py": ["T-20260609184046Z"],
        "sibling.py": ["T-20260609184046Z"],
    }
    assert any(item["task_id"] == newer_task_id for item in projection["history"])
    assert "sibling.py" not in projection["related_paths"]
    assert "newer_task_sibling.py" not in projection["related_paths"]
    assert all(
        "sibling.py" not in {relation.get("from_path"), relation.get("to_path")}
        for relation in projection["relations"]
    )


def test_graph_rebuilds_when_receipt_artifact_identity_becomes_ambiguous(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    commit_all(repo)
    task_id = "T-20260609184046Z"
    live_rel = f"docs/tasks/{task_id}--alpha.md"
    archive_rel = f"docs/archive/tasks/{task_id}--alpha.md"
    archive_path = tmp_path / archive_rel
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    history_token = "receipt_identity_history_sentinel"
    artifact_text = task_text(task_id, status="done") + f"\n{history_token}\n"
    archive_path.write_text(artifact_text, encoding="utf-8")
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True)
    receipt = _receipt(
        task_id,
        repo_id="main",
        task_path=live_rel,
        content_sha256=_sha256_text(artifact_text),
        changed_entries=[{"change": "modified", "path": "app.py"}],
    )
    (receipt_dir / f"{task_id}.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    target = require_repo_target(tmp_path, repo_id="main")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    first, first_problems, first_meta = materialize_graph(tmp_path, target=target)
    assert first is not None
    assert not [problem for problem in first_problems if problem.severity == "error"]
    assert first_meta["materialization"]["status"] == "rebuilt"
    assert any(node.id == f"task:{task_id}" for node in first.nodes)
    assert main(["context", "query", history_token, "--repo-id", "main", "--full", "--json"]) == 0
    initial_context = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert any(item["source_ref"]["path"] == archive_rel for item in initial_context["groups"]["related_history"])

    live_path = tmp_path / live_rel
    live_path.write_bytes(archive_path.read_bytes())
    stale, stale_problems = graph_materialization_freshness(tmp_path, target=target)
    assert not [problem for problem in stale_problems if problem.severity == "error"]
    assert stale["status"] == "stale"
    assert stale["root_evidence_changed"] is True
    assert stale["completion_receipt_input_changed"] is True
    assert live_rel in stale["changed_root_paths"]
    assert archive_rel in stale["changed_root_paths"]
    assert main(["context", "query", history_token, "--repo-id", "main", "--full", "--json"]) == 0
    stale_context = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert all(item["source_ref"]["path"] != archive_rel for item in stale_context["groups"]["related_history"])

    ambiguous, ambiguous_problems, ambiguous_meta = materialize_graph(tmp_path, target=target)
    assert ambiguous is not None
    assert ambiguous_meta["materialization"]["status"] == "updated"
    assert ambiguous.completeness["capabilities"]["task_history"] == "partial"
    assert ambiguous.completeness["receipt_set_complete"] is False
    assert not any(node.id == f"task:{task_id}" for node in ambiguous.nodes)
    assert any(problem.code == "invalid_completion_receipt" for problem in ambiguous_problems)
    current_ambiguous, current_ambiguous_problems = graph_materialization_freshness(tmp_path, target=target)
    assert current_ambiguous_problems == []
    assert current_ambiguous["status"] == "current"

    live_path.unlink()
    restored, restored_problems, restored_meta = materialize_graph(tmp_path, target=target)
    assert restored is not None
    assert not [problem for problem in restored_problems if problem.severity == "error"]
    assert restored_meta["materialization"]["status"] == "updated"
    assert restored.completeness["capabilities"]["task_history"] == "complete"
    assert any(node.id == f"task:{task_id}" for node in restored.nodes)
    current_restored, current_restored_problems = graph_materialization_freshness(tmp_path, target=target)
    assert current_restored_problems == []
    assert current_restored["status"] == "current"

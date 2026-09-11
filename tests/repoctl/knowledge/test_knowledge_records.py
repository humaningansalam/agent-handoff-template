from __future__ import annotations

import json
from pathlib import Path

import tools.repoctl.cli as cli
from tools.repoctl.cli import main
from tools.repoctl.tasks import Problem
from tests.repoctl.knowledge_test_helpers import (
    _add_knowledge_source,
    _setup_knowledge_workspace,
    add_task,
    init_repo,
    task_text,
    write_repometa,
    write_workspace,
)
from tests.repoctl.repository.test_repositories import commit_all


def _payload(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def _finishable_task(root: Path, monkeypatch, capsys, *, task_id: str) -> None:
    write_workspace(root)
    repo = root / "repos"
    init_repo(repo)
    write_repometa(repo)
    source = repo / "service.py"
    source.write_text("VALUE = 'before'\n", encoding="utf-8")
    commit_all(repo)
    body = task_text(task_id, status="todo").replace('repo_id: ""', 'repo_id: "main"')
    add_task(root, f"{task_id}--knowledge-closeout.md", body)
    (root / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{task_id}--knowledge-closeout.md\n\n## Backlog\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)
    assert main(["task", "start", task_id, "--json"]) == 0
    _payload(capsys)
    assert main(["task", "discovery", "add", task_id, "--chosen", "repos/service.py", "--json"]) == 0
    _payload(capsys)
    source.write_text("VALUE = 'after'\n", encoding="utf-8")


def _finish_with_knowledge(task_id: str) -> list[str]:
    return [
        "task",
        "finish",
        task_id,
        "--knowledge-kind",
        "invariant",
        "--knowledge-claim",
        "Service updates keep one owner.",
        "--knowledge-reason",
        "A second owner previously caused divergent behavior.",
        "--knowledge-applies-to",
        "service.py",
        "--json",
    ]


def test_knowledge_add_writes_one_record_and_source_change_is_advisory(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)

    payload = _add_knowledge_source(capsys)
    record = payload["data"]["record"]
    assert record["schema_version"] == 2
    assert record["reason"] == "This reusable rule explains later work."
    assert record["source_refs"][0]["content_sha256"].startswith("sha256:")
    assert "author" not in record
    assert not (tmp_path / ".repoctl-state/knowledge/candidates").exists()
    assert not list((tmp_path / "docs/knowledge/events").glob("E-*.json"))

    source = tmp_path / "docs/contracts/repoctl-context-contract.md"
    source.write_text(source.read_text(encoding="utf-8") + "\nChanged later.\n", encoding="utf-8")

    assert main(["knowledge", "query", "source bundles", "--repo-id", "main", "--json"]) == 0
    query = _payload(capsys)
    assert query["data"]["results"][0]["record"]["id"] == record["id"]
    assert [item["code"] for item in query["warnings"]] == ["knowledge_source_changed"]

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 0
    check = _payload(capsys)
    assert check["ok"] is True
    assert [(item["severity"], item["code"]) for item in check["problems"]] == [
        ("warning", "knowledge_source_changed")
    ]


def test_knowledge_replacement_and_optional_author_use_records_only(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    original = _add_knowledge_source(capsys, claim="Keep the original routing rule.")["data"]["record"]

    assert main(
        [
            "knowledge",
            "add",
            "--repo-id",
            "main",
            "--kind",
            "decision",
            "--claim",
            "Use the corrected routing rule.",
            "--reason",
            "The original rule misses retry traffic.",
            "--source",
            "docs/contracts/repoctl-context-contract.md",
            "--replaces",
            original["id"],
            "--author",
            "codex",
            "--json",
        ]
    ) == 0
    replacement = _payload(capsys)["data"]["record"]
    assert replacement["replaces"] == [original["id"]]
    assert replacement["author"] == "codex"

    assert main(["knowledge", "query", "routing rule", "--repo-id", "main", "--json"]) == 0
    current = _payload(capsys)
    assert [item["record"]["id"] for item in current["data"]["results"]] == [replacement["id"]]

    assert main(["knowledge", "query", "routing rule", "--repo-id", "main", "--include-history", "--json"]) == 0
    history = _payload(capsys)
    statuses = {item["record"]["id"]: item["record"]["status"] for item in history["data"]["results"]}
    assert statuses == {original["id"]: "superseded", replacement["id"]: "reviewed"}
    assert not list((tmp_path / "docs/knowledge/events").glob("E-*.json"))


def test_legacy_knowledge_is_preserved_and_reported_for_manual_migration(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    record_id = "K-20260609184045Z--legacy-routing"
    path = tmp_path / f"docs/knowledge/records/{record_id}.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    original = json.dumps(
        {
            "schema": "repoctl.knowledge.record",
            "schema_version": 1,
            "id": record_id,
            "repo_id": "main",
            "kind": "decision",
            "claim": "Keep the accumulated routing decision.",
        },
        indent=2,
        sort_keys=True,
    ) + "\n"
    path.write_text(original, encoding="utf-8")

    assert main(["knowledge", "check", "--repo-id", "main", "--json"]) == 0
    payload = _payload(capsys)
    assert payload["data"]["record_count"] == 0
    assert payload["data"]["legacy_record_count"] == 1
    assert [problem["code"] for problem in payload["problems"]] == [
        "knowledge_record_migration_required"
    ]
    assert path.read_text(encoding="utf-8") == original


def test_task_finish_saves_knowledge_without_candidate_or_event(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id = "T-20260609184046Z"
    _finishable_task(tmp_path, monkeypatch, capsys, task_id=task_id)

    assert main(_finish_with_knowledge(task_id)) == 0
    payload = _payload(capsys)
    closeout = payload["data"]["knowledge_closeout"]
    assert closeout["status"] == "saved"
    record = json.loads((tmp_path / closeout["record_path"]).read_text(encoding="utf-8"))
    assert record["claim"] == "Service updates keep one owner."
    assert record["reason"] == "A second owner previously caused divergent behavior."
    assert record["applies_to"] == {"paths": ["service.py"]}
    assert "author" not in record
    assert not (tmp_path / ".repoctl-state/knowledge/candidates").exists()
    assert not list((tmp_path / "docs/knowledge/events").glob("E-*.json"))


def test_task_finish_stays_successful_when_derived_knowledge_sync_fails(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    task_id = "T-20260609184047Z"
    _finishable_task(tmp_path, monkeypatch, capsys, task_id=task_id)
    projection_problem = Problem("error", "synthetic_projection_failure", "projection failed")
    graph_problem = Problem("error", "synthetic_graph_failure", "graph failed")
    monkeypatch.setattr(cli, "rebuild_knowledge_projection", lambda *_args, **_kwargs: ({}, [projection_problem]))
    monkeypatch.setattr(cli, "_sync_graph_after_knowledge_change", lambda *_args, **_kwargs: ({"status": "stale"}, [graph_problem]))

    assert main(_finish_with_knowledge(task_id)) == 0
    payload = _payload(capsys)
    assert payload["ok"] is True
    assert payload["data"]["status"] == "done"
    assert (tmp_path / payload["data"]["knowledge_closeout"]["record_path"]).is_file()
    assert {item["code"] for item in payload["warnings"]} == {
        "knowledge_projection_sync_failed",
        "knowledge_graph_sync_failed",
    }

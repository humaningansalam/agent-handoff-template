from __future__ import annotations
from tests.repoctl.repository.test_repositories import init_repo

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.context_test_helpers import _write_completion_receipt
from tests.repoctl.task_lifecycle_helpers import add_board_task, commit_all, task_text, write_verification
from tests.repoctl.workspace.test_check import write_workspace



def test_unconfigured_collection_lists_unbound_candidates(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["placement"] == "collection"
    assert payload["data"]["registry_ready"] is False
    assert payload["data"]["targets"] == []
    assert payload["data"]["candidates"] == [
        {"path": "repos/api", "suggested_id": "api", "git_toplevel": (tmp_path / "repos/api").resolve().as_posix(), "validation_status": "valid", "identity_status": "unbound"},
        {"path": "repos/web", "suggested_id": "web", "git_toplevel": (tmp_path / "repos/web").resolve().as_posix(), "validation_status": "valid", "identity_status": "unbound"},
    ]
    assert [action["command"] for action in payload["next_actions"]] == [
        "./scripts/repoctl repo adopt repos/api --id api --json",
        "./scripts/repoctl repo adopt repos/web --id web --json",
    ]


def test_unconfigured_collection_repo_check_reports_unbound_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/Web App")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"
    assert payload["data"]["candidates"][0]["suggested_id"] == "Web App"




def test_unconfigured_collection_blocks_meta_init_index_and_product_task_without_selector(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "init", "--json"]) == 2
    meta_payload = json.loads(capsys.readouterr().out)
    assert meta_payload["problems"][0]["code"] == "repository_identity_unbound"
    assert [action["command"] for action in meta_payload["next_actions"]] == [
        "./scripts/repoctl repo adopt repos/api --id api --json",
        "./scripts/repoctl repo adopt repos/web --id web --json",
    ]
    assert not (tmp_path / "repos/.repometa").exists()

    assert main(["index", "code", "--json"]) == 2
    index_payload = json.loads(capsys.readouterr().out)
    assert index_payload["problems"][0]["code"] == "repository_identity_unbound"

    assert main(["task", "create", "--area", "repo", "--slug", "oops", "Oops", "--json"]) == 2
    task_payload = json.loads(capsys.readouterr().out)
    assert task_payload["problems"][0]["code"] == "repository_identity_unbound"
    assert not list((tmp_path / "docs/tasks").glob("*--oops.md"))


def test_repo_adopt_all_pins_collection_targets(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "adopt", "--all", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["registry_ready"] is True
    assert payload["data"]["targets"] == [
        {"id": "api", "path": "repos/api", "identity_source": "pinned"},
        {"id": "web", "path": "repos/web", "identity_source": "pinned"},
    ]


def test_historical_completion_receipts_do_not_bind_current_repository_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    _write_completion_receipt(tmp_path, repo_id="main")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "adopt", "--all", "--json"]) == 0
    capsys.readouterr()

    assert main(["upgrade", "postflight", "--workspace-root", str(tmp_path), "--json"]) == 0
    historical_payload = json.loads(capsys.readouterr().out)
    main_namespace = next(item for item in historical_payload["data"]["repository_state"]["namespaces"] if item["repo_id"] == "main")
    assert main_namespace["sources"] == ["completion_receipt"]
    assert main_namespace["identity_binding"] == "historical"
    assert historical_payload["data"]["repository_state"]["unbound_repo_ids"] == []
    assert historical_payload["data"]["repository_state"]["historical_unbound_repo_ids"] == ["main"]
    assert not any(problem["code"] == "upgrade_repository_state_identity_unbound" for problem in historical_payload["problems"])

    legacy_graph = tmp_path / ".repoctl-state/graph/main/manifest.json"
    legacy_graph.parent.mkdir(parents=True)
    legacy_graph.write_text("{}\n", encoding="utf-8")

    assert main(["upgrade", "postflight", "--workspace-root", str(tmp_path), "--json"]) == 1
    active_payload = json.loads(capsys.readouterr().out)
    main_namespace = next(item for item in active_payload["data"]["repository_state"]["namespaces"] if item["repo_id"] == "main")
    assert main_namespace["sources"] == ["completion_receipt", "graph"]
    assert main_namespace["identity_binding"] == "required"
    assert active_payload["data"]["repository_state"]["unbound_repo_ids"] == ["main"]
    assert active_payload["data"]["repository_state"]["historical_unbound_repo_ids"] == []
    assert any(problem["code"] == "upgrade_repository_state_identity_unbound" for problem in active_payload["problems"])


def test_root_completion_receipt_does_not_create_repository_namespace(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    task_id = "T-20260609184046Z"
    text = task_text(task_id, status="doing").replace('area: ""', 'area: "ops"')
    add_board_task(tmp_path, f"{task_id}--workspace-update.md", text)
    verification = write_verification(tmp_path, "workspace update verified\n")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0
    finish_payload = json.loads(capsys.readouterr().out)
    receipt = json.loads((tmp_path / finish_payload["data"]["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["schema_version"] == 2
    assert receipt["repo_id"] == ""

    assert main(["upgrade", "postflight", "--workspace-root", str(tmp_path), "--json"]) == 0
    postflight_payload = json.loads(capsys.readouterr().out)
    assert postflight_payload["problems"] == []
    assert postflight_payload["data"]["repository_state"]["namespaces"] == []

    receipt_path = tmp_path / finish_payload["data"]["completion_receipt"]
    tampered = dict(receipt)
    tampered["task_id"] = "T-20260609184047Z"
    receipt_path.write_text(json.dumps(tampered, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    assert main(["upgrade", "postflight", "--workspace-root", str(tmp_path), "--json"]) == 1
    tampered_payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "repository_state_identity_missing" for problem in tampered_payload["problems"])
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")

    assert main(["repo", "adopt", "--all", "--json"]) == 0
    adoption_payload = json.loads(capsys.readouterr().out)
    assert adoption_payload["data"]["targets"] == [
        {"id": "api", "path": "repos/api", "identity_source": "pinned"},
        {"id": "web", "path": "repos/web", "identity_source": "pinned"},
    ]


def test_repo_scoped_completion_receipt_with_empty_identity_fails_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    (repo / "app.py").write_text("value = 1\n", encoding="utf-8")
    commit_all(repo)
    task_id = "T-20260609184046Z"
    text = (
        task_text(task_id, status="todo")
        .replace('area: ""', 'area: "repo"')
        .replace('repo_id: ""', 'repo_id: "main"')
    )
    add_board_task(tmp_path, f"{task_id}--repo-update.md", text)
    verification = write_verification(tmp_path, "repository update verified\n")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0
    finish_payload = json.loads(capsys.readouterr().out)
    receipt_path = tmp_path / finish_payload["data"]["completion_receipt"]
    receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
    assert receipt["repo_id"] == "main"
    receipt["repo_id"] = ""
    receipt_path.write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["upgrade", "postflight", "--workspace-root", str(tmp_path), "--json"]) == 1
    postflight_payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "repository_state_identity_missing" for problem in postflight_payload["problems"])


def test_repo_adopt_single_candidate_then_merge_next_candidate(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "adopt", "repos/web", "--id", "web", "--json"]) == 0

    first = json.loads(capsys.readouterr().out)
    assert first["data"]["registry_ready"] is False
    assert first["data"]["targets"] == [{"id": "web", "path": "repos/web", "identity_source": "pinned"}]
    assert first["data"]["candidates"][0]["path"] == "repos/api"
    config = json.loads((tmp_path / "docs/repoctl.json").read_text(encoding="utf-8"))
    assert config["repositories"] == [{"id": "web", "path": "repos/web"}]

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--slug", "blocked-unbound-api", "Blocked unbound api", "--json"]) == 2
    blocked = json.loads(capsys.readouterr().out)
    assert blocked["problems"][0]["code"] == "repository_identity_unbound"

    assert main(["repo", "adopt", "repos/api", "--id", "api", "--json"]) == 0

    second = json.loads(capsys.readouterr().out)
    assert second["data"]["registry_ready"] is True
    assert second["data"]["targets"] == [
        {"id": "web", "path": "repos/web", "identity_source": "pinned"},
        {"id": "api", "path": "repos/api", "identity_source": "pinned"},
    ]


def test_repo_adopt_all_invalid_candidate_does_not_write_config(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/Bad Name")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "adopt", "--all", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_topology_invalid"
    assert not (tmp_path / "docs/repoctl.json").exists()

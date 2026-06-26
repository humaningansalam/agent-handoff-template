from __future__ import annotations
from tests.repoctl.repository.test_repositories import init_repo

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa



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


def test_unconfigured_collection_repo_check_reports_unbound_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/Web App")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"
    assert payload["data"]["candidates"][0]["suggested_id"] == "Web App"


def test_unconfigured_collection_blocks_meta_until_adopted(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "inventory", "--repo-id", "api", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"


def test_unconfigured_collection_blocks_meta_init_index_and_product_task_without_selector(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "init", "--json"]) == 2
    meta_payload = json.loads(capsys.readouterr().out)
    assert meta_payload["problems"][0]["code"] == "repository_identity_unbound"
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


def test_unconfigured_collection_task_create_requires_adoption(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--slug", "unadopted-web", "Unadopted web", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"


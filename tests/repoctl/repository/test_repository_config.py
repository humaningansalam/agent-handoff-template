from __future__ import annotations
from tests.repoctl.repository.test_repositories import init_repo, write_settings

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.io import RepoctlError
from tools.repoctl.repositories import repo_layout
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa



def test_config_rejects_absolute_repository_path(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    write_settings(tmp_path, {"repositories": [{"id": "main", "path": "/tmp/product"}]})

    try:
        repo_layout(tmp_path)
    except RepoctlError as exc:
        assert "workspace-relative" in str(exc)
    else:
        raise AssertionError("absolute repository paths should be rejected")


def test_config_rejects_product_repo_outside_repos_boundary(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "docs/product")
    write_settings(tmp_path, {"repositories": [{"id": "x", "path": "docs/product"}]})

    try:
        repo_layout(tmp_path)
    except RepoctlError as exc:
        assert exc.code == "repository_topology_invalid"
        assert "repos or repos/<id>" in str(exc)
    else:
        raise AssertionError("configured product repositories must stay under repos/")


def test_config_rejects_direct_repos_with_non_main_id(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos"}]})

    try:
        repo_layout(tmp_path)
    except RepoctlError as exc:
        assert exc.code == "repository_topology_invalid"
        assert "main" in str(exc)
    else:
        raise AssertionError("direct repos/ must use reserved id main")


def test_config_rejects_duplicate_git_toplevel_aliases(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    real = tmp_path / "repos/real"
    init_repo(real)
    alias = tmp_path / "repos/alias"
    alias.symlink_to(real, target_is_directory=True)
    write_settings(tmp_path, {"repositories": [{"id": "real", "path": "repos/real"}, {"id": "alias", "path": "repos/alias"}]})

    try:
        repo_layout(tmp_path)
    except RepoctlError as exc:
        assert exc.code == "repository_topology_invalid"
        assert "duplicate repository" in str(exc)
    else:
        raise AssertionError("two repo_id values must not point at the same real git repository")


def test_collection_rejects_unowned_direct_entries(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    (tmp_path / "repos/shared.txt").write_text("loose\n", encoding="utf-8")
    (tmp_path / "repos/shared").mkdir()
    (tmp_path / "repos/shared/config.json").write_text("{}\n", encoding="utf-8")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["registry_ready"] is False
    assert [problem["code"] for problem in payload["problems"]] == ["repository_unowned_product_path", "repository_unowned_product_path"]
    assert {problem["path"] for problem in payload["problems"]} == {"repos/shared", "repos/shared.txt"}


def test_config_rejects_nested_git_root_under_product_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    nested = web / "vendor/nested"
    init_repo(web)
    init_repo(nested)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["registry_ready"] is False
    assert payload["problems"][0]["code"] == "repository_topology_invalid"
    assert payload["problems"][0]["path"] == "repos/web/vendor/nested"


def test_repo_relative_path_starting_with_repos_is_not_stripped(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    nested = repo / "repos"
    nested.mkdir()
    (nested / "client.py").write_text("print('nested')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "inventory", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    files = payload["data"]["files"]
    assert files[0]["path"] == "repos/client.py"
    assert files[0]["workspace_path"] == "repos/repos/client.py"


def test_direct_repos_symlink_escape_is_not_ready_and_cannot_mutate(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    outside = tmp_path.parent / f"{tmp_path.name}-outside"
    outside.mkdir()
    (tmp_path / "repos").symlink_to(outside, target_is_directory=True)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["registry_ready"] is False
    assert payload["problems"][0]["code"] == "repository_git_unavailable"

    assert main(["meta", "init", "--json"]) == 2

    capsys.readouterr()
    assert not (outside / ".repometa").exists()


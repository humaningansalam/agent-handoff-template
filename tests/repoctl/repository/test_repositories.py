from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.io import RepoctlError
from tools.repoctl.repositories import repo_layout
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa



def init_repo(repo: Path) -> None:
    repo.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)

def commit_all(repo: Path) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

def write_settings(root: Path, data: dict) -> None:
    path = root / "docs/repoctl.json"
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def test_single_repos_discovers_main_id(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "list", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["placement"] == "direct"
    assert payload["data"]["registry_ready"] is True
    assert payload["data"]["targets"] == [{"id": "main", "path": "repos", "identity_source": "reserved"}]
    assert payload["data"]["candidates"] == []

def test_repos_root_and_child_git_are_ambiguous(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos")
    init_repo(tmp_path / "repos/api")

    try:
        repo_layout(tmp_path)
    except RepoctlError as exc:
        assert "ambiguous product repositories detected" in str(exc)
    else:
        raise AssertionError("repos/.git and repos/*/.git together should be ambiguous")

def test_gitfile_worktree_is_detected_as_repo_root(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    source = tmp_path / "source-repo"
    init_repo(source)
    (source / "app.py").write_text("print('source')\n", encoding="utf-8")
    commit_all(source)
    subprocess.run(["git", "worktree", "add", str(tmp_path / "repos"), "HEAD"], cwd=source, check=True, stdout=subprocess.DEVNULL)

    layout = repo_layout(tmp_path)

    assert layout.placement == "direct"
    assert layout.targets[0].display_path == "repos"
    assert (tmp_path / "repos/.git").is_file()

def test_configured_multi_requires_repo_id_for_meta(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "inventory", "--json"]) == 2
    missing_selector = json.loads(capsys.readouterr().out)
    assert missing_selector["problems"][0]["code"] == "repository_selector_required"

    assert main(["meta", "inventory", "--repo-id", "web", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["repository"] == {"id": "web", "path": "repos/web", "identity_source": "pinned"}
    assert payload["data"]["files"][0]["path"] == "app.py"
    assert payload["data"]["files"][0]["workspace_path"] == "repos/web/app.py"

def test_repo_check_fails_when_repos_directory_is_not_git(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    (tmp_path / "repos").mkdir()
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["repo", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_git_unavailable"
    assert payload["data"]["registry_ready"] is False
    assert payload["data"]["targets"] == []

    assert main(["meta", "init", "--json"]) == 2

    meta_payload = json.loads(capsys.readouterr().out)
    assert meta_payload["problems"][0]["code"] == "repository_identity_unbound"
    assert not (tmp_path / "repos/.repometa").exists()

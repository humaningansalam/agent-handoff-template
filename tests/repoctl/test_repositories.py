from __future__ import annotations

import json
import shutil
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.io import RepoctlError
from tools.repoctl.meta import shard_for_path
from tools.repoctl.repositories import repo_layout
from tools.repoctl.tasks import validate_verification_file
from tests.repoctl.test_check import write_workspace
from tests.repoctl.test_meta_check import BASE_POLICY, write_repometa


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


def test_config_rejects_absolute_repository_path(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    write_settings(tmp_path, {"repositories": [{"id": "main", "path": "/tmp/product"}]})

    try:
        repo_layout(tmp_path)
    except RepoctlError as exc:
        assert "workspace-relative" in str(exc)
    else:
        raise AssertionError("absolute repository paths should be rejected")


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


def test_meta_check_changed_blocks_repos_without_git(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repos = tmp_path / "repos"
    repos.mkdir()
    write_repometa(repos)
    (repos / "app.py").write_text("print('not git')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--changed", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"


def test_task_start_freezes_configured_repo_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    commit_all(web)
    commit_all(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--start", "--slug", "repo-id-freeze", "Repo id freeze", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    state_path = tmp_path / "docs/tasks/.repoctl-state" / f"{payload['data']['task_id']}.json"
    state = json.loads(state_path.read_text(encoding="utf-8"))
    assert state["schema_version"] == 2
    assert state["repo_id"] == "web"
    assert state["repo_path"] == "repos/web"
    assert state["git_toplevel"] == web.resolve().as_posix()


def test_task_start_blocks_when_registry_becomes_unready(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    init_repo(web)
    write_repometa(web)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    commit_all(web)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--slug", "web-before-api", "Web before api", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    init_repo(tmp_path / "repos/api")

    assert main(["task", "start", task_id, "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"


def test_task_finish_uses_configured_repo_id_in_multi_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    commit_all(web)
    commit_all(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--start", "--slug", "finish-web", "Finish web", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    (web / "app.py").write_text("print('web changed')\n", encoding="utf-8")
    assert main(["task", "discovery", "add", task_id, "--query", "web app", "--reviewed", "repos/web/app.py", "--chosen", "repos/web/app.py", "--json"]) == 0
    capsys.readouterr()
    verification = tmp_path / "verification.md"
    verification.write_text("- Ran focused check\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["meta_gate"]["status"] == "passed"
    assert payload["data"]["meta_gate"]["changed_files"] == 1
    archive_path = tmp_path / payload["data"]["new_path"]
    assert "- repository: web repos/web" in archive_path.read_text(encoding="utf-8")


def test_task_doctor_uses_task_repo_id_in_configured_multi_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    commit_all(web)
    commit_all(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--start", "--slug", "doctor-web", "Doctor web", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    (web / "app.py").write_text("print('web changed')\n", encoding="utf-8")

    assert main(["task", "doctor", task_id, "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["repository"] == {"id": "web", "path": "repos/web", "identity_source": "pinned"}
    assert {warning["code"] for warning in payload["warnings"]} == {"missing_discovery_evidence", "missing_verification_file"}
    assert payload["problems"] == []


def test_meta_check_multi_repo_problem_paths_and_next_actions_use_selected_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    policy = dict(BASE_POLICY)
    policy["coverage"] = {"require_annotations": ["*.py"]}
    write_repometa(web, policy=policy)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--repo-id", "web", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["repository"] == {"id": "web", "path": "repos/web", "identity_source": "pinned"}
    assert payload["problems"][0]["path"] == "repos/web/app.py"
    commands = [action.get("command", "") for action in payload["next_actions"]]
    assert any("--repo-id web" in command and " app.py " in command for command in commands)


def test_meta_check_multi_repo_store_problem_paths_use_selected_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "inline.py").write_text("# @meta\nprint('web')\n", encoding="utf-8")
    shard = shard_for_path("missing.py")
    shard_path = web / ".repometa/annotations" / f"{shard}.json"
    data = json.loads(shard_path.read_text(encoding="utf-8"))
    data["annotations"]["missing.py"] = {"role": "service", "purpose": "gone", "topics": []}
    shard_path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    (web / ".repometa/annotations/not-a-shard.json").write_text("{}\n", encoding="utf-8")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--repo-id", "web", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    paths_by_code: dict[str, list[str]] = {}
    for problem in payload["problems"]:
        paths_by_code.setdefault(problem["code"], []).append(problem.get("path", ""))
    assert paths_by_code["invalid_shard_name"] == ["repos/web/.repometa/annotations/not-a-shard.json"]
    assert paths_by_code["orphan_annotation"] == ["repos/web/missing.py"]
    assert paths_by_code["inline_meta_residue"] == ["repos/web/inline.py"]
    assert all(not path.startswith("repos/") or path.startswith("repos/web/") for paths in paths_by_code.values() for path in paths)


def test_task_create_repo_scoped_requires_repo_id_in_multi_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--slug", "missing-repo-id", "Missing repo id", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"


def test_unconfigured_collection_task_create_requires_adoption(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--slug", "unadopted-web", "Unadopted web", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"


def test_adopted_multi_repo_task_can_finish_selected_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    commit_all(web)
    commit_all(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--start", "--slug", "adopted-web", "Adopted web", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    (web / "app.py").write_text("print('web changed')\n", encoding="utf-8")
    assert main(["task", "discovery", "add", task_id, "--query", "web app", "--reviewed", "repos/web/app.py", "--chosen", "repos/web/app.py", "--json"]) == 0
    capsys.readouterr()
    verification = tmp_path / "verification.md"
    verification.write_text("- Ran selected web validation\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["meta_gate"]["status"] == "passed"
    assert "- repository: web repos/web" in (tmp_path / payload["data"]["new_path"]).read_text(encoding="utf-8")


def test_repo_task_discovery_must_match_selected_repository(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    commit_all(web)
    commit_all(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--start", "--slug", "discovery-web", "Discovery web", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]

    assert main(["task", "discovery", "add", task_id, "--query", "api app", "--reviewed", "repos/api/app.py", "--chosen", "repos/api/app.py", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "discovery_outside_selected_repository"


def test_repo_task_discovery_rejects_dotdot_escape_from_selected_repository(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    commit_all(web)
    commit_all(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--start", "--slug", "discovery-dotdot", "Discovery dotdot", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]

    assert main(["task", "discovery", "add", task_id, "--query", "api app", "--reviewed", "repos/web/app.py", "--chosen", "repos/web/../api/app.py", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "discovery_outside_selected_repository"


def test_repo_task_allows_other_product_repo_preexisting_dirty_at_start(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    commit_all(web)
    commit_all(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--slug", "dirty-other", "Dirty other", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    (api / "app.py").write_text("print('api dirty')\n", encoding="utf-8")

    assert main(["task", "start", task_id, "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "doing"


def test_root_task_blocks_task_new_uncommitted_product_change_in_configured_multi(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    commit_all(web)
    commit_all(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "docs", "--start", "--slug", "root-docs", "Root docs", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    (api / "app.py").write_text("print('api changed')\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("- Checked root docs\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"
    assert payload["problems"][0]["path"] == "repos/api/app.py"


def test_root_task_blocks_task_new_uncommitted_unadopted_candidate_change(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "docs", "--start", "--slug", "root-docs-candidate", "Root docs candidate", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    web = tmp_path / "repos/web"
    init_repo(web)
    (web / "app.py").write_text("print('candidate dirty')\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("- Checked root docs\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"
    assert payload["problems"][0]["path"] == "repos/web/app.py"


def test_root_task_allows_preexisting_unadopted_candidate_dirty_when_unchanged(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    init_repo(web)
    (web / "app.py").write_text("print('preexisting')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "docs", "--start", "--slug", "root-docs-preexisting-candidate", "Root docs preexisting candidate", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    verification = tmp_path / "verification.md"
    verification.write_text("- Checked root docs\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0


def test_root_task_blocks_unadopted_candidate_removed_after_start(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    init_repo(web)
    (web / "app.py").write_text("print('base')\n", encoding="utf-8")
    commit_all(web)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "docs", "--start", "--slug", "root-docs-candidate-removed", "Root docs candidate removed", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    shutil.rmtree(web)
    verification = tmp_path / "verification.md"
    verification.write_text("- Checked root docs\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"
    assert payload["problems"][0]["path"] == "repos/web"


def test_root_task_without_baseline_treats_current_multi_repo_dirty_as_task_new(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    commit_all(web)
    commit_all(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["task", "create", "--area", "docs", "--slug", "root-no-baseline", "Root no baseline", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    task_path = tmp_path / f"docs/tasks/{task_id}--root-no-baseline.md"
    task_path.write_text(task_path.read_text(encoding="utf-8").replace("status: todo", "status: doing"), encoding="utf-8")
    (api / "app.py").write_text("print('api dirty')\n", encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("- Checked root docs\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"
    assert payload["problems"][0]["path"] == "repos/api/app.py"


def test_root_task_allows_product_head_change_when_worktree_clean_in_configured_multi(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "app.py").write_text("print('api')\n", encoding="utf-8")
    commit_all(web)
    commit_all(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "docs", "--start", "--slug", "root-docs-clean-head", "Root docs clean head", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    (api / "app.py").write_text("print('api committed')\n", encoding="utf-8")
    commit_all(api)
    verification = tmp_path / "verification.md"
    verification.write_text("- Checked root docs after independent product commit\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0


def test_single_repos_task_lifecycle_finish_passes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("print('base')\n", encoding="utf-8")
    commit_all(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--start", "--slug", "single-repos", "Single repos", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    (repo / "app.py").write_text("print('changed')\n", encoding="utf-8")
    assert main(["task", "discovery", "add", task_id, "--query", "single app", "--reviewed", "repos/app.py", "--chosen", "repos/app.py", "--json"]) == 0
    capsys.readouterr()
    verification = tmp_path / "verification.md"
    verification.write_text("- Ran single repos lifecycle\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["meta_gate"]["status"] == "passed"
    assert "- repository: main repos" in (tmp_path / payload["data"]["new_path"]).read_text(encoding="utf-8")


def test_task_finish_blocks_when_repo_registry_target_drifted(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    renamed = tmp_path / "repos/renamed"
    init_repo(web)
    write_repometa(web)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    commit_all(web)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--start", "--slug", "target-drift", "Target drift", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    web.rename(renamed)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/renamed"}]})
    verification = tmp_path / "verification.md"
    verification.write_text("- Attempt finish after registry drift\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repo_target_changed_since_start"


def test_verification_file_must_be_outside_all_product_repos(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    verification = api / "verification.md"
    verification.write_text("evidence\n", encoding="utf-8")

    try:
        validate_verification_file(tmp_path, verification)
    except RepoctlError as exc:
        assert exc.code == "verification_file_inside_repo"
        assert "repos/api/" in str(exc)
    else:
        raise AssertionError("verification file inside a non-selected product repo should be rejected")


def test_verification_file_must_be_outside_unbound_candidate_when_target_configured(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    (web / "app.py").write_text("print('web')\n", encoding="utf-8")
    (api / "proof.md").write_text("candidate proof\n", encoding="utf-8")
    commit_all(web)
    commit_all(api)
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "docs", "--start", "--slug", "root-docs-candidate-proof", "Root docs candidate proof", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]

    assert main(["task", "finish", task_id, "--verification-file", str(api / "proof.md"), "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "verification_file_inside_repo"

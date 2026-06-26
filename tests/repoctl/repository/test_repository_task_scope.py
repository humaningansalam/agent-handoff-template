from __future__ import annotations

import json
import shutil
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import commit_all, init_repo, write_settings
from tests.repoctl.workspace.test_check import write_workspace


def _setup_configured_multi_repo(tmp_path: Path, monkeypatch) -> tuple[Path, Path]:
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
    return web, api


def test_task_start_freezes_configured_repo_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    web, _ = _setup_configured_multi_repo(tmp_path, monkeypatch)

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
    web, _ = _setup_configured_multi_repo(tmp_path, monkeypatch)

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
    web, _ = _setup_configured_multi_repo(tmp_path, monkeypatch)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--start", "--slug", "doctor-web", "Doctor web", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    (web / "app.py").write_text("print('web changed')\n", encoding="utf-8")

    assert main(["task", "doctor", task_id, "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["repository"] == {"id": "web", "path": "repos/web", "identity_source": "pinned"}
    assert {warning["code"] for warning in payload["warnings"]} == {"missing_discovery_evidence", "missing_verification_file"}
    assert payload["problems"] == []


def test_task_create_repo_scoped_requires_repo_id_in_multi_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "create", "--area", "repo", "--slug", "missing-repo-id", "Missing repo id", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"


def test_adopted_multi_repo_task_can_finish_selected_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    web, _ = _setup_configured_multi_repo(tmp_path, monkeypatch)

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
    _setup_configured_multi_repo(tmp_path, monkeypatch)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--start", "--slug", "discovery-web", "Discovery web", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]

    assert main(["task", "discovery", "add", task_id, "--query", "api app", "--reviewed", "repos/api/app.py", "--chosen", "repos/api/app.py", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "discovery_outside_selected_repository"


def test_repo_task_discovery_rejects_dotdot_escape_from_selected_repository(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_configured_multi_repo(tmp_path, monkeypatch)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--start", "--slug", "discovery-dotdot", "Discovery dotdot", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]

    assert main(["task", "discovery", "add", task_id, "--query", "api app", "--reviewed", "repos/web/app.py", "--chosen", "repos/web/../api/app.py", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "discovery_outside_selected_repository"


def test_repo_task_allows_other_product_repo_preexisting_dirty_at_start(tmp_path: Path, monkeypatch, capsys) -> None:
    _, api = _setup_configured_multi_repo(tmp_path, monkeypatch)

    assert main(["task", "create", "--area", "repo", "--repo-id", "web", "--slug", "dirty-other", "Dirty other", "--json"]) == 0
    task_id = json.loads(capsys.readouterr().out)["data"]["task_id"]
    (api / "app.py").write_text("print('api dirty')\n", encoding="utf-8")

    assert main(["task", "start", task_id, "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["status"] == "doing"


def test_root_task_blocks_task_new_uncommitted_product_change_in_configured_multi(tmp_path: Path, monkeypatch, capsys) -> None:
    _, api = _setup_configured_multi_repo(tmp_path, monkeypatch)

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
    _, api = _setup_configured_multi_repo(tmp_path, monkeypatch)
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
    _, api = _setup_configured_multi_repo(tmp_path, monkeypatch)

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

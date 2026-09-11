from __future__ import annotations
from tests.repoctl.repository.test_repositories import init_repo, write_settings
import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.meta import shard_for_path
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import BASE_POLICY, write_repometa





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

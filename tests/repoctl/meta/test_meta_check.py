from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.meta import shard_for_path
from tests.repoctl.workspace.test_check import write_workspace


BASE_POLICY = {
    "schema_version": 1,
    "indexing": {
        "exclude": [
            ".git/**",
            ".repometa/**",
            "local-cache/**",
            "build-output/**",
            "third-party-snapshot/**",
            "generated-output/**",
            "**/__pycache__/**",
            "**/*.png",
        ]
    },
    "vocab": {
        "roles": {"base": ["service", "adapter", "config", "test", "workflow", "spec"], "extend": []},
        "declared_effects": {"base": ["none", "db", "net", "fs", "ui", "time", "crypto", "config"], "extend": ["queue"]},
    },
    "defaults": {
        "areas": {"backend": ["backend/**"], "frontend": ["frontend/**"], "infra": [".github/**"]},
        "topics": {"tests": ["**/tests/**", "**/*test*"], "api": ["frontend/src/api/**"], "auth": ["**/auth/**", "**/*token*"]},
    },
    "coverage": {"require_annotations": []},
}



def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")

def write_repometa(repo: Path, *, policy: dict | None = None, annotations: dict[str, dict] | None = None, exclusions: dict[str, dict] | None = None) -> None:
    meta = repo / ".repometa"
    write_json(meta / "policy.json", policy or BASE_POLICY)
    for shard in "0123456789abcdef":
        write_json(meta / "annotations" / f"{shard}.json", {"schema_version": 1, "annotations": {}, "exclusions": {}})
    for rel, annotation in (annotations or {}).items():
        shard = shard_for_path(rel)
        path = meta / "annotations" / f"{shard}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["annotations"][rel] = annotation
        write_json(path, data)
    for rel, exclusion in (exclusions or {}).items():
        shard = shard_for_path(rel)
        path = meta / "annotations" / f"{shard}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["exclusions"][rel] = exclusion
        write_json(path, data)

def init_repo(repo: Path) -> None:
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)

def commit_all(repo: Path) -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", "base"], cwd=repo, check=True, stdout=subprocess.DEVNULL)

def test_meta_check_requires_json_policy(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    (repo / "src.py").write_text("print('hello')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "missing_repometa_policy" for problem in payload["problems"])

def test_meta_check_changed_reports_repo_git_unavailable(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    write_repometa(repo)
    (repo / "src.py").write_text("print('hello')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--changed", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"

def test_meta_check_changed_allows_missing_repo_directory(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--changed", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["problems"] == []

def test_meta_status_changed_reports_repo_git_unavailable(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    write_repometa(repo)
    (repo / "src.py").write_text("print('hello')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "status", "--changed", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"

def test_meta_inventory_classifies_indexed_only_and_defaults(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "frontend/src/api/client.ts").write_text("export {}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "inventory", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    item = next(file for file in payload["data"]["files"] if file["path"] == "frontend/src/api/client.ts")
    assert item["classification"] == "indexed_only"
    assert item["areas"] == ["frontend"]
    assert "api" in item["default_topics"]

def test_meta_status_default_is_summary_first_and_verbose_lists_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    (repo / "backend").mkdir()
    (repo / "backend/service.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "status", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["summary"]["total"] == 1
    assert "files" not in payload["data"]

    assert main(["meta", "status", "--verbose", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["files"][0]["path"] == "backend/service.py"

def test_meta_set_writes_hash_shard_and_satisfies_coverage(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    policy = {**BASE_POLICY, "coverage": {"require_annotations": ["frontend/src/api/**"]}}
    write_repometa(repo, policy=policy)
    (repo / "frontend/src/api").mkdir(parents=True)
    rel = "frontend/src/api/billingGateway.ts"
    (repo / rel).write_text("export {}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "set", rel, "--role", "adapter", "--purpose", "call backend billing endpoints", "--topic", "api", "--topic", "billing", "--declared-effect", "net", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["shard"] == shard_for_path(rel)
    assert (repo / ".repometa/annotations" / f"{shard_for_path(rel)}.json").read_text(encoding="utf-8").find(rel) != -1
    assert main(["meta", "check", "--json"]) == 0

def test_meta_set_invalid_path_leaves_no_repometa_skeleton(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "set", "../bad.py", "--role", "service", "--purpose", "bad", "--topic", "bad", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repoctl_error"
    assert not (repo / ".repometa").exists()

def test_meta_set_and_exclude_reject_missing_file_without_mutation(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    before = sorted(path.relative_to(repo).as_posix() for path in (repo / ".repometa").rglob("*"))
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "set", "missing.py", "--role", "service", "--purpose", "missing", "--topic", "missing", "--json"]) == 2
    assert main(["meta", "exclude", "missing.py", "--reason", "false_positive", "--json"]) == 2

    capsys.readouterr()
    after = sorted(path.relative_to(repo).as_posix() for path in (repo / ".repometa").rglob("*"))
    assert before == after

def test_meta_status_warns_when_discovery_coverage_is_empty(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("print('app')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "status", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["warnings"][0]["code"] == "metadata_coverage_empty"
    assert payload["data"]["summary"]["indexed_only"] == 1

def test_meta_init_creates_default_policy_and_shards_without_overwriting(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "init", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["created_count"] == 17
    assert (repo / ".repometa/policy.json").is_file()
    assert all((repo / ".repometa/annotations" / f"{shard}.json").is_file() for shard in "0123456789abcdef")

    policy = json.loads((repo / ".repometa/policy.json").read_text(encoding="utf-8"))
    policy["coverage"] = {"require_annotations": ["src/**"]}
    write_json(repo / ".repometa/policy.json", policy)

    assert main(["meta", "init", "--json"]) == 0

    second = json.loads(capsys.readouterr().out)
    assert second["data"]["created_count"] == 0
    assert json.loads((repo / ".repometa/policy.json").read_text(encoding="utf-8"))["coverage"] == {"require_annotations": ["src/**"]}

def test_meta_check_changed_skips_policy_validation_when_no_repo_changes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    (repo / ".repometa/policy.json").write_text("{not-json\n", encoding="utf-8")
    commit_all(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--changed", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["problems"] == []
    assert payload["data"]["scope"] == "changed"

def test_meta_check_changed_validates_changed_repometa_store(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo, annotations={"app.py": {"role": "service", "purpose": "app", "topics": ["auth"]}})
    (repo / "app.py").write_text("print('app')\n", encoding="utf-8")
    commit_all(repo)
    shard = repo / ".repometa" / "annotations" / f"{shard_for_path('app.py')}.json"
    data = json.loads(shard.read_text(encoding="utf-8"))
    data["annotations"]["app.py"]["path"] = "app.py"
    write_json(shard, data)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--changed", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "forbidden_annotation_field" for problem in payload["problems"])

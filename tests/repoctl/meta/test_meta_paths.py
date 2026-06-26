from __future__ import annotations
from tests.repoctl.meta.test_meta_check import BASE_POLICY, commit_all, init_repo, write_json, write_repometa

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



def test_meta_check_rejects_wrong_shard_and_duplicate_path(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    rel = "backend/auth/token_service.py"
    (repo / "backend/auth").mkdir(parents=True)
    (repo / rel).write_text("def issue():\n    return 'x'\n", encoding="utf-8")
    annotation = {"role": "service", "purpose": "issue tokens", "topics": ["auth"], "declared_effects": ["crypto"]}
    write_repometa(repo, annotations={rel: annotation})
    correct = shard_for_path(rel)
    wrong = next(shard for shard in "0123456789abcdef" if shard != correct)
    wrong_path = repo / ".repometa/annotations" / f"{wrong}.json"
    data = json.loads(wrong_path.read_text(encoding="utf-8"))
    data["annotations"][rel] = annotation
    write_json(wrong_path, data)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    codes = {problem["code"] for problem in payload["problems"]}
    assert "wrong_annotation_shard" in codes
    assert "duplicate_annotation_path" in codes


def test_meta_check_changed_ignores_unrelated_wrong_shard(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    unrelated = "backend/auth/token_service.py"
    (repo / "backend/auth").mkdir(parents=True)
    (repo / unrelated).write_text("def issue():\n    return 'x'\n", encoding="utf-8")
    changed_rel = "frontend/src/api/newClient.ts"
    policy = {**BASE_POLICY, "coverage": {"require_annotations": ["frontend/src/api/**"]}}
    annotation = {"role": "service", "purpose": "issue tokens", "topics": ["auth"], "declared_effects": ["crypto"]}
    write_repometa(repo, policy=policy, annotations={unrelated: annotation})
    correct = shard_for_path(unrelated)
    wrong = next(shard for shard in "0123456789abcdef" if shard != correct)
    wrong_path = repo / ".repometa/annotations" / f"{wrong}.json"
    data = json.loads(wrong_path.read_text(encoding="utf-8"))
    data["annotations"][unrelated] = annotation
    write_json(wrong_path, data)
    commit_all(repo)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / changed_rel).write_text("export {}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--changed", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    codes = {problem["code"] for problem in payload["problems"]}
    assert "annotation_required" in codes
    assert "wrong_annotation_shard" not in codes
    assert "duplicate_annotation_path" not in codes


def test_meta_check_changed_includes_untracked_and_ignores_unrelated_full_orphan(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    policy = {**BASE_POLICY, "coverage": {"require_annotations": ["frontend/src/api/**"]}}
    write_repometa(repo, policy=policy, annotations={"backend/deleted.py": {"role": "service", "purpose": "old", "topics": ["old"]}})
    (repo / "keep.py").write_text("old\n", encoding="utf-8")
    commit_all(repo)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "frontend/src/api/newClient.ts").write_text("export {}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--changed", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "annotation_required" and "newClient" in problem["path"] for problem in payload["problems"])
    assert not any(problem["path"] == "repos/backend/deleted.py" for problem in payload["problems"])


def test_meta_check_changed_handles_unicode_git_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    policy = {**BASE_POLICY, "coverage": {"require_annotations": ["*.py"]}}
    write_repometa(repo, policy=policy)
    rel = "unicodé.py"
    (repo / rel).write_text("x = 1\n", encoding="utf-8")
    commit_all(repo)
    (repo / rel).write_text("x = 2\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--changed", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "annotation_required" and problem["path"] == f"repos/{rel}" for problem in payload["problems"])


def test_meta_check_changed_preserves_leading_space_filename_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    policy = {**BASE_POLICY, "coverage": {"require_annotations": ["*.py"]}}
    write_repometa(repo, policy=policy, annotations={"foo.py": {"role": "service", "purpose": "normal file", "topics": ["auth"]}})
    (repo / "foo.py").write_text("x = 1\n", encoding="utf-8")
    (repo / " foo.py").write_text("x = 1\n", encoding="utf-8")
    commit_all(repo)
    (repo / " foo.py").write_text("x = 2\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--changed", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "annotation_required" and problem["path"] == "repos/ foo.py" for problem in payload["problems"])


def test_meta_move_handles_cross_shard_without_loss(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    old = "backend/auth/token_service.py"
    new = next(f"backend/auth/token_service_{index}.py" for index in range(100) if shard_for_path(f"backend/auth/token_service_{index}.py") != shard_for_path(old))
    (repo / "backend/auth").mkdir(parents=True)
    (repo / old).write_text("old\n", encoding="utf-8")
    (repo / new).write_text("new\n", encoding="utf-8")
    annotation = {"role": "service", "purpose": "issue tokens", "topics": ["auth"]}
    write_repometa(repo, annotations={old: annotation})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "move", old, new, "--json"]) == 0
    old_data = json.loads((repo / ".repometa/annotations" / f"{shard_for_path(old)}.json").read_text(encoding="utf-8"))
    new_data = json.loads((repo / ".repometa/annotations" / f"{shard_for_path(new)}.json").read_text(encoding="utf-8"))
    assert old not in old_data["annotations"]
    assert new in new_data["annotations"]


def test_path_normalization_routes_repo_prefix(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    rel = "frontend/src/api/client.ts"
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / rel).write_text("export {}\n", encoding="utf-8")
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "set", "repos/frontend/src/api/client.ts", "--role", "adapter", "--purpose", "call API", "--topic", "api", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["path"] == rel
    assert payload["data"]["shard"] == shard_for_path(rel)


def test_meta_set_caution_file_round_trip(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    rel = "frontend/src/api/client.ts"
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / rel).write_text("export {}\n", encoding="utf-8")
    caution = tmp_path / "caution.txt"
    caution.write_text("keep response compatibility\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "set", rel, "--role", "adapter", "--purpose", "call API", "--topic", "api", "--caution-file", str(caution), "--json"]) == 0
    capsys.readouterr()
    assert main(["meta", "show", rel, "--json"]) == 0

    show_payload = json.loads(capsys.readouterr().out)
    assert show_payload["data"]["annotation"]["caution"] == ["keep response compatibility"]
    assert main(["meta", "check", "--json"]) == 0


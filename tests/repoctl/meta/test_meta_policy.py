from __future__ import annotations
from tests.repoctl.meta.test_meta_check import BASE_POLICY, init_repo, write_repometa

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.workspace.test_check import write_workspace


def test_project_policy_can_exclude_project_local_outputs(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    policy = {**BASE_POLICY, "indexing": {"exclude": ["local-cache/**"]}}
    write_repometa(repo, policy=policy)
    (repo / "local-cache").mkdir()
    (repo / "local-cache/noise.txt").write_text("cache\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src/app.py").write_text("print('ok')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "status", "--verbose", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    paths = [item["path"] for item in payload["data"]["files"]]
    assert "local-cache/noise.txt" not in paths
    assert "src/app.py" in paths


def test_meta_check_requires_annotation_for_coverage_match(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    policy = {**BASE_POLICY, "coverage": {"require_annotations": ["frontend/src/api/**"]}}
    write_repometa(repo, policy=policy)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "frontend/src/api/billingGateway.ts").write_text("export {}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "annotation_required" and problem["path"] == "repos/frontend/src/api/billingGateway.ts" for problem in payload["problems"])


def test_meta_check_rejects_forbidden_fields_and_none_combo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    rel = "backend/auth/token_service.py"
    (repo / "backend/auth").mkdir(parents=True)
    (repo / rel).write_text("def issue():\n    return 'x'\n", encoding="utf-8")
    write_repometa(repo, annotations={rel: {"role": "service", "purpose": "issue tokens", "topics": ["auth"], "declared_effects": ["crypto", "none"], "path": rel, "relates_to": ["docs/spec.md"]}})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    codes = {problem["code"] for problem in payload["problems"]}
    assert "forbidden_annotation_field" in codes
    assert "invalid_declared_effects_none_combo" in codes


def test_meta_check_blocks_inline_meta_residue(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    (repo / "src.py").write_text("# @meta\nprint('forbidden')\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "inline_meta_residue" for problem in payload["problems"])


def test_meta_exclude_allows_covered_false_positive(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    policy = {**BASE_POLICY, "coverage": {"require_annotations": ["frontend/src/api/**"]}}
    write_repometa(repo, policy=policy)
    rel = "frontend/src/api/service_stub.ts"
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / rel).write_text("export {}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "exclude", rel, "--reason", "test_stub", "--json"]) == 0
    assert main(["meta", "check", "--json"]) == 0


def test_policy_deep_validation_rejects_silent_coverage_disable(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    bad_policy = {**BASE_POLICY, "coverage": {"require_annotations": "frontend/src/api/**"}}
    write_repometa(repo, policy=bad_policy)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "frontend/src/api/client.ts").write_text("export {}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "invalid_policy_coverage" for problem in payload["problems"])


def test_orphan_exclusion_has_own_classification_and_repoctl_repair(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo, exclusions={"missing.py": {"reason": "stale", "excluded_by": "agent"}})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "status", "--verbose", "--json"]) == 0
    inventory = json.loads(capsys.readouterr().out)
    item = next(file for file in inventory["data"]["files"] if file["path"] == "missing.py")
    assert item["classification"] == "orphan_exclusion"

    assert main(["meta", "check", "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "orphan_exclusion" for problem in payload["problems"])

    assert main(["meta", "remove", "missing.py", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["removed_exclusion"] is True
    assert main(["meta", "check", "--json"]) == 0


def test_meta_check_rejects_coverage_pattern_hidden_by_exclude(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    policy = {
        **BASE_POLICY,
        "indexing": {"exclude": [".git/**", ".repometa/**", "frontend/src/api/**"]},
        "coverage": {"require_annotations": ["frontend/src/api/**"]},
    }
    write_repometa(repo, policy=policy)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "frontend/src/api/billingGateway.ts").write_text("export {}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "policy_coverage_excluded" for problem in payload["problems"])


def test_meta_check_rejects_partial_coverage_exclude_overlap(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    policy = {
        **BASE_POLICY,
        "indexing": {"exclude": [".git/**", ".repometa/**", "src/generated/**"]},
        "coverage": {"require_annotations": ["src/**/*.py"]},
    }
    write_repometa(repo, policy=policy)
    (repo / "src/generated").mkdir(parents=True)
    (repo / "src/generated/client.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "check", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "policy_coverage_excluded" for problem in payload["problems"])

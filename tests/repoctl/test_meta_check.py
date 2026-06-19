from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.meta import DEFAULT_POLICY, shard_for_path
from tests.repoctl.test_check import write_workspace


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


def test_default_policy_is_ecosystem_neutral() -> None:
    excludes = set(DEFAULT_POLICY["indexing"]["exclude"])
    directory_excludes = {pattern for pattern in excludes if pattern.endswith("/**") and not pattern.startswith("**/")}
    assert directory_excludes == {".git/**", ".repometa/**"}
    assert {"**/*.png", "**/*.zip"} <= excludes


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


def test_path_normalization_routes_repo_prefix_and_backslashes(tmp_path: Path, monkeypatch, capsys) -> None:
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

    assert main(["meta", "set", "repos/frontend\\src\\api\\client.ts", "--role", "adapter", "--purpose", "call API", "--topic", "api", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["path"] == rel
    assert payload["data"]["shard"] == shard_for_path(rel)


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

    assert main(["meta", "inventory", "--json"]) == 0
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


def test_meta_query_filters_annotated_files_by_role_topic_area_and_effect(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "backend").mkdir()
    billing = "frontend/src/api/billingGateway.ts"
    service = "backend/service.py"
    (repo / billing).write_text("export {}\n", encoding="utf-8")
    (repo / service).write_text("def run():\n    return 1\n", encoding="utf-8")
    write_repometa(
        repo,
        annotations={
            billing: {
                "role": "adapter",
                "purpose": "call backend billing endpoints",
                "topics": ["api", "billing"],
                "declared_effects": ["net"],
            },
            service: {
                "role": "service",
                "purpose": "run backend domain logic",
                "topics": ["backend"],
                "declared_effects": ["none"],
            },
        },
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "query", "--role", "adapter", "--topic", "billing", "--area", "frontend", "--declared-effect", "net", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert [candidate["path"] for candidate in payload["data"]["candidates"]] == [billing]
    candidate = payload["data"]["candidates"][0]
    assert "role:adapter" in candidate["signals"]
    assert "topic:billing" in candidate["signals"]
    assert "area:frontend" in candidate["signals"]
    assert "effect:net" in candidate["signals"]
    assert candidate["annotation"]["purpose"] == "call backend billing endpoints"


def test_meta_suggest_returns_non_authoritative_candidate_signals(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "tests").mkdir()
    billing = "frontend/src/api/billingGateway.ts"
    test_file = "tests/test_billing_gateway.py"
    (repo / billing).write_text("export {}\n", encoding="utf-8")
    (repo / test_file).write_text("def test_billing():\n    assert True\n", encoding="utf-8")
    write_repometa(
        repo,
        annotations={
            billing: {
                "role": "adapter",
                "purpose": "call backend billing endpoints",
                "topics": ["api", "billing"],
                "declared_effects": ["net"],
            }
        },
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "suggest", "--text", "billing retry", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is True
    assert payload["data"]["suggestion"]["authoritative"] is False
    assert payload["warnings"] == [
        {
            "code": "suggestion_not_authoritative",
            "message": "meta suggest returns candidate files only; inspect files before creating or changing task scope",
        }
    ]
    paths = [candidate["path"] for candidate in payload["data"]["candidates"]]
    assert billing in paths
    assert test_file in paths
    billing_candidate = next(candidate for candidate in payload["data"]["candidates"] if candidate["path"] == billing)
    assert any(signal.startswith("annotation:billing") for signal in billing_candidate["signals"])
    assert any(signal.startswith("filename:billing") for signal in billing_candidate["signals"])


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


def test_meta_suggest_preserves_unicode_query_tokens(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    rel = "docs/검색.md"
    (repo / "docs").mkdir()
    (repo / rel).write_text("# Search\n", encoding="utf-8")
    write_repometa(
        repo,
        annotations={
            rel: {
                "role": "spec",
                "purpose": "document search behavior",
                "topics": ["search"],
                "declared_effects": ["none"],
            }
        },
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "suggest", "--text", "검색 기능", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["suggestion"]["tokens"] == ["검색", "기능"]
    assert payload["data"]["candidates"][0]["path"] == rel
    assert "filename:검색" in payload["data"]["candidates"][0]["signals"]


def test_meta_suggest_accepts_positional_text_alias(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    rel = "src/search.ts"
    (repo / "src").mkdir()
    (repo / rel).write_text("export const search = true\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "suggest", "search flow", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["suggestion"]["text"] == "search flow"
    assert payload["data"]["candidates"][0]["path"] == rel


def test_meta_suggest_matches_identifier_tokens_not_arbitrary_substrings(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    (repo / "src").mkdir()
    (repo / "src/DelegateAdapter.ts").write_text("export class DelegateAdapter {}\n", encoding="utf-8")
    (repo / "src/release-gate.ts").write_text("export const gate = true\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["meta", "suggest", "release gate", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    paths = [candidate["path"] for candidate in payload["data"]["candidates"]]
    assert "src/release-gate.ts" in paths
    assert "src/DelegateAdapter.ts" not in paths


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

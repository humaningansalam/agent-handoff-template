from __future__ import annotations
from tests.repoctl.meta.test_meta_check import init_repo, write_repometa

import json
from pathlib import Path

from tools.repoctl.cli import main
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


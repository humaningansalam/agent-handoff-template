from __future__ import annotations

import json
import hashlib
from pathlib import Path

from tools.repoctl.code_index import CodeIndexEntry
from tools.repoctl.cli import main
from tools.repoctl.graph import build_graph
from tools.repoctl.graph_model import canonical_json, file_id, import_ref_id, topic_id
from tools.repoctl.repositories import require_repo_target
from tools.repoctl.tasks import Problem
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo, write_settings



def _snapshot(payload: dict) -> dict:
    return payload["data"]["snapshot"]

def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()

def test_graph_build_direct_repo_uses_main(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("import hashlib\n\ndef run():\n    return hashlib.sha256(b'x').hexdigest()\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    snapshot = _snapshot(payload)
    assert snapshot["repository"] == {"id": "main", "path": "repos", "identity_source": "reserved"}
    assert any(node["id"] == "repo:main" and node["kind"] == "repository" for node in snapshot["nodes"])
    assert any(node["id"] == file_id("main", "app.py") and node["kind"] == "file" for node in snapshot["nodes"])

def test_graph_build_configured_multi_requires_repo_id(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_repometa(tmp_path / "repos/web")
    write_repometa(tmp_path / "repos/api")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_selector_required"

def test_graph_build_configured_multi_includes_only_selected_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("import requests\n", encoding="utf-8")
    (api / "app.py").write_text("import urllib.request\n", encoding="utf-8")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--repo-id", "web", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert snapshot["repository"]["id"] == "web"
    assert any(node["id"] == file_id("web", "app.py") for node in snapshot["nodes"])
    assert not any(node["id"].startswith("repo:api:") for node in snapshot["nodes"])

def test_graph_build_unconfigured_collection_fails(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--repo-id", "web", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "repository_identity_unbound"

def test_graph_topics_keep_policy_and_annotation_provenance(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    rel = "backend/auth/token_service.py"
    (repo / "backend/auth").mkdir(parents=True)
    (repo / rel).write_text("def issue():\n    return 'token'\n", encoding="utf-8")
    write_repometa(
        repo,
        annotations={rel: {"role": "service", "purpose": "issue tokens", "topics": ["session"], "declared_effects": ["none"], "caution": []}},
    )
    before = {path.as_posix(): path.read_text(encoding="utf-8") for path in (repo / ".repometa").rglob("*.json")}
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    file_node = next(node for node in snapshot["nodes"] if node["id"] == file_id("main", rel))
    assert file_node["facts"]["policy"]["topics"] == ["auth"]
    assert file_node["facts"]["annotation"]["topics"] == ["session"]
    assert any(edge["kind"] == "HAS_TOPIC" and edge["to"] == topic_id("main", "auth") and edge["assertion"] == "default" for edge in snapshot["edges"])
    assert any(edge["kind"] == "HAS_TOPIC" and edge["to"] == topic_id("main", "session") and edge["assertion"] == "declared" for edge in snapshot["edges"])
    after = {path.as_posix(): path.read_text(encoding="utf-8") for path in (repo / ".repometa").rglob("*.json")}
    assert after == before

def test_graph_snapshot_is_byte_stable(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("import hashlib\n", encoding="utf-8")
    target = require_repo_target(tmp_path, repo_id="main")

    first, first_problems, _ = build_graph(tmp_path, target=target)
    second, second_problems, _ = build_graph(tmp_path, target=target)

    assert first_problems == []
    assert second_problems == []
    assert first is not None
    assert second is not None
    assert canonical_json(first.to_dict()) == canonical_json(second.to_dict())

def test_graph_index_truncation_fails(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    target = require_repo_target(tmp_path, repo_id="main")

    def truncated_index(*args, **kwargs):
        return [], [], {"summary": {"truncated": True, "total": 2, "returned": 1, "parse_error": 0}}

    monkeypatch.setattr("tools.repoctl.graph.build_code_index", truncated_index)

    snapshot, problems, _meta = build_graph(tmp_path, target=target)

    assert snapshot is None
    assert problems[0].code == "graph_index_truncated"

def test_graph_build_keeps_snapshot_with_code_index_warning(tmp_path: Path, monkeypatch) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    target = require_repo_target(tmp_path, repo_id="main")

    def warning_index(*args, **kwargs):
        return (
            [
                CodeIndexEntry(
                    path="app.py",
                    workspace_path="repos/app.py",
                    language="python",
                    classification="indexed_only",
                    symbols=[],
                    imports=[],
                    calls=[],
                    deps=[],
                    observed_effects=[],
                )
            ],
            [Problem("warning", "index_warning", "non-fatal index warning", "repos/app.py")],
            {"summary": {"truncated": False, "parse_error": 0}},
        )

    monkeypatch.setattr("tools.repoctl.graph.build_code_index", warning_index)

    snapshot, problems, _meta = build_graph(tmp_path, target=target)

    assert snapshot is not None
    assert problems[0].severity == "warning"
    assert any(node.id == file_id("main", "app.py") for node in snapshot.nodes)

def test_graph_parse_error_keeps_file_node_and_marks_completeness(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "bad.py").write_text("def broken(:\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert snapshot["completeness"]["code_facts_complete"] is False
    assert snapshot["completeness"]["parse_error_count"] == 1
    file_node = next(node for node in snapshot["nodes"] if node["id"] == file_id("main", "bad.py"))
    assert file_node["facts"]["index"]["parse_status"] == "parse_error"

def test_graph_id_encoding_avoids_collisions() -> None:
    assert file_id("main", "a/b") != file_id("main", "a%2Fb")
    assert import_ref_id("main", "typescript", "a:b") != import_ref_id("main", "typescript", "a/b")
    assert topic_id("web", "auth") != topic_id("api", "auth")

def test_graph_python_ast_provider_adds_symbol_and_anchor_nodes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "service.py").write_text(
        "class TokenService:\n"
        "    def issue(self):\n"
        "        return 'token'\n\n"
        "def helper():\n"
        "    return TokenService()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert "symbol" in snapshot["capabilities"]
    assert "anchor" in snapshot["capabilities"]
    symbols = [node for node in snapshot["nodes"] if node["kind"] == "symbol"]
    assert {node["facts"]["provider"]["qualified_name"] for node in symbols} == {"TokenService", "TokenService.issue", "helper"}
    assert all(node["identity"]["provider"] == "python_ast" for node in symbols)
    assert any(node["kind"] == "anchor" and node["identity"]["path"] == "service.py" for node in snapshot["nodes"])
    assert any(edge["kind"] == "DEFINES" and edge["from"] == file_id("main", "service.py") for edge in snapshot["edges"])
    assert any(edge["kind"] == "ANCHORS" and edge["assertion"] == "resolved" and edge["source"] == "python_ast" for edge in snapshot["edges"])

def test_graph_python_ast_provider_distinguishes_nested_function_from_method(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "nested.py").write_text(
        "class Service:\n"
        "    def method(self):\n"
        "        def inner():\n"
        "            return 1\n"
        "        return inner()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    kinds = {node["facts"]["provider"]["qualified_name"]: node["facts"]["provider"]["kind"] for node in snapshot["nodes"] if node["kind"] == "symbol"}
    assert kinds["Service.method"] == "method"
    assert kinds["Service.method.inner"] == "function"

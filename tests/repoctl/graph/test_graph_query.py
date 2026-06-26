from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import file_id, import_ref_id, topic_id
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo


def test_graph_query_file_returns_typed_subgraph(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "src").mkdir()
    (repo / "src/app.py").write_text("import hashlib\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--file", "src/app.py", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert result["query"] == {"type": "file", "path": "src/app.py"}
    assert any(node["id"] == file_id("main", "src/app.py") for node in result["nodes"])
    assert any(edge["kind"] == "CONTAINS" and edge["to"] == file_id("main", "src/app.py") for edge in result["edges"])

    assert main(["graph", "query", "--file", "./src\\app.py", "--json"]) == 1
    not_found = json.loads(capsys.readouterr().out)
    assert not_found["problems"][0]["code"] == "graph_query_not_found"
    assert not_found["problems"][0]["path"] == "src\\app.py"


def test_graph_query_topic_returns_matching_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    rel = "backend/auth/token_service.py"
    (repo / "backend/auth").mkdir(parents=True)
    (repo / rel).write_text("def issue():\n    return 'token'\n", encoding="utf-8")
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--topic", "auth", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert result["query"] == {"type": "topic", "topic": "auth"}
    assert any(node["id"] == topic_id("main", "auth") and node["kind"] == "topic" for node in result["nodes"])
    assert any(node["id"] == file_id("main", rel) for node in result["nodes"])
    assert any(edge["kind"] == "HAS_TOPIC" and edge["assertion"] == "default" for edge in result["edges"])


def test_graph_query_import_returns_declaring_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "frontend").mkdir()
    (repo / "frontend/app.ts").write_text("import axios from 'axios';\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--import", "axios", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert result["query"] == {"type": "import", "raw_import": "axios"}
    assert any(node["id"] == import_ref_id("main", "typescript", "axios") for node in result["nodes"])
    assert any(node["id"] == file_id("main", "frontend/app.ts") for node in result["nodes"])
    assert any(edge["kind"] == "DECLARES_IMPORT" for edge in result["edges"])


def test_graph_query_requires_exactly_one_selector(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--json"]) == 1
    missing = json.loads(capsys.readouterr().out)
    assert missing["problems"][0]["code"] == "graph_query_selector_required"

    assert main(["graph", "query", "--file", "app.py", "--topic", "auth", "--json"]) == 1
    ambiguous = json.loads(capsys.readouterr().out)
    assert ambiguous["problems"][0]["code"] == "graph_query_selector_ambiguous"

    assert main(["graph", "query", "--file", "missing.py", "--json"]) == 1
    missing = json.loads(capsys.readouterr().out)
    assert missing["problems"][0]["code"] == "graph_query_not_found"


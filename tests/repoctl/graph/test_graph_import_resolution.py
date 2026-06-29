from __future__ import annotations
from tests.repoctl.graph.test_graph_build import _snapshot

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import file_id, import_ref_id
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo


def test_graph_imports_are_raw_import_refs(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "frontend/src").mkdir(parents=True)
    (repo / "frontend/src/app.ts").write_text("import axios from 'axios';\nexport const run = () => fetch('/');\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    raw_import_id = import_ref_id("main", "typescript", "axios")
    assert any(node["id"] == raw_import_id and node["kind"] == "import_ref" for node in snapshot["nodes"])
    assert any(edge["kind"] == "DECLARES_IMPORT" and edge["to"] == raw_import_id for edge in snapshot["edges"])
    assert not any(node["kind"] in {"module", "package", "symbol"} for node in snapshot["nodes"])


def test_graph_resolves_repo_local_python_imports(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "utils").mkdir()
    (repo / "handlers").mkdir()
    (repo / "utils/__init__.py").write_text("", encoding="utf-8")
    (repo / "utils/tokens.py").write_text("def issue_token(user_id: str) -> str:\n    return f'token:{user_id}'\n", encoding="utf-8")
    (repo / "handlers/login.py").write_text(
        "from utils.tokens import issue_token as make_session\n\n\ndef login(user_id: str) -> str:\n    return make_session(user_id)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    import_node_id = import_ref_id("main", "python", "utils.tokens.issue_token")
    source_file_id = file_id("main", "handlers/login.py")
    target_file_id = file_id("main", "utils/tokens.py")
    assert "import_resolution" in snapshot["capabilities"]
    assert any(source["kind"] == "python_import_resolver" and source["assertion"] == "resolved" for source in snapshot["sources"])
    assert any(edge["kind"] == "DECLARES_IMPORT" and edge["from"] == source_file_id and edge["to"] == import_node_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "RESOLVES_TO" and edge["from"] == import_node_id and edge["to"] == target_file_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == source_file_id and edge["to"] == target_file_id for edge in snapshot["edges"])

    assert main(["graph", "query", "--file", "utils/tokens.py", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert any(node["id"] == source_file_id for node in result["nodes"])
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == source_file_id and edge["to"] == target_file_id for edge in result["edges"])


def test_graph_resolves_relative_python_imports(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "handlers").mkdir()
    (repo / "handlers/__init__.py").write_text("", encoding="utf-8")
    (repo / "handlers/tokens.py").write_text("def issue_token(user_id: str) -> str:\n    return f'token:{user_id}'\n", encoding="utf-8")
    (repo / "handlers/login.py").write_text(
        "from .tokens import issue_token as make_session\n\n\ndef login(user_id: str) -> str:\n    return make_session(user_id)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    import_node_id = import_ref_id("main", "python", ".tokens.issue_token")
    source_file_id = file_id("main", "handlers/login.py")
    target_file_id = file_id("main", "handlers/tokens.py")
    assert any(edge["kind"] == "RESOLVES_TO" and edge["from"] == import_node_id and edge["to"] == target_file_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == source_file_id and edge["to"] == target_file_id for edge in snapshot["edges"])


def test_graph_skips_ambiguous_python_import_resolution(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "pkg/mod").mkdir(parents=True)
    (repo / "consumer").mkdir()
    (repo / "pkg/__init__.py").write_text("", encoding="utf-8")
    (repo / "pkg/mod.py").write_text("VALUE = 'module'\n", encoding="utf-8")
    (repo / "pkg/mod/__init__.py").write_text("VALUE = 'package'\n", encoding="utf-8")
    (repo / "consumer/app.py").write_text("from pkg.mod import VALUE\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    import_node_id = import_ref_id("main", "python", "pkg.mod.VALUE")
    assert any(edge["kind"] == "DECLARES_IMPORT" and edge["to"] == import_node_id for edge in snapshot["edges"])
    assert not any(edge["kind"] == "RESOLVES_TO" and edge["from"] == import_node_id for edge in snapshot["edges"])
    assert not any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == file_id("main", "consumer/app.py") for edge in snapshot["edges"])


def test_graph_resolves_js_ts_relative_imports(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "frontend/src/client.ts").write_text("import { issueToken } from './api/tokens';\nexport const login = () => issueToken();\n", encoding="utf-8")
    (repo / "frontend/src/api/tokens.ts").write_text("export const issueToken = () => 'token';\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    import_node_id = import_ref_id("main", "typescript", "./api/tokens")
    source_file_id = file_id("main", "frontend/src/client.ts")
    target_file_id = file_id("main", "frontend/src/api/tokens.ts")
    assert any(source["kind"] == "js_ts_relative_import_resolver" and source["assertion"] == "resolved" for source in snapshot["sources"])
    assert any(edge["kind"] == "RESOLVES_TO" and edge["from"] == import_node_id and edge["to"] == target_file_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == source_file_id and edge["to"] == target_file_id for edge in snapshot["edges"])


def test_graph_skips_ambiguous_js_ts_relative_import_resolution(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "frontend/src/client.ts").write_text("import { issueToken } from './api/tokens';\n", encoding="utf-8")
    (repo / "frontend/src/api/tokens.ts").write_text("export const issueToken = () => 'ts';\n", encoding="utf-8")
    (repo / "frontend/src/api/tokens.js").write_text("export const issueToken = () => 'js';\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    import_node_id = import_ref_id("main", "typescript", "./api/tokens")
    assert any(edge["kind"] == "DECLARES_IMPORT" and edge["to"] == import_node_id for edge in snapshot["edges"])
    assert not any(edge["kind"] == "RESOLVES_TO" and edge["from"] == import_node_id for edge in snapshot["edges"])
    assert not any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == file_id("main", "frontend/src/client.ts") for edge in snapshot["edges"])


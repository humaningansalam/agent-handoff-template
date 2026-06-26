from __future__ import annotations
from tests.repoctl.graph.test_graph_build import _snapshot

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import file_id, symbol_id
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo


def test_graph_resolves_same_file_python_calls(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "auth").mkdir()
    (repo / "auth/flow.py").write_text(
        'def validate_token(token: str) -> bool:\n    return token == "ok"\n\n\ncheck_token = validate_token\n\n\ndef login(token: str) -> str:\n    if check_token(token):\n        return "ok"\n    return "denied"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    validate_id = symbol_id("main", "python_ast", "python_ast:auth/flow.py:validate_token:function:1:0:2:24")
    login_id = symbol_id("main", "python_ast", "python_ast:auth/flow.py:login:function:8:0:11:19")
    assert "same_file_calls" in snapshot["capabilities"]
    assert any(source["kind"] == "python_ast_calls" and source["assertion"] == "resolved" for source in snapshot["sources"])
    assert any(edge["kind"] == "CALLS" and edge["from"] == login_id and edge["to"] == validate_id and edge["facts"]["scope"] == "same_file" for edge in snapshot["edges"])


def test_graph_resolves_same_class_python_method_calls(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "auth").mkdir()
    (repo / "auth/method_flow.py").write_text(
        'class TokenFlow:\n    def validate(self, token: str) -> bool:\n        return token == "ok"\n\n    def login(self, token: str) -> str:\n        if self.validate(token):\n            return "ok"\n        return "denied"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    validate_id = symbol_id("main", "python_ast", "python_ast:auth/method_flow.py:TokenFlow.validate:method:2:4:3:28")
    login_id = symbol_id("main", "python_ast", "python_ast:auth/method_flow.py:TokenFlow.login:method:5:4:8:23")
    assert any(edge["kind"] == "CALLS" and edge["from"] == login_id and edge["to"] == validate_id and edge["facts"]["scope"] == "same_file" for edge in snapshot["edges"])


def test_graph_resolves_cross_file_python_imported_function_calls(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "services").mkdir()
    (repo / "handlers").mkdir()
    (repo / "services/token_service.py").write_text(
        "def issue_token(user_id: str) -> str:\n    return f'token:{user_id}'\n",
        encoding="utf-8",
    )
    (repo / "handlers/cross_login.py").write_text(
        "from services.token_service import issue_token\n\n\ndef login(user_id: str) -> str:\n    return issue_token(user_id)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    issue_id = symbol_id("main", "python_ast", "python_ast:services/token_service.py:issue_token:function:1:0:2:29")
    login_id = symbol_id("main", "python_ast", "python_ast:handlers/cross_login.py:login:function:4:0:5:31")
    assert "cross_file_import_calls" in snapshot["capabilities"]
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == file_id("main", "handlers/cross_login.py") and edge["to"] == file_id("main", "services/token_service.py") for edge in snapshot["edges"])
    assert any(edge["kind"] == "CALLS" and edge["from"] == login_id and edge["to"] == issue_id and edge["facts"]["scope"] == "cross_file_import" for edge in snapshot["edges"])


def test_graph_skips_shadowed_cross_file_python_imported_function_calls(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "services").mkdir()
    (repo / "handlers").mkdir()
    (repo / "services/token_service.py").write_text(
        "def issue_token(user_id: str) -> str:\n    return f'token:{user_id}'\n",
        encoding="utf-8",
    )
    (repo / "handlers/cross_login.py").write_text(
        "from services.token_service import issue_token\n\n\ndef login(issue_token) -> str:\n    return issue_token()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    issue_id = symbol_id("main", "python_ast", "python_ast:services/token_service.py:issue_token:function:1:0:2:29")
    login_id = symbol_id("main", "python_ast", "python_ast:handlers/cross_login.py:login:function:4:0:5:24")
    assert not any(edge["kind"] == "CALLS" and edge["from"] == login_id and edge["to"] == issue_id for edge in snapshot["edges"])


def test_graph_query_symbol_callers_and_callees(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "auth").mkdir()
    (repo / "auth/flow.py").write_text(
        'def validate_token(token: str) -> bool:\n    return token == "ok"\n\n\ndef login(token: str) -> str:\n    if validate_token(token):\n        return "ok"\n    return "denied"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--symbol", "validate_token", "--json"]) == 0
    symbol_result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert symbol_result["query"] == {"type": "symbol", "symbol": "validate_token"}
    assert symbol_result["matches"][0]["qualified_name"] == "validate_token"
    assert symbol_result["matches"][0]["path"] == "auth/flow.py"

    assert main(["graph", "query", "--callers-of", "validate_token", "--in-file", "auth/flow.py", "--json"]) == 0
    callers_result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert callers_result["query"] == {"type": "callers_of", "symbol": "validate_token", "in_file": "auth/flow.py"}
    assert any(path["edge"] == "CALLS" and path["from"]["qualified_name"] == "login" and path["to"]["qualified_name"] == "validate_token" for path in callers_result["paths"])

    assert main(["graph", "query", "--callees-of", "login", "--in-file", "auth/flow.py", "--json"]) == 0
    callees_result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert any(path["edge"] == "CALLS" and path["from"]["qualified_name"] == "login" and path["to"]["qualified_name"] == "validate_token" for path in callees_result["paths"])


def test_graph_query_symbol_ambiguity_fails_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "web").mkdir()
    (repo / "api").mkdir()
    (repo / "web/auth.py").write_text("def login():\n    return 'web'\n", encoding="utf-8")
    (repo / "api/auth.py").write_text("def login():\n    return 'api'\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--symbol", "login", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "graph_query_ambiguous_symbol"
    result = payload["data"]["result"]
    assert {match["path"] for match in result["matches"]} == {"api/auth.py", "web/auth.py"}

    assert main(["graph", "query", "--symbol", "login", "--in-file", "api/auth.py", "--json"]) == 0
    narrowed = json.loads(capsys.readouterr().out)["data"]["result"]
    assert narrowed["matches"][0]["path"] == "api/auth.py"


def test_graph_query_impact_file_uses_import_and_call_edges(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "services").mkdir()
    (repo / "handlers").mkdir()
    (repo / "services/token_service.py").write_text("def issue_token(user_id: str) -> str:\n    return f'token:{user_id}'\n", encoding="utf-8")
    (repo / "handlers/login.py").write_text(
        "from services.token_service import issue_token\n\n\ndef login(user_id: str) -> str:\n    return issue_token(user_id)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--impact-file", "services/token_service.py", "--depth", "2", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert result["query"] == {"type": "impact_file", "path": "services/token_service.py", "depth": 2}
    assert any(path["edge"] == "IMPORTS_FILE" and path["from"]["path"] == "handlers/login.py" for path in result["paths"])
    assert any(path["edge"] == "CALLS" and path["from"]["qualified_name"] == "login" and path["to"]["qualified_name"] == "issue_token" for path in result["paths"])


def test_graph_query_js_ts_impact_is_file_level(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "frontend/src/client.ts").write_text("import { issueToken } from './api/tokens';\nexport const login = () => issueToken();\n", encoding="utf-8")
    (repo / "frontend/src/api/tokens.ts").write_text("export const issueToken = () => 'token';\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--impact-file", "frontend/src/api/tokens.ts", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert any(path["edge"] == "IMPORTS_FILE" and path["from"]["path"] == "frontend/src/client.ts" for path in result["paths"])
    assert not any(path["edge"] == "CALLS" for path in result["paths"])


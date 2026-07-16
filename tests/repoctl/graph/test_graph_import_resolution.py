from __future__ import annotations
from tests.repoctl.graph.test_graph_build import _snapshot

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import file_id, import_ref_id
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo


def _from_import_id(
    importer_path: str,
    raw_import: str,
    *,
    module: str,
    imported_name: str,
    level: int = 0,
) -> str:
    return import_ref_id(
        "main",
        importer_path,
        "python",
        raw_import,
        form="from",
        level=level,
        module=module,
        imported_name=imported_name,
    )


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
    raw_import_id = import_ref_id("main", "frontend/src/app.ts", "typescript", "axios")
    assert any(node["id"] == raw_import_id and node["kind"] == "import_ref" for node in snapshot["nodes"])
    assert any(edge["kind"] == "DECLARES_IMPORT" and edge["to"] == raw_import_id for edge in snapshot["edges"])
    assert not any(node["kind"] in {"module", "package"} for node in snapshot["nodes"])


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
    import_node_id = _from_import_id(
        "handlers/login.py",
        "utils.tokens.issue_token",
        module="utils.tokens",
        imported_name="issue_token",
    )
    source_file_id = file_id("main", "handlers/login.py")
    target_file_id = file_id("main", "utils/tokens.py")
    assert "import_resolution" in snapshot["capabilities"]
    assert any(source["kind"] == "python_import_resolver" and source["assertion"] == "resolved" for source in snapshot["sources"])
    assert any(edge["kind"] == "DECLARES_IMPORT" and edge["from"] == source_file_id and edge["to"] == import_node_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "RESOLVES_TO" and edge["from"] == import_node_id and edge["to"] == target_file_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == source_file_id and edge["to"] == target_file_id for edge in snapshot["edges"])

    assert main(["graph", "query", "--file", "utils/tokens.py", "--full", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert any(node["id"] == source_file_id for node in result["nodes"])
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == source_file_id and edge["to"] == target_file_id for edge in result["edges"])


def test_graph_resolves_python_imports_from_manifest_declared_src_root(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "pyproject.toml").write_text(
        '[tool.setuptools]\npackage-dir = {"" = "src"}\n\n[tool.setuptools.packages.find]\nwhere = ["src"]\n',
        encoding="utf-8",
    )
    (repo / "src/relayboard").mkdir(parents=True)
    (repo / "src/relayboard/__init__.py").write_text("", encoding="utf-8")
    (repo / "src/relayboard/retry_worker.py").write_text("class RetryWorker:\n    pass\n", encoding="utf-8")
    (repo / "tests").mkdir()
    (repo / "tests/test_retry_worker.py").write_text(
        "from relayboard.retry_worker import RetryWorker\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    importer_id = file_id("main", "tests/test_retry_worker.py")
    target_id = file_id("main", "src/relayboard/retry_worker.py")
    import_id = _from_import_id(
        "tests/test_retry_worker.py",
        "relayboard.retry_worker.RetryWorker",
        module="relayboard.retry_worker",
        imported_name="RetryWorker",
    )
    assert any(edge["kind"] == "RESOLVES_TO" and edge["from"] == import_id and edge["to"] == target_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == importer_id and edge["to"] == target_id for edge in snapshot["edges"])

    assert main(["graph", "query", "--file", "src/relayboard/retry_worker.py", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert any(
        relation["edge"] == "IMPORTS_FILE"
        and relation["from"].get("path") == "tests/test_retry_worker.py"
        and relation["to"].get("path") == "src/relayboard/retry_worker.py"
        for relation in result["relations"]
    )


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
    import_node_id = _from_import_id(
        "handlers/login.py",
        ".tokens.issue_token",
        module="tokens",
        imported_name="issue_token",
        level=1,
    )
    source_file_id = file_id("main", "handlers/login.py")
    target_file_id = file_id("main", "handlers/tokens.py")
    assert any(edge["kind"] == "RESOLVES_TO" and edge["from"] == import_node_id and edge["to"] == target_file_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == source_file_id and edge["to"] == target_file_id for edge in snapshot["edges"])


def test_graph_relative_import_occurrences_keep_importer_identity(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    for package in ("p1", "p2"):
        (repo / package).mkdir()
        (repo / package / "__init__.py").write_text("", encoding="utf-8")
        (repo / package / "target.py").write_text("def target():\n    return 1\n", encoding="utf-8")
        (repo / package / "use.py").write_text("from . import target\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    expected = {
        package: _from_import_id(
            f"{package}/use.py",
            ".target",
            module="",
            imported_name="target",
            level=1,
        )
        for package in ("p1", "p2")
    }
    assert len(set(expected.values())) == 2
    for package, import_node_id in expected.items():
        assert any(
            edge["kind"] == "DECLARES_IMPORT"
            and edge["from"] == file_id("main", f"{package}/use.py")
            and edge["to"] == import_node_id
            for edge in snapshot["edges"]
        )
        assert any(
            edge["kind"] == "RESOLVES_TO"
            and edge["from"] == import_node_id
            and edge["to"] == file_id("main", f"{package}/target.py")
            for edge in snapshot["edges"]
        )


def test_graph_python_imports_use_module_identity_and_package_attribute_precedence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "pkg").mkdir()
    (repo / "top_level.py").write_text("from .sibling import target\n", encoding="utf-8")
    (repo / "sibling.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    (repo / "pkg/__init__.py").write_text("def item():\n    return 'package attribute'\n", encoding="utf-8")
    (repo / "pkg/item.py").write_text("def item():\n    return 'submodule'\n", encoding="utf-8")
    (repo / "consumer.py").write_text("from pkg import item\n", encoding="utf-8")
    (repo / "wildpkg").mkdir()
    (repo / "wildpkg/__init__.py").write_text("from .impl_a import target\nfrom .impl_b import *\n", encoding="utf-8")
    (repo / "wildpkg/impl_a.py").write_text("def target():\n    return 'explicit attribute'\n", encoding="utf-8")
    (repo / "wildpkg/impl_b.py").write_text("__all__ = ['target']\n\ndef target():\n    return 'wildcard attribute'\n", encoding="utf-8")
    (repo / "wildpkg/target.py").write_text("def target():\n    return 'submodule'\n", encoding="utf-8")
    (repo / "wild_consumer.py").write_text("from wildpkg import target\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    invalid_relative = _from_import_id(
        "top_level.py",
        ".sibling.target",
        module="sibling",
        imported_name="target",
        level=1,
    )
    package_attribute = _from_import_id(
        "consumer.py",
        "pkg.item",
        module="pkg",
        imported_name="item",
    )
    wildcard_attribute = _from_import_id(
        "wild_consumer.py",
        "wildpkg.target",
        module="wildpkg",
        imported_name="target",
    )
    assert not any(edge["kind"] == "RESOLVES_TO" and edge["from"] == invalid_relative for edge in snapshot["edges"])
    assert any(
        edge["kind"] == "RESOLVES_TO"
        and edge["from"] == package_attribute
        and edge["to"] == file_id("main", "pkg/__init__.py")
        and edge["facts"]["match_kind"] == "attribute_exact"
        for edge in snapshot["edges"]
    )
    assert not any(
        edge["kind"] == "RESOLVES_TO"
        and edge["from"] == package_attribute
        and edge["to"] == file_id("main", "pkg/item.py")
        for edge in snapshot["edges"]
    )
    assert not any(edge["kind"] == "RESOLVES_TO" and edge["from"] == wildcard_attribute for edge in snapshot["edges"])


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
    import_node_id = _from_import_id(
        "consumer/app.py",
        "pkg.mod.VALUE",
        module="pkg.mod",
        imported_name="VALUE",
    )
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
    import_node_id = import_ref_id("main", "frontend/src/client.ts", "typescript", "./api/tokens")
    source_file_id = file_id("main", "frontend/src/client.ts")
    target_file_id = file_id("main", "frontend/src/api/tokens.ts")
    assert any(source["kind"] == "js_ts_relative_import_resolver" and source["assertion"] == "resolved" for source in snapshot["sources"])
    assert any(edge["kind"] == "RESOLVES_TO" and edge["from"] == import_node_id and edge["to"] == target_file_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == source_file_id and edge["to"] == target_file_id for edge in snapshot["edges"])


def test_graph_resolves_dart_relative_and_local_package_imports(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "lib/src").mkdir(parents=True)
    (repo / "pubspec.yaml").write_text("name: demo_app\n", encoding="utf-8")
    (repo / "lib/main.dart").write_text(
        "import './src/local.dart';\n"
        "import 'package:demo_app/src/session.dart';\n",
        encoding="utf-8",
    )
    (repo / "lib/src/local.dart").write_text("class Local {}\n", encoding="utf-8")
    (repo / "lib/src/session.dart").write_text("class Session {}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    source_file_id = file_id("main", "lib/main.dart")
    local_import_id = import_ref_id("main", "lib/main.dart", "dart", "./src/local.dart")
    package_import_id = import_ref_id("main", "lib/main.dart", "dart", "package:demo_app/src/session.dart")
    assert any(source["kind"] == "dart_import_resolver" and source["assertion"] == "resolved" for source in snapshot["sources"])
    assert any(edge["kind"] == "RESOLVES_TO" and edge["from"] == local_import_id and edge["to"] == file_id("main", "lib/src/local.dart") for edge in snapshot["edges"])
    assert any(edge["kind"] == "RESOLVES_TO" and edge["from"] == package_import_id and edge["to"] == file_id("main", "lib/src/session.dart") for edge in snapshot["edges"])
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == source_file_id and edge["to"] == file_id("main", "lib/src/session.dart") for edge in snapshot["edges"])


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
    import_node_id = import_ref_id("main", "frontend/src/client.ts", "typescript", "./api/tokens")
    assert any(edge["kind"] == "DECLARES_IMPORT" and edge["to"] == import_node_id for edge in snapshot["edges"])
    assert not any(edge["kind"] == "RESOLVES_TO" and edge["from"] == import_node_id for edge in snapshot["edges"])
    assert not any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == file_id("main", "frontend/src/client.ts") for edge in snapshot["edges"])

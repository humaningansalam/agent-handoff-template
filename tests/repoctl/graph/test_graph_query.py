from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_store import materialize_graph
from tools.repoctl.graph_model import digest_data, file_id, import_ref_id, topic_id
from tools.repoctl.repositories import require_repo_target
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo


def _materialize(root: Path) -> None:
    snapshot, problems, _meta = materialize_graph(root, target=require_repo_target(root, repo_id="main"))
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]


def test_graph_query_file_returns_typed_subgraph(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "src").mkdir()
    (repo / "src/app.py").write_text("import hashlib\n\n\ndef api_error():\n    return 'root'\n", encoding="utf-8")
    (repo / "repos/src").mkdir(parents=True)
    (repo / "repos/src/app.py").write_text("def nested_app():\n    return 'nested'\n", encoding="utf-8")
    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr(
        "tools.repoctl.graph_store.collect_graph_inputs",
        lambda *args, **kwargs: (_ for _ in ()).throw(AssertionError("query rescanned product sources")),
    )

    assert main(["graph", "query", "--file", "src/app.py", "--json"]) == 0

    compact_payload = json.loads(capsys.readouterr().out)
    compact_result = compact_payload["data"]["result"]
    assert "nodes" not in compact_result
    assert "edges" not in compact_result
    assert compact_result["node_count"] >= 1
    assert compact_result["edge_count"] >= 1
    assert any(relation["edge"] == "CONTAINS" for relation in compact_result["relations"])
    assert any(item["selector"] == {"kind": "file", "value": "src/app.py"} for item in compact_result["continuations"])

    assert main(["graph", "query", "--file", "repos/src/app.py", "--json"]) == 0
    nested = json.loads(capsys.readouterr().out)["data"]["result"]
    assert nested["query"] == {"type": "file", "path": "repos/src/app.py"}
    assert nested["matches"][0]["path"] == "repos/src/app.py"

    assert main(["graph", "query", "--file", "src/app.py", "--full", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert result["query"] == {"type": "file", "path": "src/app.py"}
    assert any(node["id"] == file_id("main", "src/app.py") for node in result["nodes"])
    assert any(edge["kind"] == "CONTAINS" and edge["to"] == file_id("main", "src/app.py") for edge in result["edges"])

    assert main(["graph", "query", "--file", "./src\\app.py", "--json"]) == 1
    invalid = json.loads(capsys.readouterr().out)
    assert invalid["problems"][0]["code"] == "graph_query_invalid_path"

    assert main(["graph", "query", "--symbol", "api_error", "--in-file", "../outside.py", "--json"]) == 1
    invalid_in_file = json.loads(capsys.readouterr().out)
    assert invalid_in_file["problems"][0]["code"] == "graph_query_invalid_path"

    provider_path = tmp_path / ".repoctl-state/graph/main/providers/python_ast.json"
    removed_provider_path = provider_path.with_suffix(".removed")
    provider_path.rename(removed_provider_path)
    assert main(["graph", "query", "--file", "src/app.py", "--json"]) == 1
    incomplete = json.loads(capsys.readouterr().out)
    assert incomplete["data"]["materialization"]["status"] == "incomplete"
    assert [problem["code"] for problem in incomplete["problems"]] == ["graph_materialization_incomplete"]

    removed_provider_path.rename(provider_path)
    provider_data = json.loads(provider_path.read_text(encoding="utf-8"))
    provider_data["symbols"] = None
    provider_path.write_text(json.dumps(provider_data, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    manifest_path = tmp_path / ".repoctl-state/graph/main/manifest.json"
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["provider_result_digests"]["python_ast"] = digest_data(provider_data)
    manifest_path.write_text(json.dumps(manifest, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["graph", "query", "--file", "src/app.py", "--json"]) == 1
    invalid_materialization = json.loads(capsys.readouterr().out)
    assert invalid_materialization["data"]["materialization"]["status"] == "invalid"
    assert [problem["code"] for problem in invalid_materialization["problems"]] == ["graph_materialization_invalid"]


def test_graph_query_topic_returns_matching_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    rel = "backend/auth/token_service.py"
    (repo / "backend/auth").mkdir(parents=True)
    (repo / rel).write_text("def issue():\n    return 'token'\n", encoding="utf-8")
    write_repometa(repo)
    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--topic", "auth", "--full", "--json"]) == 0

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
    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--import", "axios", "--full", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert result["query"] == {"type": "import", "raw_import": "axios"}
    assert any(node["id"] == import_ref_id("main", "frontend/app.ts", "typescript", "axios") for node in result["nodes"])
    assert any(node["id"] == file_id("main", "frontend/app.ts") for node in result["nodes"])
    assert any(edge["kind"] == "DECLARES_IMPORT" for edge in result["edges"])


def test_graph_query_requires_exactly_one_selector(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--json"]) == 1
    missing = json.loads(capsys.readouterr().out)
    assert missing["problems"][0]["code"] == "graph_query_selector_required"

    assert main(["graph", "query", "--file", "app.py", "--topic", "auth", "--json"]) == 1
    ambiguous = json.loads(capsys.readouterr().out)
    assert ambiguous["problems"][0]["code"] == "graph_query_selector_ambiguous"

    assert main(["graph", "query", "--file", "missing.py", "--json"]) == 0
    missing = json.loads(capsys.readouterr().out)
    assert missing["ok"] is True
    assert missing["data"]["query_status"] == "not_found"
    assert missing["data"]["completeness"]["status"] == "complete"


def test_graph_query_reports_snapshot_freshness(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    _materialize(tmp_path)
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--file", "app.py", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["freshness"]["status"] == "stale"
    assert payload["data"]["freshness"]["changed_path_count"] == 1
    assert "changed_paths" not in payload["data"]["freshness"]
    assert any(warning["code"] == "graph_snapshot_stale" for warning in payload["warnings"])

    assert main(["graph", "query", "--file", "app.py", "--full", "--json"]) == 0
    full = json.loads(capsys.readouterr().out)
    assert full["data"]["freshness"]["changed_paths"] == ["app.py"]


def test_graph_call_query_reports_defined_but_missing_provider_as_unavailable(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.ts").write_text("export function run() { return 1; }\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    monkeypatch.setattr("tools.repoctl.graph_typescript_provider._find_compiler", lambda repo, entries: (None, ""))
    monkeypatch.setattr("tools.repoctl.graph_typescript_provider._bundled_compiler", lambda root: (None, ""))
    _materialize(tmp_path)

    assert main(["graph", "query", "--callers-of", "run", "--in-file", "app.ts", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert payload["data"]["query_status"] == "unavailable"
    assert payload["data"]["result"]["query_status"] == "unavailable"
    assert payload["data"]["completeness"]["capabilities"]["calls"] == "unavailable"


def test_graph_shell_only_repo_reports_provider_coverage_as_unsupported(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "run.sh").write_text("#!/bin/sh\nrun_task() { echo ok; }\n", encoding="utf-8")
    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--symbol", "run_task", "--full", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    completeness = payload["data"]["completeness"]
    assert payload["data"]["query_status"] == "unsupported"
    assert completeness["capabilities"]["symbols"] == "unsupported"
    assert completeness["capabilities"]["calls"] == "unsupported"
    assert completeness["provider_coverage"]["symbols"]["unsupported_paths"] == ["run.sh"]

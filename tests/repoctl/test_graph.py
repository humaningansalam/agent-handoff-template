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
from tests.repoctl.test_check import add_task, task_text, write_workspace
from tests.repoctl.test_meta_check import write_repometa
from tests.repoctl.test_repositories import commit_all, init_repo, write_settings


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


def test_graph_imports_are_raw_import_refs(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "frontend/src").mkdir(parents=True)
    (repo / "frontend/src/app.ts").write_text("import axios from 'axios';\nexport const run = () => fetch('/');\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

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

    assert main(["graph", "build", "--json"]) == 0

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


def test_graph_build_consumes_task_completion_receipts(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    commit_all(repo)
    task_id = "T-20260609184046Z"
    task = task_text(task_id, status="todo").replace('area: ""', 'area: "repo"').replace('repo_id: ""', 'repo_id: "main"')
    add_task(tmp_path, f"{task_id}--alpha.md", task)
    (tmp_path / "docs/BOARD.md").write_text(f"# BOARD\n\n## Board\n\n- docs/tasks/{task_id}--alpha.md\n\n## Backlog\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()
    (repo / "app.py").write_text("def run():\n    return 2\n", encoding="utf-8")
    task_path = tmp_path / "docs/tasks" / f"{task_id}--alpha.md"
    task_text_value = task_path.read_text(encoding="utf-8")
    discovery = "## Discovery\n\n- Candidate query: `run`\n- Candidate files reviewed: `repos/app.py`\n- Chosen files: `repos/app.py`\n\n"
    task_path.write_text(task_text_value.replace("## Execution Log", discovery + "## Execution Log", 1), encoding="utf-8")
    verification = tmp_path / "verification.md"
    verification.write_text("- Command: pytest\n- Result: pass\n", encoding="utf-8")

    assert main(["task", "finish", task_id, "--verification-file", str(verification), "--json"]) == 0
    finish_payload = json.loads(capsys.readouterr().out)
    receipt = json.loads((tmp_path / finish_payload["completion_receipt"]).read_text(encoding="utf-8"))
    assert receipt["repo_id"] == "main"
    assert receipt["changed_entries"] == [{"change": "modified", "path": "app.py"}]

    assert main(["graph", "build", "--repo-id", "main", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert any(source["kind"] == "task_completion" and source["assertion"] == "recorded" for source in snapshot["sources"])
    task_node_id = f"task:{task_id}"
    assert any(node["id"] == task_node_id and node["kind"] == "task" for node in snapshot["nodes"])
    assert any(node["kind"] == "change_event" for node in snapshot["nodes"])
    assert any(node["kind"] == "artifact" for node in snapshot["nodes"])
    assert any(edge["kind"] == "TASK_RECORDED_CHANGE" and edge["from"] == task_node_id for edge in snapshot["edges"])
    assert any(edge["kind"] == "CHANGE_AFFECTED_FILE" and edge["to"] == file_id("main", "app.py") for edge in snapshot["edges"])
    assert any(edge["kind"] == "TASK_VERIFIED_BY" and edge["from"] == task_node_id for edge in snapshot["edges"])


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


def test_graph_receipt_edges_preserve_deleted_and_renamed_paths(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "new.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    archive_path = tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md"
    archive_text = task_text("T-20260609184046Z", status="done")
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(archive_text, encoding="utf-8")
    archive_hash = _sha256_text(archive_text)
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True)
    receipt = {
        "schema": "repoctl.task.completion",
        "schema_version": 1,
        "task_id": "T-20260609184046Z",
        "repo_id": "main",
        "status": "done",
        "completed_at": "2026-06-09T18:40:46Z",
        "task_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
        "archive_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
        "content_sha256": archive_hash,
        "changed_entries": [
            {"change": "deleted", "path": "deleted.py"},
            {"change": "renamed", "path": "new.py", "old_path": "old.py"},
        ],
        "verification": {
            "task_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
            "archive_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
            "content_sha256": archive_hash,
        },
    }
    (receipt_dir / "T-20260609184046Z.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    assert any(node["id"] == file_id("main", "deleted.py") and node["facts"]["receipt"]["present_in_current_inventory"] is False for node in snapshot["nodes"])
    assert any(node["id"] == file_id("main", "old.py") and node["facts"]["receipt"]["present_in_current_inventory"] is False for node in snapshot["nodes"])
    assert any(edge["kind"] == "CHANGE_AFFECTED_FILE" and edge["to"] == file_id("main", "deleted.py") and edge["facts"]["role"] == "path" for edge in snapshot["edges"])
    assert any(edge["kind"] == "CHANGE_AFFECTED_FILE" and edge["to"] == file_id("main", "old.py") and edge["facts"]["role"] == "old_path" for edge in snapshot["edges"])


def test_graph_ignores_invalid_receipt_for_other_repo_but_rejects_selected_repo(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    web = tmp_path / "repos/web"
    api = tmp_path / "repos/api"
    init_repo(web)
    init_repo(api)
    write_repometa(web)
    write_repometa(api)
    (web / "app.py").write_text("def web():\n    return 1\n", encoding="utf-8")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True)
    (receipt_dir / "T-20260609184046Z.json").write_text(json.dumps({"schema": "future", "schema_version": 99, "repo_id": "api"}) + "\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--repo-id", "web", "--json"]) == 0
    capsys.readouterr()

    (receipt_dir / "T-20260609184047Z.json").write_text(
        json.dumps({"schema": "repoctl.task.completion", "schema_version": 1, "repo_id": "web", "task_id": "BAD", "status": "banana"}) + "\n",
        encoding="utf-8",
    )
    assert main(["graph", "build", "--repo-id", "web", "--json"]) == 2
    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_completion_receipt"


def test_graph_rejects_receipt_with_fake_hash(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    archive_path = tmp_path / "docs/archive/tasks/T-20260609184046Z--alpha.md"
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(task_text("T-20260609184046Z", status="done"), encoding="utf-8")
    receipt_dir = tmp_path / "docs/tasks/.repoctl-state/completions"
    receipt_dir.mkdir(parents=True)
    receipt = {
        "schema": "repoctl.task.completion",
        "schema_version": 1,
        "task_id": "T-20260609184046Z",
        "repo_id": "main",
        "status": "done",
        "completed_at": "2026-06-09T18:40:46Z",
        "task_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
        "archive_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
        "content_sha256": "sha256:" + "a" * 64,
        "changed_entries": [{"change": "modified", "path": "app.py"}],
        "verification": {
            "task_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
            "archive_path": "docs/archive/tasks/T-20260609184046Z--alpha.md",
            "content_sha256": "sha256:" + "a" * 64,
        },
    }
    (receipt_dir / "T-20260609184046Z.json").write_text(json.dumps(receipt, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "invalid_completion_receipt"

from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import init_repo, write_repometa


def test_index_code_extracts_python_facts_without_writing_annotations(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    rel = "backend/auth/token_service.py"
    (repo / "backend/auth").mkdir(parents=True)
    (repo / rel).write_text(
        "import hashlib\n"
        "from pathlib import Path\n\n"
        "class TokenService:\n"
        "    def issue(self, value):\n"
        "        Path('token.txt').write_text(hashlib.sha256(value).hexdigest())\n",
        encoding="utf-8",
    )
    before = (repo / ".repometa/annotations" / "0.json").read_text(encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["index", "code", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "index code"
    assert payload["data"]["authoritative"] is False
    entry = next(file for file in payload["data"]["files"] if file["path"] == rel)
    assert entry["language"] == "python"
    assert entry["symbols"] == ["TokenService", "issue"]
    assert entry["imports"] == ["hashlib", "pathlib.Path"]
    assert "hashlib.sha256" in entry["calls"]
    assert entry["deps"] == ["hashlib", "pathlib"]
    assert {"crypto", "fs"} <= set(entry["observed_effects"])
    assert (repo / ".repometa/annotations" / "0.json").read_text(encoding="utf-8") == before


def test_index_code_extracts_typescript_facts(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    rel = "frontend/src/api/billingGateway.ts"
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / rel).write_text(
        "import axios from 'axios';\n"
        "export class BillingClient {}\n"
        "export const charge = () => fetch('/charge');\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["index", "code", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    entry = next(file for file in payload["data"]["files"] if file["path"] == rel)
    assert entry["language"] == "typescript"
    assert entry["symbols"] == ["BillingClient", "charge"]
    assert entry["imports"] == ["axios"]
    assert "fetch" in entry["calls"]
    assert entry["deps"] == ["axios"]
    assert "net" in entry["observed_effects"]


def test_index_code_changed_requires_repo_git(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    write_repometa(repo)
    (repo / "app.py").write_text("def run():\n    return 1\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["index", "code", "--changed", "--json"]) == 2

    payload = json.loads(capsys.readouterr().out)
    assert payload["ok"] is False
    assert any(problem["code"] == "repository_identity_unbound" for problem in payload["problems"])


def test_index_code_reports_truncation_separately_from_total(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    repo.mkdir()
    init_repo(repo)
    write_repometa(repo)
    for index in range(3):
        (repo / f"mod{index}.py").write_text(f"def run_{index}():\n    return {index}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["index", "code", "--limit", "2", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    summary = payload["data"]["summary"]
    assert summary["total"] == 3
    assert summary["returned"] == 2
    assert summary["truncated"] is True
    assert summary["dropped_count"] == 1
    assert any(warning["code"] == "index_truncated" for warning in payload["warnings"])

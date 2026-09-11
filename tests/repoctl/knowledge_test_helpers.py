from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.workspace.test_check import (
    add_task as add_task,
    task_text as task_text,
    write_workspace as write_workspace,
)
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo



def _write_knowledge_docs(root: Path) -> None:
    (root / "docs/adr").mkdir(parents=True, exist_ok=True)
    (root / "docs/contracts").mkdir(parents=True, exist_ok=True)
    (root / ".repoctl-state/knowledge").mkdir(parents=True, exist_ok=True)
    (root / "docs/contracts/repoctl-context-contract.md").write_text(
        "# repoctl Context contract\n\n## Decision\n\nContext returns source bundles but does not create authoritative knowledge.\n\n## Authority Rules\n\nSaved Knowledge requires an explicit claim, reason, and source.\n",
        encoding="utf-8",
    )
    (root / ".repoctl-state/knowledge/private-plan.md").write_text("# Private Plan\n\nDo not ingest this.\n", encoding="utf-8")


def _setup_knowledge_workspace(root: Path, monkeypatch) -> Path:
    write_workspace(root)
    _write_knowledge_docs(root)
    repo = root / "repos"
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)
    return repo


def _setup_knowledge_multirepo_workspace(root: Path, monkeypatch) -> None:
    write_workspace(root)
    _write_knowledge_docs(root)
    init_repo(root / "repos/web")
    init_repo(root / "repos/api")
    write_repometa(root / "repos/web")
    write_repometa(root / "repos/api")
    (root / "docs/repoctl.json").write_text(
        json.dumps({"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]}),
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)


def _add_knowledge_source(
    capsys,
    *,
    source: str = "docs/contracts/repoctl-context-contract.md",
    repo_id: str = "main",
    claim: str = "Context returns source bundles but does not create authoritative knowledge.",
    kind: str = "decision",
    applies_to: list[str] | None = None,
    replaces: list[str] | None = None,
) -> dict:
    args = [
        "knowledge",
        "add",
        "--source",
        source,
        "--repo-id",
        repo_id,
        "--kind",
        kind,
        "--claim",
        claim,
        "--reason",
        "This reusable rule explains later work.",
    ]
    for path in applies_to or []:
        args.extend(["--applies-to", path])
    for record_id in replaces or []:
        args.extend(["--replaces", record_id])
    args.append("--json")
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    return payload

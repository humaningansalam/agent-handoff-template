from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.workspace.test_check import add_task, task_text, write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo



def _write_knowledge_docs(root: Path) -> None:
    (root / "docs/adr").mkdir(parents=True, exist_ok=True)
    (root / ".repoctl-state/knowledge").mkdir(parents=True, exist_ok=True)
    (root / "docs/adr/evidence-context-authority-v0.md").write_text(
        "# Evidence Context Authority\n\n## Decision\n\nContext returns source bundles but does not create authoritative knowledge.\n\n## Authority Rules\n\nReviewed knowledge requires explicit human approval.\n",
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


def _read_event(root: Path, event_id: str) -> dict:
    return json.loads((root / "docs/knowledge/events" / f"{event_id}.json").read_text(encoding="utf-8"))


def _write_event(root: Path, event: dict) -> None:
    (root / "docs/knowledge/events" / f"{event['id']}.json").write_text(
        json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        encoding="utf-8",
    )


def _approve_knowledge_source(
    capsys,
    *,
    source: str = "docs/adr/evidence-context-authority-v0.md",
    repo_id: str = "main",
    build_args: list[str] | None = None,
    approve_args: list[str] | None = None,
) -> dict:
    build = ["knowledge", "candidate", "build", "--source", source, "--repo-id", repo_id, "--json"]
    if build_args:
        build[-1:-1] = build_args
    assert main(build) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    args = ["knowledge", "approve", candidate_id, "--repo-id", repo_id, "--json"]
    if approve_args:
        args[-1:-1] = approve_args
    assert main(args) == 0
    payload = json.loads(capsys.readouterr().out)
    payload["candidate_id"] = candidate_id
    return payload

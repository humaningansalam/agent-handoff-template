from __future__ import annotations

import hashlib
import json
from pathlib import Path
from datetime import datetime, timedelta

from tools.repoctl.completion_catalogue import rebuild_completion_catalogue
from tools.repoctl.knowledge_projection import initialize_empty_knowledge_projection
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo, write_settings



def _write_context_docs(root: Path) -> None:
    (root / "docs/adr").mkdir(parents=True, exist_ok=True)
    (root / "docs/contracts").mkdir(parents=True, exist_ok=True)
    (root / "docs/workflows").mkdir(parents=True, exist_ok=True)
    (root / "docs/PRD.md").write_text(
        "# PRD\n\n## Graph and Knowledge\n\nGraph retrieves source-bound implementation evidence; Knowledge stores reviewed claims and their reasons.\n",
        encoding="utf-8",
    )
    (root / "docs/contracts/repoctl-graph-contract.md").write_text(
        "# repoctl Graph contract\n\nGraph is a read-only derived evidence snapshot. Source authorities remain repo registry, code index, repometa, and task completion receipts.\n",
        encoding="utf-8",
    )
    (root / "docs/contracts/repoctl-evidence-contract.md").write_text(
        "# repoctl Graph evidence contract\n\nGraph evidence retrieval returns read-only, source-linked results. It does not replace original code, Task scope, Board or repometa authority.\n",
        encoding="utf-8",
    )
    (root / "docs/contracts/repoctl-module-boundaries.md").write_text(
        "# repoctl module boundaries\n\n## Derived layer rules\n\nGraph and Knowledge are derived navigation and reusable judgment, not Task or product repository authority.\n",
        encoding="utf-8",
    )
    (root / "docs/workflows/generated.md").write_text("# Workflow\n\nGenerated output is not an authority.\n", encoding="utf-8")


def _write_context_benchmark_docs(root: Path) -> None:
    """Benchmark the shipped contracts instead of obsolete synthetic prose."""
    source_root = Path(__file__).resolve().parents[2]
    for rel in ("docs/PRD.md", "docs/contracts/repoctl-evidence-contract.md", "docs/contracts/repoctl-graph-contract.md", "docs/contracts/repoctl-module-boundaries.md"):
        target = root / rel
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text((source_root / rel).read_text(encoding="utf-8"), encoding="utf-8")


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_completion_receipt(
    root: Path,
    *,
    task_id: str = "T-20260625010101Z",
    repo_id: str = "main",
    changed_paths: list[str] | None = None,
    completed_at: str = "20260625T010101Z",
) -> None:
    changed_paths = ["auth.py"] if changed_paths is None else changed_paths
    archive_rel = f"docs/archive/tasks/{task_id}--knowledge-receipt.md"
    archive_text = f"""---
id: {task_id}
title: "Token validation invariant"
status: done
owner: "codex"
repo_ref: ""
repo_id: "{repo_id}"
created: 20260625T010101Z
area: "repo"
parent: ""
depends_on: []
---

# {task_id} - Token validation invariant

## Goal

Keep token validation centralized.

## Verification

`uv run pytest tests/test_auth.py` passed.
"""
    archive_path = root / archive_rel
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    archive_path.write_text(archive_text, encoding="utf-8")
    archive_hash = _sha256_text(archive_text)
    # Synthetic test-only lifecycle and path facts, never claims about real historical tasks.
    completion_event = datetime.strptime(completed_at, "%Y%m%dT%H%M%SZ")
    start_event = completion_event - timedelta(seconds=1)
    receipt = {
        "schema": "repoctl.task.completion",
        "schema_version": 5,
        "repo_id": repo_id,
        "task_id": task_id,
        "status": "done",
        "completed_at": completed_at,
        "started_at": start_event.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "completed_event_at": completion_event.strftime("%Y-%m-%dT%H:%M:%S.%fZ"),
        "task_path_at_completion": archive_rel,
        "content_sha256": archive_hash,
        "changed_entries": [{"change": "modified", "path": path} for path in changed_paths],
        "repo_evidence": {
            "mode": "working_tree_diff",
            "attribution": "task_working_tree",
            "start_head": "",
            "observed_head": "",
            "git_available": True,
            "diff_fingerprint_sha256": "sha256:" + ("0" * 64),
            "fingerprint_manifest": {},
            "ownership": {},
            "meta_gate": {},
            "delta": {"changed_count": len(changed_paths)},
            "path_transitions": [
                {
                    "path": path,
                    "effect": "write",
                    "basis": ["observed_change"],
                    "before": {"kind": "file", "blob_oid": "a" * 40, "executable": False},
                    "after": {"kind": "file", "blob_oid": "b" * 40, "executable": False},
                }
                for path in changed_paths
            ],
        },
    }
    receipt_path = root / "docs/tasks/.repoctl-state/completions" / f"{task_id}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _rebuild_completion_history(root: Path, repo_id: str = "main") -> None:
    rebuild_completion_catalogue(root, repo_id)


def _write_graph_evidence_task(
    root: Path, *, task_id: str, slug: str, title: str, goal: str,
    context_doc: str,
) -> None:
    """Provide a real Task source for Graph search; no Pack is created."""
    (root / "docs/tasks" / f"{task_id}--{slug}.md").write_text(
        f"""---
id: {task_id}
title: "{title}"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: {task_id[2:10]}T{task_id[10:16]}Z
area: "repo"
parent: ""
depends_on: []
---

# {task_id} - {title}

## Context Docs

- `{context_doc}`

## Discovery

- Chosen files: `repos/app.py`
- Notes: none yet

## Goal

{goal}

## Handoff

- Next exact step: inspect current Graph source evidence.
- First file to open: `{context_doc}`
- First command to run: ./scripts/repoctl graph search 'implementation and tests' --repo-id main --json
- Done when: required Graph evidence is visible.
""",
        encoding="utf-8",
    )


def _setup_context_workspace(root: Path, monkeypatch) -> Path:
    write_workspace(root)
    _write_context_docs(root)
    repo = root / "repos"
    init_repo(repo)
    write_repometa(repo)
    projection, projection_problems = initialize_empty_knowledge_projection(
        root,
        repo_id="main",
    )
    assert projection
    assert not projection_problems
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)
    return repo


def _setup_context_multirepo_workspace(root: Path, monkeypatch) -> None:
    write_workspace(root)
    _write_context_docs(root)
    init_repo(root / "repos/web")
    init_repo(root / "repos/api")
    write_repometa(root / "repos/web")
    write_repometa(root / "repos/api")
    write_settings(root, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    for repo_id in ("web", "api"):
        projection, projection_problems = initialize_empty_knowledge_projection(
            root,
            repo_id=repo_id,
        )
        assert projection
        assert not projection_problems
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)


def _write_context_benchmark_collection_corpus(root: Path, fixture: Path) -> None:
    corpus = json.loads((fixture / "corpus.json").read_text(encoding="utf-8"))
    for repo_id, repo_corpus in corpus["repositories"].items():
        for item in repo_corpus["files"]:
            path = root / "repos" / repo_id / item["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["content"], encoding="utf-8")

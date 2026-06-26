from __future__ import annotations

import hashlib
import json
from pathlib import Path

from tools.repoctl.cli import main
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo, write_settings



def _write_context_docs(root: Path) -> None:
    (root / "docs/adr").mkdir(parents=True, exist_ok=True)
    (root / "docs/contracts").mkdir(parents=True, exist_ok=True)
    (root / "docs/workflows").mkdir(parents=True, exist_ok=True)
    (root / "docs/adr/repoctl-graph-v0.md").write_text(
        "# ADR: repoctl Graph v0\n\n## Decision\n\nGraph is a read-only derived evidence snapshot. Source authorities remain repo registry, code index, .repometa, and task completion receipts.\n",
        encoding="utf-8",
    )
    (root / "docs/adr/evidence-context-authority-v0.md").write_text(
        "# ADR: Evidence Context Authority v0\n\n## Decision\n\nEvidence Context comes before reviewed knowledge and keeps source bundles separate.\n\n## Authority Rules\n\nEvidence Context is read-only and non-authoritative. Context retrieval does not replace Graph, task, Board, Backlog, or .repometa authority.\n",
        encoding="utf-8",
    )
    (root / "docs/contracts/repoctl-module-boundaries.md").write_text(
        "# repoctl module boundaries\n\n## Future layer rules\n\nContext must not replace task, Board, Backlog, Graph, or .repometa authority.\n",
        encoding="utf-8",
    )
    (root / "docs/workflows/generated.md").write_text("# Workflow\n\nGenerated output is not an authority.\n", encoding="utf-8")


def _sha256_text(text: str) -> str:
    return "sha256:" + hashlib.sha256(text.encode("utf-8")).hexdigest()


def _write_completion_receipt(root: Path, *, task_id: str = "T-20260625010101Z", repo_id: str = "main") -> None:
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
    receipt = {
        "schema": "repoctl.task.completion",
        "schema_version": 1,
        "repo_id": repo_id,
        "task_id": task_id,
        "status": "done",
        "task_path": f"docs/tasks/{task_id}--knowledge-receipt.md",
        "archive_path": archive_rel,
        "content_sha256": archive_hash,
        "changed_entries": [{"change": "modified", "path": "auth.py"}],
        "verification": {
            "task_path": f"docs/tasks/{task_id}--knowledge-receipt.md",
            "archive_path": archive_rel,
            "content_sha256": archive_hash,
        },
    }
    receipt_path = root / "docs/tasks/.repoctl-state/completions" / f"{task_id}.json"
    receipt_path.parent.mkdir(parents=True, exist_ok=True)
    receipt_path.write_text(json.dumps(receipt, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def _write_context_pack_task(
    root: Path,
    *,
    task_id: str,
    slug: str,
    title: str,
    query: str,
    goal: str,
    context_doc: str = "docs/adr/evidence-context-authority-v0.md",
    reviewed: str = "repos/app.py",
    chosen: str = "repos/app.py",
    first_command: str | None = None,
) -> None:
    first_command = first_command or f"./scripts/repoctl context pack --task {task_id} --repo-id main --json"
    task_path = root / "docs/tasks" / f"{task_id}--{slug}.md"
    task_path.write_text(
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

- Candidate query: {query}
- Candidate files reviewed: `{reviewed}`
- Chosen files: `{chosen}`

## Goal

{goal}

## Handoff

- Next exact step: inspect context pack evidence.
- First file to open: `{context_doc}`
- First command to run: `{first_command}`
- Done when: required context evidence is visible.
""",
        encoding="utf-8",
    )




def _setup_context_workspace(root: Path, monkeypatch) -> Path:
    write_workspace(root)
    _write_context_docs(root)
    repo = root / "repos"
    init_repo(repo)
    write_repometa(repo)
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
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)


def _write_context_benchmark_corpus(root: Path, fixture: Path | None = None) -> None:
    fixture = fixture or Path("tests/fixtures/context-benchmark")
    corpus = json.loads((fixture / "corpus.json").read_text(encoding="utf-8"))
    repo = root / "repos"
    main = corpus["repositories"]["main"]
    for item in main["files"]:
        path = repo / item["path"]
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(item["content"], encoding="utf-8")


def _write_context_benchmark_collection_corpus(root: Path, fixture: Path) -> None:
    corpus = json.loads((fixture / "corpus.json").read_text(encoding="utf-8"))
    for repo_id, repo_corpus in corpus["repositories"].items():
        for item in repo_corpus["files"]:
            path = root / "repos" / repo_id / item["path"]
            path.parent.mkdir(parents=True, exist_ok=True)
            path.write_text(item["content"], encoding="utf-8")


def _approve_superseded_context_knowledge(capsys) -> tuple[str, str]:
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    first_candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", first_candidate_id, "--repo-id", "main", "--json"]) == 0
    old_record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    replacement_candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", replacement_candidate_id, "--repo-id", "main", "--supersedes", old_record_id, "--json"]) == 0
    new_record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    return old_record_id, new_record_id


def _approve_deprecated_context_knowledge(tmp_path: Path, capsys) -> str:
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    reason = tmp_path / "deprecated-reason.md"
    reason.write_text("Decision is no longer current but remains historical evidence.\n", encoding="utf-8")
    assert main(["knowledge", "deprecate", record_id, "--repo-id", "main", "--reason-file", reason.as_posix(), "--json"]) == 0
    capsys.readouterr()
    return record_id


def _write_pack_benchmark_task(root: Path) -> None:
    task_path = root / "docs/tasks/T-20260624020202Z--pack-benchmark.md"
    task_path.write_text(
        """---
id: T-20260624020202Z
title: "Benchmark context pack must read recall"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260624T020202Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260624020202Z - Benchmark context pack must read recall

## Context Docs

- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: evidence context authority
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Use evidence context authority for task startup.

## Handoff

- Next exact step: inspect evidence context authority.
- First file to open: `docs/adr/evidence-context-authority-v0.md`
- First command to run: `./scripts/repoctl context pack --task T-20260624020202Z --repo-id main --json`
- Done when: mandatory source refs are packed.
""",
        encoding="utf-8",
    )
    contract_task_path = root / "docs/tasks/T-20260624030303Z--pack-benchmark-contract.md"
    contract_task_path.write_text(
        """---
id: T-20260624030303Z
title: "Benchmark context pack contract recall"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260624T030303Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260624030303Z - Benchmark context pack contract recall

## Context Docs

- `docs/contracts/repoctl-module-boundaries.md`

## Discovery

- Candidate query: context authority boundaries
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Preserve repoctl module boundaries while preparing context packs.

## Handoff

- Next exact step: inspect module boundary contract.
- First file to open: `docs/contracts/repoctl-module-boundaries.md`
- First command to run: `./scripts/repoctl context pack --task T-20260624030303Z --repo-id main --json`
- Done when: contract source refs are packed.
""",
        encoding="utf-8",
    )
    graph_task_path = root / "docs/tasks/T-20260624040404Z--pack-benchmark-graph.md"
    graph_task_path.write_text(
        """---
id: T-20260624040404Z
title: "Benchmark Graph authority startup context"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260624T040404Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260624040404Z - Benchmark Graph authority startup context

## Context Docs

- `docs/adr/repoctl-graph-v0.md`

## Discovery

- Candidate query: graph derived evidence snapshot
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Preserve Graph as a read-only derived evidence snapshot.

## Handoff

- Next exact step: inspect Graph authority decision.
- First file to open: `docs/adr/repoctl-graph-v0.md`
- First command to run: `./scripts/repoctl context pack --task T-20260624040404Z --repo-id main --json`
- Done when: Graph authority source refs are packed.
""",
        encoding="utf-8",
    )
    future_layer_task_path = root / "docs/tasks/T-20260624050505Z--pack-benchmark-future-layer.md"
    future_layer_task_path.write_text(
        """---
id: T-20260624050505Z
title: "Benchmark future layer boundaries"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260624T050505Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260624050505Z - Benchmark future layer boundaries

## Context Docs

- `docs/contracts/repoctl-module-boundaries.md`
- `docs/adr/evidence-context-authority-v0.md`

## Discovery

- Candidate query: future layer rules context authority
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Keep context and knowledge from replacing source authorities.

## Handoff

- Next exact step: inspect future layer rules.
- First file to open: `docs/contracts/repoctl-module-boundaries.md`
- First command to run: `./scripts/repoctl context pack --task T-20260624050505Z --repo-id main --json`
- Done when: future layer boundary refs are packed.
""",
        encoding="utf-8",
    )
    workspace_contract_task_path = root / "docs/tasks/T-20260624060606Z--pack-benchmark-workspace-contract.md"
    workspace_contract_task_path.write_text(
        """---
id: T-20260624060606Z
title: "Benchmark workspace contract startup context"
status: doing
owner: "codex"
repo_ref: ""
repo_id: "main"
created: 20260624T060606Z
area: "repo"
parent: ""
depends_on: []
---

# T-20260624060606Z - Benchmark workspace contract startup context

## Context Docs

- `AGENTS.md`

## Discovery

- Candidate query: workspace contract selected product repo boundary
- Candidate files reviewed: `repos/app.py`
- Chosen files: `repos/app.py`

## Goal

Follow workspace contract and selected product repository boundary.

## Handoff

- Next exact step: inspect workspace contract.
- First file to open: `AGENTS.md`
- First command to run: `./scripts/repoctl context pack --task T-20260624060606Z --repo-id main --json`
- Done when: workspace contract source refs are packed.
""",
        encoding="utf-8",
    )

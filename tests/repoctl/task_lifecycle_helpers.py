from __future__ import annotations

import json
import subprocess
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.meta import shard_for_path
from tests.repoctl.workspace.test_check import add_task, init_repo, task_text, write_workspace




def add_board_task(root: Path, name: str, text: str) -> Path:
    path = add_task(root, name, text)
    (root / "docs/BOARD.md").write_text(
        f"# BOARD\n\n## Board\n\n- docs/tasks/{name}\n\n## Backlog\n",
        encoding="utf-8",
    )
    return path


def write_json(path: Path, data: dict) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(data, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def write_repometa(repo: Path, *, coverage: list[str] | None = None, annotations: dict[str, dict] | None = None) -> None:
    policy = {
        "schema_version": 1,
        "indexing": {"exclude": [".git/**", ".repometa/**", "__pycache__/**", "**/__pycache__/**"]},
        "vocab": {
            "roles": {"base": ["service", "adapter", "config", "test", "workflow"], "extend": []},
            "declared_effects": {"base": ["none", "db", "net", "fs", "ui", "time", "crypto", "config"], "extend": []},
        },
        "defaults": {"areas": {}, "topics": {}},
        "coverage": {"require_annotations": coverage or []},
    }
    write_json(repo / ".repometa/policy.json", policy)
    for shard in "0123456789abcdef":
        write_json(repo / ".repometa/annotations" / f"{shard}.json", {"schema_version": 1, "annotations": {}, "exclusions": {}})
    for rel, annotation in (annotations or {}).items():
        shard = shard_for_path(rel)
        path = repo / ".repometa/annotations" / f"{shard}.json"
        data = json.loads(path.read_text(encoding="utf-8"))
        data["annotations"][rel] = annotation
        write_json(path, data)


def init_product_repo(repo: Path, *, coverage: list[str] | None = None, annotations: dict[str, dict] | None = None) -> None:
    repo.mkdir()
    subprocess.run(["git", "init"], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "config", "user.email", "a@example.com"], cwd=repo, check=True)
    subprocess.run(["git", "config", "user.name", "A"], cwd=repo, check=True)
    write_repometa(repo, coverage=coverage, annotations=annotations)


def init_committed_product_repo(
    repo: Path,
    files: dict[str, str] | None = None,
    *,
    coverage: list[str] | None = None,
    annotations: dict[str, dict] | None = None,
) -> None:
    init_product_repo(repo, coverage=coverage, annotations=annotations)
    for rel_path, text in (files or {}).items():
        path = repo / rel_path
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
    commit_all(repo)


def commit_all(repo: Path, message: str = "base") -> None:
    subprocess.run(["git", "add", "."], cwd=repo, check=True, stdout=subprocess.DEVNULL)
    subprocess.run(["git", "commit", "-m", message], cwd=repo, check=True, stdout=subprocess.DEVNULL)


def write_verification(root: Path, text: str = "ok\n") -> Path:
    verification = root / "verification.md"
    verification.write_text(text, encoding="utf-8")
    return verification


def start_task_for_finish(monkeypatch, capsys, root: Path, task_id: str = "T-20260609184046Z") -> None:
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: root)
    assert main(["task", "start", task_id, "--json"]) == 0
    capsys.readouterr()


def record_discovery(root: Path, task_id: str, *, query: str, reviewed: str, chosen: str) -> None:
    task_path = next((root / "docs/tasks").glob(f"{task_id}--*.md"))
    text = task_path.read_text(encoding="utf-8")
    discovery = f"## Discovery\n\n- Candidate query: `{query}`\n- Candidate files reviewed: `{reviewed}`\n- Chosen files: `{chosen}`\n\n"
    if "## Discovery" in text:
        start = text.index("## Discovery")
        end = text.index("## Execution Log")
        text = text[:start] + discovery + text[end:]
    else:
        text = text.replace("## Execution Log", discovery + "## Execution Log", 1)
    task_path.write_text(text, encoding="utf-8")

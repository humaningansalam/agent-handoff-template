from __future__ import annotations

from pathlib import Path
from typing import Any

from .context import build_context_bundle
from .context_model import ContextCandidate
from .markdown import find_section
from .repositories import RepoTarget
from .tasks import Problem, Task, resolve_task


def build_task_context_pack(root: Path, *, target: RepoTarget, task_id: str, budget_tokens: int = 5000) -> tuple[dict[str, Any], list[Problem], dict[str, Any]]:
    task = resolve_task(root, task_id)
    query = _task_seed_query(task)
    bundle, problems, meta = build_context_bundle(root, target=target, query=query, budget_tokens=budget_tokens)
    groups = _group_candidates(bundle.packed_context if bundle is not None else [])
    data = {
        "schema": "repoctl.context.task_pack",
        "schema_version": 1,
        "authoritative": False,
        "task": {
            "id": task.id,
            "path": task.rel_path,
            "status": task.status,
            "repo_id": str(task.frontmatter.get("repo_id") or ""),
            "area": str(task.frontmatter.get("area") or ""),
        },
        "seed": {
            "source": "task_fields_for_retrieval_only",
            "query": query,
            "used_sections": _used_sections(task),
        },
        "groups": groups,
        "bundle": bundle.to_dict() if bundle is not None else None,
        "warnings": _pack_warnings(bundle, task),
    }
    return data, problems, meta


def _task_seed_query(task: Task) -> str:
    parts = [
        str(task.frontmatter.get("title") or ""),
        str(task.frontmatter.get("area") or ""),
        _section(task, "Context Docs"),
        _section(task, "Goal"),
        _section(task, "Discovery"),
        _section(task, "Handoff"),
    ]
    return "\n".join(part.strip() for part in parts if part.strip())


def _used_sections(task: Task) -> list[str]:
    return [name for name in ("Context Docs", "Goal", "Discovery", "Handoff") if _section(task, name).strip()]


def _section(task: Task, heading: str) -> str:
    try:
        section = find_section(task.body, heading)
    except Exception:
        return ""
    return task.body[section.body_start : section.end].strip()


def _group_candidates(candidates: list[ContextCandidate]) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {"must_read": [], "maybe_relevant": [], "verification_hints": []}
    for candidate in candidates:
        ref = candidate.source_ref
        item = candidate.to_dict()
        if ref.kind in {"completion_receipt", "task_artifact"} or "Verification" in ref.section:
            groups["verification_hints"].append(item)
        elif ref.path == "AGENTS.md" or ref.path.startswith("docs/contracts/") or ref.path.startswith("docs/adr/"):
            groups["must_read"].append(item)
        else:
            groups["maybe_relevant"].append(item)
    return groups


def _pack_warnings(bundle: Any, task: Task) -> list[dict[str, str]]:
    warnings = [
        {
            "code": "context_pack_not_authoritative",
            "message": "task context pack uses task text only as retrieval seed; it does not set task scope or create knowledge",
        }
    ]
    task_repo_id = str(task.frontmatter.get("repo_id") or "")
    if task_repo_id and bundle is not None and str(bundle.repository.get("id") or "") != task_repo_id:
        warnings.append(
            {
                "code": "context_pack_repo_mismatch",
                "message": f"task repo_id is {task_repo_id}, but context pack used {bundle.repository.get('id')}",
            }
        )
    return warnings

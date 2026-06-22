from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .context import build_context_bundle
from .context_model import ContextCandidate
from .graph_model import digest_data
from .markdown import find_section
from .repositories import RepoTarget
from .tasks import Problem, Task, resolve_task


def build_task_context_pack(root: Path, *, target: RepoTarget, task_id: str, budget_tokens: int = 5000, explain: bool = False) -> tuple[dict[str, Any], list[Problem], dict[str, Any]]:
    task = resolve_task(root, task_id)
    query = _task_seed_query(task)
    bundle, problems, meta = build_context_bundle(root, target=target, query=query, budget_tokens=budget_tokens, explain=explain)
    groups = _group_candidates(bundle.packed_context if bundle is not None else [])
    groups["reviewed_knowledge"] = bundle.knowledge_results if bundle is not None else []
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
    data["pack_digest"] = digest_data(data)
    return data, problems, meta


def compare_task_context_packs(
    *,
    baseline_path: Path,
    candidate_path: Path,
    max_must_read_drop: int | None = None,
    max_reviewed_knowledge_drop: int | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    baseline = _read_pack_artifact(baseline_path, problems, label="baseline")
    candidate = _read_pack_artifact(candidate_path, problems, label="candidate")
    if not baseline or not candidate:
        return {}, problems
    count_deltas = {
        "must_read": _group_count_delta(baseline, candidate, "must_read"),
        "maybe_relevant": _group_count_delta(baseline, candidate, "maybe_relevant"),
        "verification_hints": _group_count_delta(baseline, candidate, "verification_hints"),
        "reviewed_knowledge": _group_count_delta(baseline, candidate, "reviewed_knowledge"),
    }
    missing_refs = _missing_group_refs(baseline, candidate, "must_read")
    regressions = _pack_regressions(count_deltas, missing_refs, max_must_read_drop=max_must_read_drop, max_reviewed_knowledge_drop=max_reviewed_knowledge_drop)
    problems.extend(regressions)
    return {
        "schema": "repoctl.context.task_pack.compare",
        "schema_version": 1,
        "baseline": _pack_identity(baseline_path, baseline),
        "candidate": _pack_identity(candidate_path, candidate),
        "count_deltas": count_deltas,
        "missing_must_read_refs": missing_refs,
        "regressions": [problem.to_dict() for problem in regressions],
        "gates": {
            "max_must_read_drop": max_must_read_drop,
            "max_reviewed_knowledge_drop": max_reviewed_knowledge_drop,
        },
    }, problems


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


def _read_pack_artifact(path: Path, problems: list[Problem], *, label: str) -> dict[str, Any]:
    if not path.is_file():
        problems.append(Problem("error", "context_pack_artifact_missing", f"{label} context pack artifact is missing", path.as_posix()))
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        problems.append(Problem("error", "context_pack_artifact_invalid_json", f"{label} context pack artifact is not valid JSON", path.as_posix()))
        return {}
    if not isinstance(payload, dict):
        problems.append(Problem("error", "context_pack_artifact_invalid", f"{label} context pack artifact must be an object", path.as_posix()))
        return {}
    data = payload.get("data") if str(payload.get("command") or "") == "context pack" else payload
    if not isinstance(data, dict):
        problems.append(Problem("error", "context_pack_artifact_missing_data", f"{label} context pack artifact is missing data", path.as_posix()))
        return {}
    groups = data.get("groups")
    if not isinstance(groups, dict):
        problems.append(Problem("error", "context_pack_artifact_invalid_data", f"{label} context pack artifact is missing groups", path.as_posix()))
        return {}
    expected_digest = str(data.get("pack_digest") or "")
    digest_basis = {key: value for key, value in data.items() if key not in {"pack_digest", "artifact", "repository", "graph"}}
    actual_digest = digest_data(digest_basis)
    if expected_digest != actual_digest:
        problems.append(Problem("error", "context_pack_artifact_digest_mismatch", f"{label} context pack artifact digest does not match its content", path.as_posix()))
        return {}
    return data


def _pack_identity(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    return {
        "path": path.as_posix(),
        "pack_digest": str(data.get("pack_digest") or ""),
        "task_id": str(task.get("id") or ""),
    }


def _group_count_delta(baseline: dict[str, Any], candidate: dict[str, Any], group: str) -> dict[str, int]:
    baseline_count = _group_count(baseline, group)
    candidate_count = _group_count(candidate, group)
    return {
        "baseline": baseline_count,
        "candidate": candidate_count,
        "delta": candidate_count - baseline_count,
    }


def _group_count(data: dict[str, Any], group: str) -> int:
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    values = groups.get(group)
    return len(values) if isinstance(values, list) else 0


def _missing_group_refs(baseline: dict[str, Any], candidate: dict[str, Any], group: str) -> list[dict[str, str]]:
    candidate_refs = {_ref_key(ref) for ref in _group_refs(candidate, group)}
    missing = [ref for ref in _group_refs(baseline, group) if _ref_key(ref) not in candidate_refs]
    return sorted(missing, key=lambda item: (item.get("path", ""), item.get("section", ""), item.get("kind", "")))


def _group_refs(data: dict[str, Any], group: str) -> list[dict[str, str]]:
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    values = groups.get(group)
    refs: list[dict[str, str]] = []
    if not isinstance(values, list):
        return refs
    for item in values:
        if not isinstance(item, dict):
            continue
        ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        refs.append(
            {
                "kind": str(ref.get("kind") or ""),
                "path": str(ref.get("path") or ""),
                "section": str(ref.get("section") or ""),
            }
        )
    return refs


def _ref_key(ref: dict[str, str]) -> tuple[str, str, str]:
    return (str(ref.get("kind") or ""), str(ref.get("path") or ""), str(ref.get("section") or ""))


def _pack_regressions(count_deltas: dict[str, dict[str, int]], missing_must_read_refs: list[dict[str, str]], *, max_must_read_drop: int | None, max_reviewed_knowledge_drop: int | None) -> list[Problem]:
    problems: list[Problem] = []
    if max_must_read_drop is not None and int(count_deltas["must_read"]["delta"]) < -abs(max_must_read_drop):
        problems.append(Problem("error", "context_pack_must_read_regressed", "context pack must_read count dropped more than allowed"))
    if max_reviewed_knowledge_drop is not None and int(count_deltas["reviewed_knowledge"]["delta"]) < -abs(max_reviewed_knowledge_drop):
        problems.append(Problem("error", "context_pack_reviewed_knowledge_regressed", "context pack reviewed_knowledge count dropped more than allowed"))
    for ref in missing_must_read_refs:
        problems.append(Problem("error", "context_pack_must_read_ref_missing", "candidate context pack is missing a baseline must_read source ref", f"{ref.get('path', '')}#{ref.get('section', '')}"))
    return problems


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

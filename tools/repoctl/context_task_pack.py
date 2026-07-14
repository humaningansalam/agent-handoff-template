from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .context import build_context_bundle
from .context_chunks import chunk_markdown_file, chunk_text_source
from .context_model import ContextBundle, ContextCandidate, ContextSourceRef
from .graph import project_context_neighborhood
from .graph_model import digest_data
from .graph_store import load_materialized_graph
from .git import normalize_repo_path, repo_git_head
from .language_profiles import collect_verification_hints
from .markdown import find_section
from .repositories import RepoTarget
from .tasks import Problem, Task, resolve_task, task_discovery_values


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def materialize_task_context_pack_benchmark_tasks(root: Path, *, fixture: Path, force: bool = False) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    tasks_path = fixture / "tasks.json"
    if not tasks_path.is_file():
        return {}, [Problem("error", "context_pack_benchmark_tasks_missing", "context pack benchmark tasks.json is missing", tasks_path.as_posix())]
    try:
        payload = json.loads(tasks_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [Problem("error", "context_pack_benchmark_tasks_invalid_json", f"context pack benchmark tasks.json is invalid: {exc}", tasks_path.as_posix())]
    task_entries = payload.get("tasks") if isinstance(payload, dict) else None
    if not isinstance(task_entries, list):
        return {}, [Problem("error", "context_pack_benchmark_tasks_invalid", "context pack benchmark tasks must be a list", tasks_path.as_posix())]

    created: list[str] = []
    unchanged: list[str] = []
    overwritten: list[str] = []
    conflicts: list[str] = []
    for entry in task_entries:
        if not isinstance(entry, dict):
            problems.append(Problem("error", "context_pack_benchmark_task_invalid", "context pack benchmark task entry must be an object", tasks_path.as_posix()))
            continue
        rel_path = str(entry.get("path") or "")
        content = entry.get("content")
        if not rel_path.startswith("docs/archive/tasks/T-") or not rel_path.endswith(".md") or not isinstance(content, str):
            problems.append(Problem("error", "context_pack_benchmark_task_invalid", "context pack benchmark task must declare archive task path and content", rel_path or tasks_path.as_posix()))
            continue
        target = root / rel_path
        try:
            target.resolve().relative_to(root.resolve())
        except ValueError:
            problems.append(Problem("error", "context_pack_benchmark_task_outside_workspace", "context pack benchmark task path must stay inside workspace", rel_path))
            continue
        if target.exists():
            current = target.read_text(encoding="utf-8")
            if current == content:
                unchanged.append(rel_path)
                continue
            if not force:
                conflicts.append(rel_path)
                continue
            overwritten.append(rel_path)
        else:
            created.append(rel_path)
        target.parent.mkdir(parents=True, exist_ok=True)
        target.write_text(content, encoding="utf-8")

    if conflicts:
        problems.append(Problem("error", "context_pack_benchmark_task_conflict", "context pack benchmark task already exists with different content; rerun with --force to overwrite", conflicts[0]))
    data = {
        "schema": "repoctl.context.task_pack.benchmark.materialize",
        "schema_version": 1,
        "fixture": fixture.as_posix(),
        "created": created,
        "unchanged": unchanged,
        "overwritten": overwritten,
        "conflicts": conflicts,
        "totals": {
            "created": len(created),
            "unchanged": len(unchanged),
            "overwritten": len(overwritten),
            "conflict": len(conflicts),
            "task_count": len(task_entries),
        },
    }
    return data, problems


def build_task_context_pack(root: Path, *, target: RepoTarget, task_id: str, budget_tokens: int = 1500, explain: bool = False) -> tuple[dict[str, Any], list[Problem], dict[str, Any]]:
    task = resolve_task(root, task_id)
    discovery = task_discovery_values(task)
    chosen = _without_discovery_placeholders(discovery.get("Chosen files", []))
    reviewed = _without_discovery_placeholders(discovery.get("Candidate files reviewed", []))
    chosen_paths = {normalize_repo_path(path) for path in chosen}
    reviewed_paths = {normalize_repo_path(path) for path in reviewed} - chosen_paths
    stage = "scoped" if chosen else "bootstrap"
    query = _task_seed_query(task)
    bundle: ContextBundle | None = None
    problems: list[Problem] = []
    meta: dict[str, Any] = {"repository": target.to_dict()}
    snapshot, graph_problems, graph_meta = load_materialized_graph(root, target=target)
    if query:
        bundle, bundle_problems, meta = build_context_bundle(
            root,
            target=target,
            query=query,
            explain=explain,
            graph_result=(snapshot, graph_problems, graph_meta),
            include_linked_records=False,
        )
        problems.extend(bundle_problems)
    task_graph_evidence = _direct_task_graph_evidence(snapshot, target=target, chosen=chosen) if stage == "scoped" and snapshot is not None else []
    mandatory_candidates, mandatory_problems = _explicit_context_doc_candidates(root, task)
    problems.extend(mandatory_problems)
    required_candidates, required_problems = _required_task_candidates(root, target=target, task=task)
    problems.extend(required_problems)
    discovery_candidates, discovery_problems = _discovery_file_candidates(root, target=target, chosen=chosen, reviewed=reviewed)
    problems.extend(discovery_problems)
    fallback_candidates, fallback_problems = _startup_fallback_candidates(root, target=target, task=task)
    problems.extend(fallback_problems)
    verification_candidates, verification_problems = _verification_hint_candidates(root, target=target)
    problems.extend(verification_problems)
    allowed_bundle_kinds = {"current_source", "product_manifest", "verification_hint", "graph_relation"}
    bundle_candidates = [
        candidate
        for candidate in (bundle.evidence if bundle is not None else [])
        if candidate.source_ref.kind in allowed_bundle_kinds
    ][:8]
    if stage == "scoped":
        fallback_candidates = [candidate for candidate in fallback_candidates if candidate.source_ref.kind == "product_manifest"]
    context_candidates = _dedupe_candidates(
        [*required_candidates, *mandatory_candidates, *discovery_candidates, *fallback_candidates, *verification_candidates, *bundle_candidates]
    )
    groups = _group_candidates(context_candidates)
    groups["task_graph_evidence"] = task_graph_evidence
    groups.update(_agent_pack_groups(groups, bundle))
    groups["edit_candidates"] = _candidate_items(
        discovery_candidates,
        reason="Chosen files are the active edit scope",
        allowed_paths=chosen_paths,
    )
    groups["supporting_evidence"] = _candidate_items(
        discovery_candidates,
        reason="Reviewed files are supporting evidence",
        allowed_paths=reviewed_paths,
    )
    _mark_group_requirements(
        groups,
        required_paths={
            "AGENTS.md",
            task.rel_path,
            *(_context_doc_paths(task)),
            *chosen_paths,
        },
    )
    graph_completeness = snapshot.completeness if snapshot is not None else {}
    warnings = [*_pack_warnings(bundle, task), *_graph_capability_warnings(graph_completeness, graph_meta), *_pack_quality_warnings(groups, task)]
    observed_head, _head_state = repo_git_head(root, target)
    input_digest = digest_data(
        {
            "task_content_digest": _task_content_digest(task),
            "candidate_query_history": _without_discovery_placeholders(discovery.get("Candidate query", [])),
            "reviewed_files": reviewed,
            "chosen_files": chosen,
            "context_docs": _context_doc_digest_inputs(root, task),
            "repository": target.to_dict(),
            "observed_head": observed_head,
            "graph_snapshot_digest": snapshot.snapshot_digest if snapshot is not None else "",
            "capability_matrix": graph_completeness.get("capabilities", {}),
        }
    )
    data = {
        "schema": "repoctl.context.task_pack",
        "schema_version": 2,
        "authoritative": False,
        "stage": stage,
        "input_digest": input_digest,
        "task": {
            "id": task.id,
            "path": task.rel_path,
            "status": task.status,
            "repo_id": str(task.frontmatter.get("repo_id") or ""),
            "area": str(task.frontmatter.get("area") or ""),
            "content_digest": _task_content_digest(task),
        },
        "seed": {
            "source": "discovery_query_history_only",
            "query": query,
            "used_sections": _used_sections(task),
        },
        "groups": groups,
        "metrics": _pack_metrics(groups, bundle),
        "bundle": bundle.to_dict() if bundle is not None else None,
        "warnings": warnings,
    }
    data["budget"] = {
        "maximum_estimated_tokens": budget_tokens,
        "final_render_estimated_tokens": 0,
    }
    data["pack_digest"] = "sha256:" + "0" * 64
    stop_reason = _apply_render_budget(data, budget_tokens=budget_tokens)
    data["stop_reason"] = stop_reason
    data["metrics"] = _pack_metrics(data["groups"], bundle)
    data["budget"]["final_render_estimated_tokens"] = estimate_tokens(render_task_context_pack_markdown(data))
    data["metrics"]["requested_tokens"] = budget_tokens
    data["metrics"]["estimated_tokens"] = data["budget"]["final_render_estimated_tokens"]
    data.pop("pack_digest", None)
    data["pack_digest"] = digest_data(data)
    return data, problems, meta


def render_task_context_pack_markdown(data: dict[str, Any]) -> str:
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    seed = data.get("seed") if isinstance(data.get("seed"), dict) else {}
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    lines = [
        "# Agent Context Pack",
        "",
        f"- Task: `{task.get('id', '')}`",
        f"- Repository: `{task.get('repo_id', '')}`",
        f"- Status: `{task.get('status', '')}`",
        f"- Stage: `{data.get('stage', '')}`",
        f"- Input digest: `{data.get('input_digest', '')}`",
        f"- Pack digest: `{data.get('pack_digest', '')}`",
        f"- Stop reason: `{data.get('stop_reason', '')}`",
        f"- Source: {seed.get('source', '')}",
        "",
        "## Task Startup Order",
        "",
        "- Read `AGENTS.md`.",
        f"- Read `{task.get('path', '')}`.",
        "- Read this Context Pack before editing.",
        "- Open the `must_read` sources and inspect candidate files directly before treating them as scope.",
        "",
        "## Task Query",
        "",
        "```text",
        str(seed.get("query") or "").strip()[:1200],
        "```",
        "",
    ]
    sections = [
        ("must_read", "Read First"),
        ("edit_candidates", "Active Edit Candidates"),
        ("supporting_evidence", "Reviewed Supporting Evidence"),
        ("likely_change", "Likely Change Surface"),
        ("impact", "Definitions, Callers, Imports, Dependents"),
        ("verification", "Tests And Verification Hints"),
        ("warnings", "Ambiguity And Completeness Warnings"),
    ]
    for group, title in sections:
        lines.extend([f"## {title}", ""])
        items = groups.get(group)
        if not isinstance(items, list) or not items:
            lines.extend(["- No evidence selected.", ""])
            continue
        for item in _limited_group_items(items, limit=12):
            lines.extend(_markdown_item(item))
        lines.append("")
    lines.extend(
        [
            "## Source References",
            "",
            f"- Unique must-read sources: {metrics.get('unique_must_read_source_count', 0)}",
            f"- Unique verification sources: {metrics.get('unique_verification_source_count', 0)}",
            f"- Estimated tokens: {metrics.get('estimated_tokens', 0)} / {metrics.get('requested_tokens', 0)} maximum",
            "",
        ]
    )
    return "\n".join(lines).rstrip() + "\n"


COMPACT_GROUP_LIMITS = {
    "must_read": 7,
    "edit_candidates": 8,
    "supporting_evidence": 8,
    "likely_change": 5,
    "impact": 5,
    "verification": 5,
    "warnings": 8,
}


def compact_task_context_pack(data: dict[str, Any], *, excerpt_chars: int = 180) -> dict[str, Any]:
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    canonical_groups = ("must_read", "edit_candidates", "supporting_evidence", "likely_change", "impact", "verification", "warnings")
    compact_groups = {
        group: [_compact_pack_item(item, excerpt_chars=excerpt_chars) for item in _compact_group_items(groups.get(group) or [], group)]
        for group in canonical_groups
    }
    group_counts = {name: len(items) for name, items in sorted(groups.items()) if isinstance(items, list)}
    compact = {
        "schema": data.get("schema", "repoctl.context.task_pack"),
        "schema_version": data.get("schema_version", 1),
        "view": "compact",
        "authoritative": data.get("authoritative", False),
        "stage": data.get("stage", ""),
        "input_digest": data.get("input_digest", ""),
        "stop_reason": data.get("stop_reason", ""),
        "budget": data.get("budget", {}),
        "task": data.get("task", {}),
        "seed": _compact_seed(data.get("seed") if isinstance(data.get("seed"), dict) else {}),
        "groups": compact_groups,
        "metrics": {
            **(data.get("metrics") if isinstance(data.get("metrics"), dict) else {}),
            "group_counts": group_counts,
            "omitted_group_items": {
                name: max(0, count - len(compact_groups.get(name, [])))
                for name, count in group_counts.items()
            },
        },
        "warnings": data.get("warnings", []),
        "source_pack_digest": data.get("pack_digest", ""),
    }
    compact["summary"] = _compact_pack_summary(compact_groups, compact["metrics"])
    compact["pack_digest"] = digest_data(compact)
    return compact


def _compact_group_items(items: list[Any], group: str) -> list[dict[str, Any]]:
    return _limited_group_items(items, limit=COMPACT_GROUP_LIMITS[group], filter_noisy=True)


def _limited_group_items(items: list[Any], *, limit: int, filter_noisy: bool = False) -> list[dict[str, Any]]:
    filtered = [
        item
        for item in items
        if isinstance(item, dict)
        and (
            not filter_noisy
            or item.get("requirement") == "required"
            or not _is_noisy_pack_item(item)
        )
    ]
    required = [item for item in filtered if item.get("requirement") == "required"]
    optional = [item for item in filtered if item.get("requirement") != "required"]
    return [*required, *optional[: max(0, limit - len(required))]]


def _compact_seed(seed: dict[str, Any]) -> dict[str, Any]:
    query = str(seed.get("query") or "")
    compact = {
        "source": seed.get("source", ""),
        "used_sections": seed.get("used_sections", []),
    }
    if query.strip():
        compact["query_preview"] = _truncate_text(query, 240)
    return compact


def _compact_pack_summary(groups: dict[str, list[dict[str, Any]]], metrics: dict[str, Any]) -> dict[str, Any]:
    top_refs: list[dict[str, str]] = []
    for group in ("must_read", "edit_candidates", "supporting_evidence", "likely_change", "impact", "verification"):
        for item in groups.get(group, [])[:3]:
            ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
            path = str(ref.get("path") or "")
            if path:
                top_refs.append({"group": group, "path": path, "section": str(ref.get("section") or "")})
    return {
        "read_first_count": len(groups.get("must_read", [])),
        "top_refs": top_refs[:10],
        "requested_tokens": int(metrics.get("requested_tokens") or 0),
        "estimated_tokens": int(metrics.get("estimated_tokens") or 0),
    }


def _compact_pack_item(item: dict[str, Any], *, excerpt_chars: int) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("status", "record_id", "code", "selection_reason", "requirement"):
        if item.get(key):
            compact[key] = item[key]
    ref = item.get("source_ref")
    if isinstance(ref, dict):
        compact["source_ref"] = ref
    record = item.get("record")
    if isinstance(record, dict):
        compact["record"] = {key: record.get(key) for key in ("id", "kind", "status", "title") if record.get(key)}
    score_breakdown = item.get("score_breakdown")
    if isinstance(score_breakdown, dict) and score_breakdown:
        compact["score_breakdown"] = score_breakdown
    excerpt = item.get("excerpt") or item.get("claim")
    if excerpt:
        compact["excerpt"] = _truncate_text(str(excerpt), excerpt_chars)
    graph_path = item.get("graph_path")
    if isinstance(graph_path, list) and graph_path:
        compact["graph_path_count"] = len(graph_path)
    return compact


def _is_noisy_pack_item(item: dict[str, Any]) -> bool:
    ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
    path = str(ref.get("path") or "").lower()
    text = str(item.get("excerpt") or item.get("selection_reason") or "").lower()
    noisy_parts = (
        "/.cxx/",
        "/.dart_tool/",
        "/.firebase/",
        "/.gradle/",
        "/.next/",
        "/.nuxt/",
        "/.parcel-cache/",
        "/.playwright-browsers/",
        "/.pytest_cache/",
        "/.svelte-kit/",
        "/.temp/",
        "/.turbo/",
        "/__pycache__/",
        "/build/",
        "/builds/",
        "/dist/",
        "/library/",
        "/logs/",
        "/node_modules/",
        "/obj/",
        "/target/",
        "/temp/",
        "/usersettings/",
    )
    noisy_suffixes = (".csv", ".ipynb", ".lock", ".log", ".pkl", ".pyc", ".svg", ".tsbuildinfo", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb")
    return any(part in path for part in noisy_parts) or path.endswith(noisy_suffixes) or "parse_status" in text and any(marker in text for marker in ("skipped", "unsupported"))


def _truncate_text(value: str, limit: int) -> str:
    compact = " ".join(value.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def compare_task_context_packs(
    *,
    baseline_path: Path,
    candidate_path: Path,
    max_must_read_drop: int | None = None,
    require_warning_stability: bool = False,
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
    }
    metric_deltas = _metric_deltas(baseline, candidate)
    missing_refs = _missing_group_refs(baseline, candidate, "must_read")
    warning_deltas = _warning_deltas(baseline, candidate)
    regressions = _pack_regressions(
        count_deltas,
        missing_refs,
        warning_deltas,
        max_must_read_drop=max_must_read_drop,
        require_warning_stability=require_warning_stability,
    )
    problems.extend(regressions)
    return {
        "schema": "repoctl.context.task_pack.compare",
        "schema_version": 1,
        "baseline": _pack_identity(baseline_path, baseline),
        "candidate": _pack_identity(candidate_path, candidate),
        "count_deltas": count_deltas,
        "metric_deltas": metric_deltas,
        "warning_deltas": warning_deltas,
        "missing_must_read_refs": missing_refs,
        "regressions": [problem.to_dict() for problem in regressions],
        "gates": {
            "max_must_read_drop": max_must_read_drop,
            "require_warning_stability": require_warning_stability,
        },
    }, problems


def run_task_context_pack_benchmark(
    root: Path,
    *,
    target: RepoTarget,
    fixture: Path,
    budget_tokens: int = 1500,
    explain: bool = False,
    min_must_read_recall: float | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    cases_path = fixture / "cases.json"
    if not cases_path.is_file():
        return {}, [Problem("error", "context_pack_benchmark_cases_missing", "context pack benchmark cases.json is missing", cases_path.as_posix())]
    try:
        data = json.loads(cases_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as exc:
        return {}, [Problem("error", "context_pack_benchmark_cases_invalid_json", f"context pack benchmark cases.json is invalid: {exc}", cases_path.as_posix())]
    cases = data.get("cases") if isinstance(data, dict) else None
    if not isinstance(cases, list):
        return {}, [Problem("error", "context_pack_benchmark_cases_invalid", "context pack benchmark cases must be a list", cases_path.as_posix())]

    results: list[dict[str, Any]] = []
    for case in cases:
        if not isinstance(case, dict):
            continue
        task_id = str(case.get("task_id") or "")
        if not task_id:
            problems.append(Problem("error", "context_pack_benchmark_task_missing", "context pack benchmark case is missing task_id", str(case.get("id") or "")))
            continue
        pack, pack_problems, _meta = build_task_context_pack(root, target=target, task_id=task_id, budget_tokens=budget_tokens, explain=explain)
        problems.extend(pack_problems)
        result = _score_pack_case(case, pack, pack_problems)
        results.append(result)

    summary = _pack_benchmark_summary(results)
    if min_must_read_recall is not None and float(summary.get("mean_must_read_recall") or 0.0) < min_must_read_recall:
        problems.append(Problem("error", "context_pack_benchmark_must_read_recall_failed", "context pack benchmark must_read recall is below gate"))
    payload = {
        "schema": "repoctl.context.task_pack.benchmark",
        "schema_version": 1,
        "fixture": fixture.as_posix(),
        "repository": target.to_dict(),
        "case_count": len(results),
        "results": results,
        "summary": summary,
        "gates": {"min_must_read_recall": min_must_read_recall},
    }
    payload["benchmark_digest"] = digest_data(payload)
    return payload, problems


def compare_task_context_pack_benchmarks(
    *,
    baseline_path: Path,
    candidate_path: Path,
    max_mean_must_read_recall_drop: float | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    baseline = _read_pack_benchmark_artifact(baseline_path, problems, label="baseline")
    candidate = _read_pack_benchmark_artifact(candidate_path, problems, label="candidate")
    if not baseline or not candidate:
        return {}, problems
    baseline_summary = baseline.get("summary") if isinstance(baseline.get("summary"), dict) else {}
    candidate_summary = candidate.get("summary") if isinstance(candidate.get("summary"), dict) else {}
    metric_deltas = {
        "mean_must_read_recall": _float_metric_delta(baseline_summary, candidate_summary, "mean_must_read_recall"),
        "required_must_read_count": _int_metric_delta(baseline_summary, candidate_summary, "required_must_read_count"),
        "warning_count": _int_metric_delta(baseline_summary, candidate_summary, "warning_count"),
    }
    case_deltas = _pack_benchmark_case_deltas(baseline, candidate)
    regressions = _pack_benchmark_regressions(
        metric_deltas,
        case_deltas,
        max_mean_must_read_recall_drop=max_mean_must_read_recall_drop,
    )
    problems.extend(regressions)
    return {
        "schema": "repoctl.context.task_pack.benchmark.compare",
        "schema_version": 1,
        "baseline": _pack_benchmark_identity(baseline_path, baseline),
        "candidate": _pack_benchmark_identity(candidate_path, candidate),
        "metric_deltas": metric_deltas,
        "case_deltas": case_deltas,
        "regressions": [problem.to_dict() for problem in regressions],
        "gates": {"max_mean_must_read_recall_drop": max_mean_must_read_recall_drop},
    }, problems


def _task_seed_query(task: Task) -> str:
    discovery = task_discovery_values(task)
    return "\n".join(_without_discovery_placeholders(discovery.get("Candidate query", [])))


def _without_discovery_placeholders(values: list[str]) -> list[str]:
    placeholders = {"none", "none yet", "n/a", "na", "tbd", "todo", "pending", "-"}
    return [value for value in values if value.strip().strip("`").lower() not in placeholders]


def _required_task_candidates(root: Path, *, target: RepoTarget, task: Task) -> tuple[list[ContextCandidate], list[Problem]]:
    candidates: list[ContextCandidate] = []
    problems: list[Problem] = []
    for rel_path in ("AGENTS.md", task.rel_path):
        path = root / rel_path
        if not path.is_file():
            problems.append(Problem("warning", "context_pack_required_source_missing", "required context source is missing", rel_path))
            continue
        try:
            chunks = chunk_markdown_file(root, path)
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem("warning", "context_pack_required_source_unreadable", str(exc), rel_path))
            continue
        chunk = chunks[0]
        candidates.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=f"Open {rel_path} directly for the canonical task context.",
                score=120.0,
                score_breakdown={"required_task_context": 1.0},
                selection_reasons=["Required task context"],
                graph_path=[],
            )
        )
    return candidates, problems


def _discovery_file_candidates(root: Path, *, target: RepoTarget, chosen: list[str], reviewed: list[str]) -> tuple[list[ContextCandidate], list[Problem]]:
    candidates: list[ContextCandidate] = []
    problems: list[Problem] = []
    prefix = f"{target.display_path.rstrip('/')}/"
    ordered = [(value, "active Chosen file", 118.0) for value in chosen]
    ordered.extend((value, "reviewed supporting file", 108.0) for value in reviewed if value not in chosen)
    for value, reason, score in ordered:
        workspace_path = normalize_repo_path(value)
        if not workspace_path.startswith(prefix):
            problems.append(Problem("warning", "context_pack_discovery_path_outside_repo", "Discovery path is outside the selected repository", value))
            continue
        path = root / workspace_path
        if not path.is_file():
            candidates.append(
                ContextCandidate(
                    source_ref=ContextSourceRef(
                        kind="planned_new",
                        path=workspace_path,
                        section="planned new file",
                        content_sha256=digest_data({"planned_new": workspace_path}),
                    ),
                    text="Chosen path has no current file content; create it before editing or keep it as an intentional deletion.",
                    score=score,
                    score_breakdown={"structured_discovery": 1.0},
                    selection_reasons=[f"{reason}; current content unavailable"],
                    graph_path=[],
                )
            )
            problems.append(Problem("warning", "context_pack_planned_new_file", "Chosen path has no current file content", workspace_path))
            continue
        try:
            if path.suffix.lower() in {".md", ".markdown"}:
                chunks = chunk_markdown_file(root, path)
            else:
                chunks = [chunk_text_source(root, workspace_path, path.read_text(encoding="utf-8"), kind="current_source", section=path.name)]
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem("warning", "context_pack_discovery_path_unreadable", str(exc), workspace_path))
            continue
        for chunk in chunks[:4]:
            candidates.append(
                ContextCandidate(
                    source_ref=chunk.source_ref,
                    text=chunk.text,
                    score=score,
                    score_breakdown={"structured_discovery": 1.0},
                    selection_reasons=[reason],
                    graph_path=[],
                )
            )
    return candidates, problems


def _candidate_items(candidates: list[ContextCandidate], *, reason: str, allowed_paths: set[str]) -> list[dict[str, Any]]:
    items_by_path: dict[str, dict[str, Any]] = {}
    for candidate in candidates:
        path = normalize_repo_path(candidate.source_ref.path)
        if path not in allowed_paths:
            continue
        item = candidate.to_dict()
        item["selection_reason"] = reason
        items_by_path.setdefault(path, item)
    return list(items_by_path.values())


def _mark_group_requirements(groups: dict[str, list[dict[str, Any]]], *, required_paths: set[str]) -> None:
    normalized_required = {normalize_repo_path(path) for path in required_paths}
    for items in groups.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
            path = normalize_repo_path(str(ref.get("path") or ""))
            item["requirement"] = "required" if path in normalized_required else "optional"


def _context_doc_digest_inputs(root: Path, task: Task) -> list[dict[str, str]]:
    inputs: list[dict[str, str]] = []
    for rel_path in _context_doc_paths(task):
        path = root / rel_path
        try:
            digest = "sha256:" + hashlib.sha256(path.read_bytes()).hexdigest()
        except OSError:
            digest = ""
        inputs.append({"path": rel_path, "content_sha256": digest})
    return inputs


def _task_content_digest(task: Task) -> str:
    try:
        return digest_data({"content": task.path.read_text(encoding="utf-8")})
    except OSError:
        return ""


CONTEXT_DOC_RE = re.compile(r"`([^`]+)`")


def _explicit_context_doc_candidates(root: Path, task: Task) -> tuple[list[ContextCandidate], list[Problem]]:
    candidates: list[ContextCandidate] = []
    problems: list[Problem] = []
    for rel_path in _context_doc_paths(task):
        path = root / rel_path
        try:
            path.resolve().relative_to(root.resolve())
        except ValueError:
            problems.append(Problem("error", "context_pack_context_doc_outside_workspace", "task Context Docs path must stay inside workspace", rel_path))
            continue
        if not path.is_file():
            problems.append(Problem("warning", "context_pack_context_doc_missing", "task Context Docs path is missing", rel_path))
            continue
        try:
            chunk = chunk_text_source(root, rel_path, path.read_text(encoding="utf-8"), kind="document", section=path.name)
        except (OSError, UnicodeDecodeError) as exc:
            problems.append(Problem("warning", "context_pack_context_doc_unreadable", str(exc), rel_path))
            continue
        candidates.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=chunk.text,
                score=100.0,
                score_breakdown={"explicit_context_doc": 1.0},
                selection_reasons=["Task Context Docs explicit source"],
                graph_path=[],
            )
        )
    return candidates, problems


def _context_doc_paths(task: Task) -> list[str]:
    section = _section(task, "Context Docs")
    paths: list[str] = []
    seen: set[str] = set()
    for match in CONTEXT_DOC_RE.finditer(section):
        rel_path = match.group(1).strip()
        if not rel_path or rel_path in seen:
            continue
        seen.add(rel_path)
        paths.append(rel_path)
    return paths


def _startup_fallback_candidates(root: Path, *, target: RepoTarget, task: Task) -> tuple[list[ContextCandidate], list[Problem]]:
    candidates: list[ContextCandidate] = []
    problems: list[Problem] = []
    for rel_path, score in _startup_fallback_docs(task, target=target):
        path = root / rel_path
        if not path.is_file():
            continue
        try:
            if _is_product_manifest_path(rel_path):
                chunks = [chunk_text_source(root, rel_path, path.read_text(encoding="utf-8"), kind="product_manifest", section=path.name)]
            else:
                chunks = chunk_markdown_file(root, path)
        except UnicodeDecodeError as exc:
            problems.append(Problem("warning", "context_pack_startup_doc_non_utf8", str(exc), rel_path))
            continue
        except OSError as exc:
            problems.append(Problem("warning", "context_pack_startup_doc_unreadable", str(exc), rel_path))
            continue
        for chunk in chunks[:4]:
            candidates.append(
                ContextCandidate(
                    source_ref=chunk.source_ref,
                    text=chunk.text,
                    score=score,
                    score_breakdown={"startup_fallback": 1.0},
                    selection_reasons=["Startup fallback source before structured Discovery is available"],
                    graph_path=[],
                )
            )
    return candidates, problems


def _startup_fallback_docs(task: Task, *, target: RepoTarget) -> list[tuple[str, float]]:
    repo_id = str(task.frontmatter.get("repo_id") or "").strip()
    area = str(task.frontmatter.get("area") or "").strip()
    repo_scoped = bool(repo_id) or area in {"repo", "frontend", "backend", "fullstack", "mobile", "infra", "test", "tests"}
    repo_prefix = target.display_path.rstrip("/")
    if repo_scoped:
        docs = [
            (f"{repo_prefix}/README.md", 95.0),
            (f"{repo_prefix}/package.json", 92.0),
            (f"{repo_prefix}/tsconfig.json", 90.0),
            (f"{repo_prefix}/jsconfig.json", 90.0),
            (f"{repo_prefix}/pyproject.toml", 92.0),
            (f"{repo_prefix}/pubspec.yaml", 92.0),
            (f"{repo_prefix}/analysis_options.yaml", 90.0),
            (f"{repo_prefix}/Cargo.toml", 92.0),
            (f"{repo_prefix}/go.mod", 92.0),
            (f"{repo_prefix}/Packages/manifest.json", 90.0),
            (f"{repo_prefix}/ProjectSettings/ProjectVersion.txt", 90.0),
        ]
    else:
        docs = [
            ("docs/PRD.md", 80.0),
            ("AGENTS.md", 75.0),
            ("README.md", 70.0),
            ("docs/README.md", 65.0),
        ]
    seen: set[str] = set()
    unique: list[tuple[str, float]] = []
    for rel_path, score in docs:
        if rel_path in seen:
            continue
        seen.add(rel_path)
        unique.append((rel_path, score))
    return unique


def _is_product_manifest_path(path: str) -> bool:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    return name in {"package.json", "tsconfig.json", "jsconfig.json", "pyproject.toml", "pubspec.yaml", "analysis_options.yaml", "cargo.toml", "go.mod", "manifest.json", "projectversion.txt", "requirements.txt"} or (
        name.startswith("requirements-") and name.endswith(".txt")
    )


def _verification_hint_candidates(root: Path, *, target: RepoTarget) -> tuple[list[ContextCandidate], list[Problem]]:
    candidates: list[ContextCandidate] = []
    problems: list[Problem] = []
    for hint in collect_verification_hints(target.root_path):
        source = target.root_path / hint.source_path
        if not source.exists():
            continue
        try:
            rel = source.relative_to(root).as_posix()
            text = f"Verification command: {hint.command}\nSource: {rel}\nReason: {hint.reason}\nProvider: {hint.provider}"
            chunk = chunk_text_source(root, rel, text, kind="verification_hint", section=f"verification: {hint.command}")
        except OSError as exc:
            problems.append(Problem("warning", "context_pack_verification_hint_unreadable", str(exc), hint.source_path))
            continue
        candidates.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=chunk.text,
                score=94.0,
                score_breakdown={"verification_hint": 1.0},
                selection_reasons=["Manifest-derived verification hint"],
                graph_path=[],
            )
        )
    return candidates, problems


def _direct_task_graph_evidence(snapshot: Any, *, target: RepoTarget, chosen: list[str]) -> list[dict[str, Any]]:
    prefix = f"{target.display_path.rstrip('/')}/"
    seed_paths: list[str] = []
    for workspace_path in chosen:
        normalized = normalize_repo_path(workspace_path)
        if not normalized.startswith(prefix):
            continue
        repo_path = normalize_repo_path(normalized[len(prefix) :])
        if repo_path and repo_path not in seed_paths:
            seed_paths.append(repo_path)
    if not seed_paths:
        return []
    projection = project_context_neighborhood(snapshot, seed_paths=seed_paths)
    relations = projection.get("relations") if isinstance(projection.get("relations"), list) else []
    return _dedupe_dict_items([
        _graph_relation_item(relation, reason="provider-confirmed relation from active Chosen files")
        for relation in relations
        if isinstance(relation, dict)
    ])


def _graph_relation_item(relation: dict[str, Any], *, reason: str) -> dict[str, Any]:
    digest = digest_data(relation)
    source_symbol = relation.get("from_symbol") if isinstance(relation.get("from_symbol"), dict) else {}
    target_symbol = relation.get("to_symbol") if isinstance(relation.get("to_symbol"), dict) else {}
    source_label = source_symbol.get("qualified_name") or source_symbol.get("name") or relation.get("from_path")
    target_label = target_symbol.get("qualified_name") or target_symbol.get("name") or relation.get("to_path")
    edge = str(relation.get("edge") or "RELATED")
    return {
        "status": "current",
        "source_ref": {
            "kind": "graph_relation",
            "path": f"<graph-relation:{digest[7:19]}>",
            "section": edge,
            "content_sha256": digest,
        },
        "selection_reason": reason,
        "score_breakdown": {"graph": 1.0},
        "excerpt": f"{source_label} --{edge}--> {target_label}",
        "graph_path": [relation],
    }


def _dedupe_candidates(candidates: list[ContextCandidate]) -> list[ContextCandidate]:
    deduped: list[ContextCandidate] = []
    seen: set[tuple[str, str, str, int, int]] = set()
    for candidate in candidates:
        key = candidate.source_ref.key()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


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
    if str(payload.get("command") or "") == "context pack" and payload.get("ok") is False:
        problems.append(Problem("error", "context_pack_artifact_failed", f"{label} context pack artifact was produced by a failed command", path.as_posix()))
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


def _read_pack_benchmark_artifact(path: Path, problems: list[Problem], *, label: str) -> dict[str, Any]:
    if not path.is_file():
        problems.append(Problem("error", "context_pack_benchmark_artifact_missing", f"{label} context pack benchmark artifact is missing", path.as_posix()))
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        problems.append(Problem("error", "context_pack_benchmark_artifact_invalid_json", f"{label} context pack benchmark artifact is not valid JSON", path.as_posix()))
        return {}
    if not isinstance(payload, dict):
        problems.append(Problem("error", "context_pack_benchmark_artifact_invalid", f"{label} context pack benchmark artifact must be an object", path.as_posix()))
        return {}
    if str(payload.get("command") or "") == "context pack-benchmark" and payload.get("ok") is False:
        problems.append(Problem("error", "context_pack_benchmark_artifact_failed", f"{label} context pack benchmark artifact was produced by a failed command", path.as_posix()))
        return {}
    data = payload.get("data") if str(payload.get("command") or "") == "context pack-benchmark" else payload
    if not isinstance(data, dict):
        problems.append(Problem("error", "context_pack_benchmark_artifact_missing_data", f"{label} context pack benchmark artifact is missing data", path.as_posix()))
        return {}
    if str(data.get("schema") or "") != "repoctl.context.task_pack.benchmark":
        problems.append(Problem("error", "context_pack_benchmark_artifact_wrong_schema", f"{label} artifact is not a context pack benchmark", path.as_posix()))
        return {}
    expected_digest = str(data.get("benchmark_digest") or "")
    digest_basis = {key: value for key, value in data.items() if key not in {"benchmark_digest", "artifact"}}
    actual_digest = digest_data(digest_basis)
    if expected_digest != actual_digest:
        problems.append(Problem("error", "context_pack_benchmark_artifact_digest_mismatch", f"{label} context pack benchmark artifact digest does not match its content", path.as_posix()))
        return {}
    return data


def _pack_benchmark_identity(path: Path, data: dict[str, Any]) -> dict[str, Any]:
    return {
        "path": path.as_posix(),
        "benchmark_digest": str(data.get("benchmark_digest") or ""),
        "case_count": int(data.get("case_count") or 0),
    }


def _score_pack_case(case: dict[str, Any], pack: dict[str, Any], problems: list[Problem]) -> dict[str, Any]:
    required = _expected_refs(case.get("required_must_read_refs"))
    must_read_refs = _group_refs(pack, "must_read")
    found = [ref for ref in required if _contains_expected_ref(must_read_refs, ref)]
    warning_codes = [str(warning.get("code") or "") for warning in pack.get("warnings", []) if isinstance(warning, dict) and warning.get("code")]
    return {
        "id": str(case.get("id") or ""),
        "task_id": str(case.get("task_id") or ""),
        "metrics": {
            "must_read_recall": _ratio(len(found), len(required)),
            "required_must_read_count": len(required),
            "warning_count": len(warning_codes),
        },
        "required_must_read_found": found,
        "missing_required_must_read": [ref for ref in required if not _contains_expected_ref(must_read_refs, ref)],
        "warning_codes": sorted(warning_codes),
        "problem_codes": [problem.code for problem in problems],
        "pack_digest": str(pack.get("pack_digest") or ""),
    }


def _pack_benchmark_summary(results: list[dict[str, Any]]) -> dict[str, Any]:
    return {
        "mean_must_read_recall": _mean(result.get("metrics", {}).get("must_read_recall", 0.0) for result in results),
        "required_must_read_count": sum(int(result.get("metrics", {}).get("required_must_read_count") or 0) for result in results),
        "warning_count": sum(int(result.get("metrics", {}).get("warning_count") or 0) for result in results),
    }


def _expected_refs(value: Any) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    if not isinstance(value, list):
        return refs
    for item in value:
        if not isinstance(item, dict):
            continue
        refs.append({"kind": str(item.get("kind") or ""), "path": str(item.get("path") or ""), "section": str(item.get("section") or "")})
    return refs


def _contains_expected_ref(haystack: list[dict[str, str]], needle: dict[str, str]) -> bool:
    for item in haystack:
        if str(item.get("path") or "") != str(needle.get("path") or ""):
            continue
        kind = str(needle.get("kind") or "")
        if kind and str(item.get("kind") or "") != kind:
            continue
        section = str(needle.get("section") or "")
        if section and str(item.get("section") or "") != section:
            continue
        return True
    return False


def _ratio(numerator: int, denominator: int) -> float:
    if denominator <= 0:
        return 1.0
    return round(numerator / denominator, 6)


def _mean(values: Any) -> float:
    items = list(values)
    if not items:
        return 0.0
    return round(sum(float(item) for item in items) / len(items), 6)


def _float_metric_delta(baseline: dict[str, Any], candidate: dict[str, Any], key: str) -> dict[str, float]:
    baseline_value = float(baseline.get(key) or 0.0)
    candidate_value = float(candidate.get(key) or 0.0)
    return {
        "baseline": round(baseline_value, 6),
        "candidate": round(candidate_value, 6),
        "delta": round(candidate_value - baseline_value, 6),
    }


def _int_metric_delta(baseline: dict[str, Any], candidate: dict[str, Any], key: str) -> dict[str, int]:
    baseline_value = int(baseline.get(key) or 0)
    candidate_value = int(candidate.get(key) or 0)
    return {
        "baseline": baseline_value,
        "candidate": candidate_value,
        "delta": candidate_value - baseline_value,
    }


def _pack_benchmark_case_deltas(baseline: dict[str, Any], candidate: dict[str, Any]) -> list[dict[str, Any]]:
    baseline_cases = _pack_benchmark_cases_by_id(baseline)
    candidate_cases = _pack_benchmark_cases_by_id(candidate)
    deltas: list[dict[str, Any]] = []
    for case_id in sorted(set(baseline_cases) | set(candidate_cases)):
        baseline_case = baseline_cases.get(case_id, {})
        candidate_case = candidate_cases.get(case_id, {})
        baseline_metrics = baseline_case.get("metrics") if isinstance(baseline_case.get("metrics"), dict) else {}
        candidate_metrics = candidate_case.get("metrics") if isinstance(candidate_case.get("metrics"), dict) else {}
        deltas.append(
            {
                "id": case_id,
                "present_in_baseline": bool(baseline_case),
                "present_in_candidate": bool(candidate_case),
                "task_id": str(candidate_case.get("task_id") or baseline_case.get("task_id") or ""),
                "must_read_recall": _float_metric_delta(baseline_metrics, candidate_metrics, "must_read_recall"),
                "required_must_read_count": _int_metric_delta(baseline_metrics, candidate_metrics, "required_must_read_count"),
                "warning_count": _int_metric_delta(baseline_metrics, candidate_metrics, "warning_count"),
            }
        )
    return deltas


def _pack_benchmark_cases_by_id(data: dict[str, Any]) -> dict[str, dict[str, Any]]:
    results = data.get("results")
    if not isinstance(results, list):
        return {}
    cases: dict[str, dict[str, Any]] = {}
    for item in results:
        if not isinstance(item, dict):
            continue
        case_id = str(item.get("id") or "")
        if case_id:
            cases[case_id] = item
    return cases


def _pack_benchmark_regressions(
    metric_deltas: dict[str, dict[str, Any]],
    case_deltas: list[dict[str, Any]],
    *,
    max_mean_must_read_recall_drop: float | None,
) -> list[Problem]:
    problems: list[Problem] = []
    if max_mean_must_read_recall_drop is not None and float(metric_deltas["mean_must_read_recall"]["delta"]) < -abs(max_mean_must_read_recall_drop):
        problems.append(Problem("error", "context_pack_benchmark_must_read_recall_regressed", "context pack benchmark mean must_read recall dropped more than allowed"))
    for item in case_deltas:
        if bool(item["present_in_baseline"]) and not bool(item["present_in_candidate"]):
            problems.append(Problem("error", "context_pack_benchmark_case_missing", "candidate context pack benchmark artifact is missing a baseline case", str(item["id"])))
    return problems


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


def _metric_deltas(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, dict[str, int]]:
    keys = (
        "unique_must_read_source_count",
        "unique_verification_source_count",
        "evidence_context_count",
        "requested_tokens",
        "estimated_tokens",
    )
    baseline_metrics = baseline.get("metrics") if isinstance(baseline.get("metrics"), dict) else {}
    candidate_metrics = candidate.get("metrics") if isinstance(candidate.get("metrics"), dict) else {}
    return {
        key: {
            "baseline": int(baseline_metrics.get(key) or 0),
            "candidate": int(candidate_metrics.get(key) or 0),
            "delta": int(candidate_metrics.get(key) or 0) - int(baseline_metrics.get(key) or 0),
        }
        for key in keys
    }


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


def _warning_deltas(baseline: dict[str, Any], candidate: dict[str, Any]) -> dict[str, Any]:
    baseline_codes = _warning_codes(baseline)
    candidate_codes = _warning_codes(candidate)
    all_codes = sorted(set(baseline_codes) | set(candidate_codes))
    return {
        "baseline_codes": sorted(baseline_codes),
        "candidate_codes": sorted(candidate_codes),
        "missing_codes": sorted(code for code in baseline_codes if code not in candidate_codes),
        "added_codes": sorted(code for code in candidate_codes if code not in baseline_codes),
        "counts": {
            code: {
                "baseline": baseline_codes.count(code),
                "candidate": candidate_codes.count(code),
                "delta": candidate_codes.count(code) - baseline_codes.count(code),
            }
            for code in all_codes
        },
    }


def _warning_codes(data: dict[str, Any]) -> list[str]:
    warnings = data.get("warnings")
    if not isinstance(warnings, list):
        return []
    codes: list[str] = []
    for warning in warnings:
        if not isinstance(warning, dict):
            continue
        code = str(warning.get("code") or "")
        if code:
            codes.append(code)
    return codes


def _pack_regressions(
    count_deltas: dict[str, dict[str, int]],
    missing_must_read_refs: list[dict[str, str]],
    warning_deltas: dict[str, Any],
    *,
    max_must_read_drop: int | None,
    require_warning_stability: bool,
) -> list[Problem]:
    problems: list[Problem] = []
    if max_must_read_drop is not None and int(count_deltas["must_read"]["delta"]) < -abs(max_must_read_drop):
        problems.append(Problem("error", "context_pack_must_read_regressed", "context pack must_read count dropped more than allowed"))
    for ref in missing_must_read_refs:
        problems.append(Problem("error", "context_pack_must_read_ref_missing", "candidate context pack is missing a baseline must_read source ref", f"{ref.get('path', '')}#{ref.get('section', '')}"))
    if require_warning_stability:
        for code in warning_deltas.get("missing_codes", []):
            problems.append(Problem("error", "context_pack_warning_missing", "candidate context pack is missing a baseline warning code", str(code)))
        for code in warning_deltas.get("added_codes", []):
            problems.append(Problem("error", "context_pack_warning_added", "candidate context pack added a warning code", str(code)))
    return problems


def _used_sections(task: Task) -> list[str]:
    return [name for name in ("Context Docs", "Discovery") if _section(task, name).strip()]


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
        if candidate.score_breakdown.get("required_task_context") or candidate.score_breakdown.get("explicit_context_doc"):
            groups["must_read"].append(item)
        elif candidate.score_breakdown.get("structured_discovery"):
            continue
        elif ref.kind == "verification_hint" or "Verification" in ref.section or _looks_like_test_or_workflow_ref(ref.path):
            groups["verification_hints"].append(item)
        elif _must_read_ref_path(ref.path):
            groups["must_read"].append(item)
        else:
            groups["maybe_relevant"].append(item)
    return groups


def _must_read_ref_path(path: str) -> bool:
    lowered = path.lower()
    name = lowered.rsplit("/", 1)[-1]
    return (
        path in {"AGENTS.md", "README.md", "docs/README.md", "docs/PRD.md"}
        or path.startswith("docs/contracts/")
        or path.startswith("docs/adr/")
        or lowered == "repos/readme.md"
        or lowered.startswith("repos/") and name in {"package.json", "tsconfig.json", "jsconfig.json", "pyproject.toml", "pubspec.yaml", "analysis_options.yaml", "cargo.toml", "go.mod", "manifest.json", "projectversion.txt", "requirements.txt"}
        or lowered.startswith("repos/") and name.startswith("requirements-") and name.endswith(".txt")
        or lowered.startswith("repos/docs/")
        or lowered.startswith("repos/") and lowered.endswith("/readme.md")
    )


def _looks_like_test_or_workflow_ref(path: str) -> bool:
    lowered = path.lower()
    return (
        "/test" in lowered
        or "tests/" in lowered
        or lowered.startswith("test")
        or lowered.startswith(".github/workflows/")
        or lowered.startswith("docs/workflows/")
    )


def _agent_pack_groups(groups: dict[str, list[dict[str, Any]]], bundle: ContextBundle | None) -> dict[str, list[dict[str, Any]]]:
    likely_change = _copy_items(groups.get("maybe_relevant"))
    impact = [
        *_copy_items(groups.get("task_graph_evidence")),
        *_bundle_graph_relation_items(bundle),
        *_copy_items(_graph_items(groups.get("maybe_relevant", []))),
    ]
    verification = _copy_items(groups.get("verification_hints"))
    warnings = _warning_items(bundle)
    return {
        "likely_change": _dedupe_dict_items(likely_change),
        "impact": _dedupe_dict_items(impact),
        "verification": _dedupe_dict_items(verification),
        "warnings": _dedupe_dict_items(warnings),
    }


def _copy_items(value: Any) -> list[dict[str, Any]]:
    return [dict(item) for item in value if isinstance(item, dict)] if isinstance(value, list) else []


def _graph_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [item for item in items if isinstance(item.get("source_ref"), dict) and str(item["source_ref"].get("kind") or "").startswith("graph")]


def _bundle_graph_relation_items(bundle: ContextBundle | None) -> list[dict[str, Any]]:
    if bundle is None:
        return []
    items: list[dict[str, Any]] = []
    for candidate in bundle.evidence:
        if candidate.source_ref.kind != "graph_relation":
            continue
        item = candidate.to_dict()
        item.setdefault("status", "current")
        items.append(item)
    return items


def _warning_items(bundle: ContextBundle | None) -> list[dict[str, Any]]:
    if bundle is None:
        return []
    warnings: list[dict[str, Any]] = []
    graph_completeness = bundle.completeness.get("graph_completeness") if isinstance(bundle.completeness.get("graph_completeness"), dict) else {}
    if graph_completeness and not graph_completeness.get("code_facts_complete", True):
        warnings.append(
            {
                "status": "warning",
                "code": "context_pack_graph_code_facts_incomplete",
                "selection_reason": f"Graph parse errors: {graph_completeness.get('parse_error_count', 0)}",
            }
        )
    return warnings


def _dedupe_dict_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    deduped: list[dict[str, Any]] = []
    seen: set[str] = set()
    for item in items:
        key = json.dumps(item.get("source_ref", item), ensure_ascii=False, sort_keys=True)
        if key in seen:
            continue
        seen.add(key)
        deduped.append(item)
    return deduped


def _markdown_item(item: dict[str, Any]) -> list[str]:
    ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
    record = item.get("record") if isinstance(item.get("record"), dict) else {}
    label = ref.get("path") or record.get("id") or item.get("record_id") or item.get("code") or "evidence"
    section = f" ({ref.get('section')})" if ref.get("section") else ""
    status = item.get("status") or record.get("status") or "current"
    if item.get("selection_reason"):
        reason = str(item.get("selection_reason") or "")
    elif isinstance(item.get("selection_reasons"), list):
        reason = "; ".join(str(reason) for reason in item.get("selection_reasons", []))
    else:
        reason = ""
    excerpt = item.get("excerpt") or item.get("claim") or record.get("claim") or record.get("summary") or item.get("selection_reason") or ""
    lines = [f"- `{label}`{section} [{status}]: {reason}".rstrip()]
    if ref.get("content_sha256"):
        lines.append(f"  digest: `{ref.get('content_sha256')}`")
    if excerpt:
        lines.append(f"  {' '.join(str(excerpt).split())[:360]}")
    graph_path = item.get("graph_path")
    if isinstance(graph_path, list) and graph_path:
        lines.append(f"  graph paths: {len(graph_path)}")
    return lines


def _pack_metrics(groups: dict[str, list[dict[str, Any]]], bundle: Any) -> dict[str, Any]:
    group_counts = {name: len(items) for name, items in sorted(groups.items())}
    group_estimated_tokens = {
        name: sum(estimate_tokens(str(item.get("excerpt") or _knowledge_text(item))) for item in items)
        for name, items in sorted(groups.items())
    }
    must_read_refs = _source_ref_keys(groups.get("must_read", []))
    verification_refs = _source_ref_keys(groups.get("verification", []) or groups.get("verification_hints", []))
    selection = bundle.selection if bundle is not None else {}
    return {
        "group_counts": group_counts,
        "group_estimated_tokens": group_estimated_tokens,
        "must_read_source_refs": must_read_refs,
        "verification_source_refs": verification_refs,
        "unique_must_read_source_count": len({(ref["kind"], ref["path"], ref["section"]) for ref in must_read_refs}),
        "unique_verification_source_count": len({(ref["kind"], ref["path"], ref["section"]) for ref in verification_refs}),
        "evidence_context_count": int(selection.get("evidence_count") or 0),
    }


def _graph_capability_warnings(completeness: dict[str, Any], graph_meta: dict[str, Any]) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    capabilities = completeness.get("capabilities") if isinstance(completeness.get("capabilities"), dict) else {}
    if completeness.get("status") == "partial":
        warnings.append(
            {
                "code": "context_pack_graph_partial",
                "message": f"Graph evidence is partial by capability: {capabilities}",
            }
        )
    provider_coverage = graph_meta.get("provider_coverage") if isinstance(graph_meta.get("provider_coverage"), dict) else {}
    incomplete_coverage = {
        name: value.get("status")
        for name, value in sorted(provider_coverage.items())
        if isinstance(value, dict) and value.get("status") != "complete"
    }
    if incomplete_coverage:
        warnings.append(
            {
                "code": "context_pack_graph_provider_coverage",
                "message": f"Graph semantic provider coverage is incomplete: {incomplete_coverage}.",
            }
        )
    return warnings


def _apply_render_budget(data: dict[str, Any], *, budget_tokens: int) -> str:
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    for items in groups.values():
        if not isinstance(items, list):
            continue
        for item in items:
            if isinstance(item, dict) and item.get("excerpt"):
                item["excerpt"] = _truncate_text(str(item["excerpt"]), 220)
    estimate = estimate_tokens(render_task_context_pack_markdown(data))
    if estimate <= budget_tokens:
        return "required_evidence_satisfied"
    removed = False
    for group in ("supporting_evidence", "likely_change", "impact", "verification", "must_read", "warnings"):
        items = groups.get(group)
        while isinstance(items, list) and estimate > budget_tokens:
            optional_index = next(
                (index for index in range(len(items) - 1, -1, -1) if items[index].get("requirement") != "required"),
                None,
            )
            if optional_index is None:
                break
            items.pop(optional_index)
            removed = True
            estimate = estimate_tokens(render_task_context_pack_markdown(data))
    if estimate <= budget_tokens:
        return "budget_reached" if removed else "required_evidence_satisfied"
    return "required_evidence_exceeds_budget"


def _source_ref_keys(items: list[dict[str, Any]]) -> list[dict[str, str]]:
    refs: list[dict[str, str]] = []
    for item in items:
        ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        refs.append(
            {
                "kind": str(ref.get("kind") or ""),
                "path": str(ref.get("path") or ""),
                "section": str(ref.get("section") or ""),
            }
        )
    return sorted(refs, key=lambda ref: (ref["kind"], ref["path"], ref["section"]))


def _knowledge_text(item: dict[str, Any]) -> str:
    record = item.get("record") if isinstance(item.get("record"), dict) else {}
    return "\n".join(str(record.get(key) or "") for key in ("title", "claim", "summary"))


def _pack_warnings(bundle: Any, task: Task) -> list[dict[str, str]]:
    warnings = [
        {
            "code": "context_pack_not_authoritative",
            "message": "task context pack uses structured Discovery as retrieval input; it does not set task scope or create knowledge",
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
    if bundle is None:
        return warnings
    completeness = bundle.completeness if isinstance(bundle.completeness, dict) else {}
    if completeness.get("graph_available") is False:
        warnings.append(
            {
                "code": "context_pack_graph_unavailable",
                "message": "context pack was built without a Graph snapshot; graph-backed file and symbol evidence may be incomplete",
            }
        )
    graph_completeness = completeness.get("graph_completeness") if isinstance(completeness.get("graph_completeness"), dict) else {}
    parse_error_count = int(graph_completeness.get("parse_error_count") or 0)
    if parse_error_count > 0 or graph_completeness.get("code_facts_complete") is False:
        warnings.append(
            {
                "code": "context_pack_graph_code_facts_incomplete",
                "message": f"Graph code facts are incomplete; parse_error_count={parse_error_count}",
            }
        )
    provider_failures = graph_completeness.get("provider_failures")
    if isinstance(provider_failures, list) and provider_failures:
        warnings.append(
            {
                "code": "context_pack_graph_provider_failures",
                "message": f"Graph provider failures are present; count={len(provider_failures)}",
            }
        )
    return warnings


def _pack_quality_warnings(groups: dict[str, list[dict[str, Any]]], task: Task) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if not _section(task, "Discovery").strip():
        warnings.append(
            {
                "code": "context_pack_no_structured_discovery",
                "message": "task has no structured Discovery yet; pack uses fallback PRD/README/context evidence and should be refreshed after file review",
            }
        )
    if not groups.get("must_read"):
        warnings.append(
            {
                "code": "context_pack_no_must_read",
                "message": "context pack selected no must_read evidence; inspect PRD/README/task docs directly before editing",
            }
        )
    return warnings

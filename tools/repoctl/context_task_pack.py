from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .context import (
    CONTEXT_GRAPH_FRESHNESS_WARNING_CODES,
    build_context_bundle,
    context_graph_freshness_warnings,
)
from .context_chunks import chunk_markdown_file, chunk_text_source
from .context_model import CONTEXT_SOURCE_KIND_VALUES, ContextBundle, ContextCandidate, ContextGraphSeedRef, ContextSourceRef
from .context_retrieval import rank_context_chunks
from .context_sources import context_graph_problems
from .document_roles import (
    AUTHORITY_DOCUMENT_ROLES,
    SOURCE_EXCLUDED_DOCUMENT_ROLES,
    DocumentRole,
    source_document_role,
)
from .graph import project_context_neighborhood
from .graph_model import GraphContextAnchor, GraphContextAnchorKind, digest_data
from .graph_store import graph_materialization_freshness, graph_stale_paths, load_materialized_graph
from .git import normalize_repo_path, repo_change_fingerprint_records, repo_changed_entries, repo_git_head
from .io import RepoctlError
from .language_profiles import collect_verification_hints
from .markdown import find_section
from .path_roles import PathRole, classify_path_role
from .repositories import RepoTarget
from .result_receipts import ContextResultRequest, GraphResultRequest, ResultProducer, parse_result_request
from .tasks import Problem, Task, normalize_task_id, repo_changes_since_task_start, resolve_task, task_discovery_result_selections, task_discovery_values


TASK_CONTEXT_PACK_SCHEMA_VERSION = 4
TASK_CONTEXT_PACK_MARKDOWN_ENVELOPE_SCHEMA_VERSION = 1
TASK_CONTEXT_PACK_MARKDOWN_ENVELOPE_PREFIX = "<!-- repoctl-context-pack-envelope "
COMPACT_SEED_NOTE_CHARS = 320
COMPACT_RESULT_REQUEST_CHARS = 240


@dataclass(frozen=True)
class ContextDocRef:
    declared_path: str
    workspace_path: str
    path: Path


@dataclass(frozen=True)
class _TaskContextPackInputs:
    task: Task
    discovery: dict[str, list[str]]
    reviewed: list[str]
    chosen: list[str]
    notes: list[str]
    selected_results: list[dict[str, str]]
    stage: str
    query: str
    snapshot: Any
    graph_meta: dict[str, Any]
    bundle: ContextBundle | None
    graph_seed_refs: list[ContextGraphSeedRef]
    explicit_candidates: list[ContextCandidate]
    required_candidates: list[ContextCandidate]
    discovery_candidates: list[ContextCandidate]
    fallback_candidates: list[ContextCandidate]
    verification_candidates: list[ContextCandidate]
    bundle_candidates: list[ContextCandidate]
    problems: list[Problem]
    meta: dict[str, Any]


def estimate_tokens(text: str) -> int:
    return max(1, (len(text) + 3) // 4)


def _sha256_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def _context_pack_source_digest_inputs(candidates: list[ContextCandidate]) -> list[dict[str, str]]:
    values = {
        (
            candidate.source_ref.kind,
            candidate.source_ref.path,
            candidate.source_ref.content_sha256,
        )
        for candidate in candidates
        if candidate.source_ref.path and candidate.source_ref.content_sha256
    }
    return [
        {"kind": kind, "path": path, "content_sha256": content_sha256}
        for kind, path, content_sha256 in sorted(values)
    ]


def _task_context_pack_repository_input(root: Path, *, target: RepoTarget, task: Task) -> dict[str, Any]:
    task_repo_id = str(task.frontmatter.get("repo_id") or "")
    if task_repo_id:
        delta = repo_changes_since_task_start(root, task.id)
        entries = list(delta.get("changes") or [])
        baseline_conflicts = sorted(str(value) for value in delta.get("baseline_conflicts", []) if str(value))
    else:
        entries, _entries_state = repo_changed_entries(root, target)
        baseline_conflicts = []
    records, state = repo_change_fingerprint_records(root, entries, target)
    head, head_state = repo_git_head(root, target)
    return {
        "repository": target.to_dict(),
        "head": head,
        "head_available": head_state.available,
        "head_reason": head_state.reason,
        "change_fingerprints_available": state.available,
        "change_fingerprint_reason": state.reason,
        "change_records": records,
        "baseline_conflicts": baseline_conflicts,
    }


def _task_context_pack_input_projection(
    root: Path,
    *,
    target: RepoTarget,
    task: Task,
    discovery: dict[str, list[str]],
    reviewed: list[str],
    chosen: list[str],
    notes: list[str],
    selected_results: list[dict[str, str]],
    graph_seed_refs: list[ContextGraphSeedRef],
    explicit_candidates: list[ContextCandidate],
    source_candidates: list[ContextCandidate],
    snapshot: Any,
) -> dict[str, Any]:
    graph_completeness = snapshot.completeness if snapshot is not None else {}
    return {
        "task_content_digest": _task_content_digest(task),
        "candidate_query": _task_seed_query(task),
        "reviewed_files": reviewed,
        "chosen_files": chosen,
        "notes": notes,
        "selected_result_evidence": selected_results,
        "graph_seed_refs": [seed.to_dict() for seed in graph_seed_refs],
        "context_docs": _context_doc_digest_inputs(explicit_candidates),
        "source_inputs": _context_pack_source_digest_inputs(source_candidates),
        "repository_state": _task_context_pack_repository_input(root, target=target, task=task),
        "graph_snapshot_digest": snapshot.snapshot_digest if snapshot is not None else "",
        "capability_matrix": graph_completeness.get("capabilities", {}),
    }


def _stabilize_render_estimate(data: dict[str, Any], *, budget_tokens: int) -> int:
    metrics = data.get("metrics") if isinstance(data.get("metrics"), dict) else {}
    data["metrics"] = metrics
    estimate = -1
    for _ in range(4):
        current = estimate_tokens(render_task_context_pack_markdown(data))
        data["budget"]["final_render_estimated_tokens"] = current
        metrics["requested_tokens"] = budget_tokens
        metrics["estimated_tokens"] = current
        if current == estimate:
            return current
        estimate = current
    return estimate


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


def _collect_task_context_pack_inputs(
    root: Path,
    *,
    target: RepoTarget,
    task_id: str,
    explain: bool = False,
) -> _TaskContextPackInputs:
    task = resolve_task(root, task_id)
    task_repo_id = str(task.frontmatter.get("repo_id") or "")
    if task_repo_id and task_repo_id != target.id:
        raise RepoctlError(
            f"task repository is {task_repo_id}, but Context Pack target is {target.id}",
            code="context_pack_repo_mismatch",
            path=task.rel_path,
        )
    discovery = task_discovery_values(task)
    chosen = _without_discovery_placeholders(discovery.get("Chosen files", []))
    reviewed = _without_discovery_placeholders(discovery.get("Candidate files reviewed", []))
    notes = _without_discovery_placeholders(discovery.get("Notes", []))
    selected_results = [selection.to_dict() for selection in task_discovery_result_selections(task)]
    stage = "scoped" if chosen else "bootstrap"
    query = _task_seed_query(task)
    problems: list[Problem] = []
    context_docs, context_doc_problems = _resolve_context_docs(root, task)
    problems.extend(context_doc_problems)
    snapshot, graph_problems, graph_meta = load_materialized_graph(root, target=target)
    bundle: ContextBundle | None = None
    meta: dict[str, Any] = {"repository": target.to_dict()}
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
    else:
        problems.extend(
            context_graph_problems(
                graph_problems,
                graph_available=snapshot is not None,
            )
        )
    explicit_candidates, explicit_problems = _explicit_context_doc_candidates(
        root,
        target=target,
        context_docs=context_docs,
    )
    problems.extend(explicit_problems)
    required_candidates, required_problems = _required_task_candidates(
        root,
        target=target,
        task=task,
        query=query,
    )
    problems.extend(required_problems)
    discovery_candidates, discovery_problems = _discovery_file_candidates(
        root,
        target=target,
        chosen=chosen,
        reviewed=reviewed,
    )
    problems.extend(discovery_problems)
    fallback_candidates, fallback_problems = _startup_fallback_candidates(
        root,
        target=target,
        task=task,
    )
    problems.extend(fallback_problems)
    if stage == "scoped":
        fallback_candidates = [
            candidate
            for candidate in fallback_candidates
            if candidate.source_ref.kind == "product_manifest"
        ]
    verification_candidates, verification_problems = _verification_hint_candidates(root, target=target)
    problems.extend(verification_problems)
    required_paths = {candidate.source_ref.path for candidate in required_candidates}
    mandatory_candidates = [
        candidate
        for candidate in explicit_candidates
        if candidate.source_ref.path not in required_paths
    ]
    bundle_candidates = _task_pack_bundle_candidates(
        bundle,
        excluded_paths={
            *(candidate.source_ref.path for candidate in required_candidates),
            *(candidate.source_ref.path for candidate in mandatory_candidates),
        },
        limit=8,
    )
    return _TaskContextPackInputs(
        task=task,
        discovery=discovery,
        reviewed=reviewed,
        chosen=chosen,
        notes=notes,
        selected_results=selected_results,
        stage=stage,
        query=query,
        snapshot=snapshot,
        graph_meta=graph_meta,
        bundle=bundle,
        graph_seed_refs=list(bundle.graph_seed_refs) if bundle is not None else [],
        explicit_candidates=explicit_candidates,
        required_candidates=required_candidates,
        discovery_candidates=discovery_candidates,
        fallback_candidates=fallback_candidates,
        verification_candidates=verification_candidates,
        bundle_candidates=bundle_candidates,
        problems=problems,
        meta=meta,
    )


def _task_context_pack_input_digest(root: Path, *, target: RepoTarget, inputs: _TaskContextPackInputs) -> str:
    return digest_data(
        _task_context_pack_input_projection(
            root,
            target=target,
            task=inputs.task,
            discovery=inputs.discovery,
            reviewed=inputs.reviewed,
            chosen=inputs.chosen,
            notes=inputs.notes,
            selected_results=inputs.selected_results,
            graph_seed_refs=inputs.graph_seed_refs,
            explicit_candidates=inputs.explicit_candidates,
            source_candidates=_dedupe_candidates(
                [
                    *inputs.required_candidates,
                    *inputs.explicit_candidates,
                    *inputs.discovery_candidates,
                    *inputs.fallback_candidates,
                    *inputs.verification_candidates,
                    *inputs.bundle_candidates,
                ]
            ),
            snapshot=inputs.snapshot,
        )
    )


def current_task_context_pack_input_digest(
    root: Path,
    *,
    target: RepoTarget,
    task_id: str,
) -> tuple[str, list[Problem]]:
    inputs = _collect_task_context_pack_inputs(root, target=target, task_id=task_id)
    return _task_context_pack_input_digest(root, target=target, inputs=inputs), inputs.problems


def build_task_context_pack(root: Path, *, target: RepoTarget, task_id: str, budget_tokens: int = 1500, explain: bool = False) -> tuple[dict[str, Any], list[Problem], dict[str, Any]]:
    inputs = _collect_task_context_pack_inputs(
        root,
        target=target,
        task_id=task_id,
        explain=explain,
    )
    task = inputs.task
    chosen = inputs.chosen
    reviewed = inputs.reviewed
    notes = inputs.notes
    selected_results = inputs.selected_results
    chosen_paths = {normalize_repo_path(path) for path in chosen}
    reviewed_paths = {normalize_repo_path(path) for path in reviewed} - chosen_paths
    stage = inputs.stage
    query = inputs.query
    bundle = inputs.bundle
    problems = list(inputs.problems)
    meta = inputs.meta
    snapshot = inputs.snapshot
    graph_meta = inputs.graph_meta
    graph_freshness, graph_freshness_problems = _task_pack_graph_freshness(
        root,
        target=target,
        snapshot=snapshot,
        bundle=bundle,
    )
    task_graph_evidence = (
        _direct_task_graph_evidence(
            snapshot,
            target=target,
            chosen=chosen,
            freshness=graph_freshness,
        )
        if stage == "scoped" and snapshot is not None
        else []
    )
    explicit_candidates = inputs.explicit_candidates
    required_candidates = inputs.required_candidates
    required_paths = {
        candidate.source_ref.path
        for candidate in required_candidates
    }
    mandatory_candidates = [
        candidate
        for candidate in explicit_candidates
        if candidate.source_ref.path not in required_paths
    ]
    discovery_candidates = inputs.discovery_candidates
    fallback_candidates = inputs.fallback_candidates
    verification_candidates = inputs.verification_candidates
    bundle_candidates = inputs.bundle_candidates
    context_candidates = _dedupe_candidates(
        [*required_candidates, *mandatory_candidates, *discovery_candidates, *fallback_candidates, *verification_candidates, *bundle_candidates]
    )
    groups = _group_candidates(context_candidates, repository_path=target.display_path)
    groups["task_graph_evidence"] = task_graph_evidence
    groups.update(
        _agent_pack_groups(
            groups,
            bundle,
            graph_freshness=graph_freshness,
            graph_freshness_problems=graph_freshness_problems,
        )
    )
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
            *(candidate.source_ref.path for candidate in required_candidates),
            *(candidate.source_ref.path for candidate in mandatory_candidates),
            *chosen_paths,
        },
    )
    graph_completeness = snapshot.completeness if snapshot is not None else {}
    warnings = [
        *_pack_warnings(
            bundle,
            task,
            graph_freshness=graph_freshness,
            graph_freshness_problems=graph_freshness_problems,
        ),
        *_graph_capability_warnings(graph_completeness, graph_meta),
        *_pack_quality_warnings(groups, task),
    ]
    input_digest = _task_context_pack_input_digest(root, target=target, inputs=inputs)
    data = {
        "schema": "repoctl.context.task_pack",
        "schema_version": TASK_CONTEXT_PACK_SCHEMA_VERSION,
        "authoritative": False,
        "stage": stage,
        "render_projection": "full",
        "input_digest": input_digest,
        "task": {
            "id": task.id,
            "path": task.rel_path,
            "status": task.status,
            "repo_id": str(task.frontmatter.get("repo_id") or target.id),
            "area": str(task.frontmatter.get("area") or ""),
            "content_digest": _task_content_digest(task),
        },
        "seed": {
            "source": "current_discovery_episode",
            "query": query,
            "notes": notes,
            "selected_result_evidence": selected_results,
            "graph_seed_refs": [seed.to_dict() for seed in inputs.graph_seed_refs],
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
    data["stop_reason"] = "required_evidence_satisfied"
    data["metrics"]["requested_tokens"] = budget_tokens
    data["metrics"]["estimated_tokens"] = budget_tokens
    data["pack_digest"] = "sha256:" + "0" * 64
    stop_reason = _apply_render_budget(data, budget_tokens=budget_tokens)
    data["stop_reason"] = stop_reason
    data["metrics"] = _pack_metrics(data["groups"], bundle)
    final_estimate = _stabilize_render_estimate(data, budget_tokens=budget_tokens)
    if final_estimate > budget_tokens and data.get("render_projection") != "required_reference_manifest":
        data["render_projection"] = "required_reference_manifest"
        data["stop_reason"] = "budget_reached"
        final_estimate = _stabilize_render_estimate(data, budget_tokens=budget_tokens)
    if final_estimate > budget_tokens:
        data["stop_reason"] = "required_evidence_exceeds_budget"
        _stabilize_render_estimate(data, budget_tokens=budget_tokens)
        problems.append(
            Problem(
                "error",
                "context_pack_required_evidence_exceeds_budget",
                "required source references cannot fit within the requested context pack budget",
                task.rel_path,
            )
        )
    data.pop("pack_digest", None)
    data["pack_digest"] = digest_data(data)
    return data, problems, meta


def render_task_context_pack_markdown(data: dict[str, Any]) -> str:
    body = _render_task_context_pack_markdown_body(data)
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    envelope = {
        "schema": "repoctl.context.task_pack.markdown_envelope",
        "schema_version": TASK_CONTEXT_PACK_MARKDOWN_ENVELOPE_SCHEMA_VERSION,
        "task_pack_schema_version": int(data.get("schema_version") or 0),
        "task_id": str(task.get("id") or ""),
        "repo_id": str(task.get("repo_id") or ""),
        "input_digest": str(data.get("input_digest") or ""),
        "body_sha256": _sha256_bytes(body.encode("utf-8")),
    }
    encoded = json.dumps(envelope, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
    return f"{TASK_CONTEXT_PACK_MARKDOWN_ENVELOPE_PREFIX}{encoded} -->\n{body}"


def _render_task_context_pack_markdown_body(data: dict[str, Any]) -> str:
    if data.get("render_projection") == "required_reference_manifest":
        return _render_required_reference_manifest(data)
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
    lines.extend(_graph_seed_manifest_lines(seed))
    notes = seed.get("notes") if isinstance(seed.get("notes"), list) else []
    if notes:
        lines.extend(["## Current Discovery Notes", ""])
        lines.extend(f"- {str(note)[:320]}" for note in notes[:4])
        lines.append("")
    selected_results = seed.get("selected_result_evidence") if isinstance(seed.get("selected_result_evidence"), list) else []
    if selected_results:
        lines.extend(["## Selected Result Provenance", ""])
        for selection in selected_results[:8]:
            if not isinstance(selection, dict):
                continue
            lines.append(
                f"- `{selection.get('producer', '')}` `{selection.get('authority', '')}` "
                f"`{selection.get('ref', '')}` from `{selection.get('result_id', '')}`"
            )
        lines.append("")
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


def _render_required_reference_manifest(data: dict[str, Any]) -> str:
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    seed = data.get("seed") if isinstance(data.get("seed"), dict) else {}
    groups = data.get("groups") if isinstance(data.get("groups"), dict) else {}
    budget = data.get("budget") if isinstance(data.get("budget"), dict) else {}
    lines = [
        "# Agent Context Pack",
        "",
        f"- Task: `{task.get('id', '')}`",
        f"- Repository: `{task.get('repo_id', '')}`",
        f"- Stage: `{data.get('stage', '')}`",
        f"- Stop reason: `{data.get('stop_reason', '')}`",
        f"- Maximum estimated tokens: {budget.get('maximum_estimated_tokens', 0)}",
        "",
        "Open every source in the required sections below directly; full evidence and digests remain in JSON.",
        "",
    ]
    lines.extend(_graph_seed_manifest_lines(seed))
    sections = (
        ("must_read", "Read First"),
        ("edit_candidates", "Active Edit Candidates"),
        ("supporting_evidence", "Required Supporting Evidence"),
        ("likely_change", "Required Likely Change Surface"),
        ("impact", "Required Impact Surface"),
        ("verification", "Required Verification Sources"),
        ("warnings", "Required Warnings"),
    )
    seen: set[tuple[str, str, str]] = set()
    for group, title in sections:
        refs: list[dict[str, Any]] = []
        items = groups.get(group)
        if not isinstance(items, list):
            continue
        for item in items:
            if not isinstance(item, dict) or item.get("requirement") != "required":
                continue
            ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
            key = (str(ref.get("kind") or ""), str(ref.get("path") or ""), str(ref.get("section") or ""))
            if not key[1] or key in seen:
                continue
            seen.add(key)
            refs.append(ref)
        if not refs:
            continue
        lines.extend([f"## {title}", ""])
        for ref in refs:
            path = str(ref.get("path") or "")
            section_name = str(ref.get("section") or "")
            section = f" ({section_name})" if section_name and section_name != Path(path).name else ""
            lines.append(f"- `{path}`{section}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def _graph_seed_manifest_lines(seed: dict[str, Any]) -> list[str]:
    refs = seed.get("graph_seed_refs") if isinstance(seed.get("graph_seed_refs"), list) else []
    if not refs:
        return []
    lines = [
        "## Graph Seed Identities",
        "",
        "Graph seeds are ranked traversal evidence only; they do not define edit scope or authority. Inspect source before choosing one.",
        "",
    ]
    for selection_rank, ref in enumerate(refs, start=1):
        if not isinstance(ref, dict):
            continue
        continuation = ref.get("continuation") if isinstance(ref.get("continuation"), dict) else {}
        selector = continuation.get("selector") if isinstance(continuation.get("selector"), dict) else {}
        selector_text = json.dumps(selector, ensure_ascii=True, separators=(",", ":"), sort_keys=True)
        lines.append(
            f"- Rank {selection_rank}: `{ref.get('path', '')}`; provenance `{ref.get('provenance', '')}`; "
            f"strength `{ref.get('anchor_strength', '')}`; identity `{ref.get('identity_digest', '')}`; "
            f"continue `{selector_text}`"
        )
    lines.append("")
    return lines


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
    reference_only = data.get("render_projection") == "required_reference_manifest"
    compact_groups = {
        group: [
            _compact_pack_item(
                item,
                excerpt_chars=excerpt_chars,
                reference_only=reference_only and item.get("requirement") == "required",
            )
            for item in _compact_group_items(groups.get(group) or [], group)
        ]
        for group in canonical_groups
    }
    group_counts = {name: len(items) for name, items in sorted(groups.items()) if isinstance(items, list)}
    compact = {
        "schema": data.get("schema", "repoctl.context.task_pack"),
        "schema_version": data.get("schema_version", 1),
        "view": "compact",
        "authoritative": data.get("authoritative", False),
        "stage": data.get("stage", ""),
        "render_projection": data.get("render_projection", "full"),
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
    return _limited_group_items(items, limit=COMPACT_GROUP_LIMITS[group])


def _limited_group_items(items: list[Any], *, limit: int) -> list[dict[str, Any]]:
    filtered = [item for item in items if isinstance(item, dict)]
    required = [item for item in filtered if item.get("requirement") == "required"]
    optional = [item for item in filtered if item.get("requirement") != "required"]
    return [*required, *optional[: max(0, limit - len(required))]]


def _compact_seed(seed: dict[str, Any]) -> dict[str, Any]:
    query = str(seed.get("query") or "")
    compact = {
        "source": seed.get("source", ""),
        "used_sections": seed.get("used_sections", []),
        "notes": [
            _truncate_text(str(note), COMPACT_SEED_NOTE_CHARS)
            for note in seed.get("notes", [])
            if str(note)
        ][:4],
        "selected_result_evidence": [
            _compact_selected_result_evidence(item)
            for item in seed.get("selected_result_evidence", [])
            if isinstance(item, dict)
        ][:8],
        "graph_seed_refs": [
            item
            for item in seed.get("graph_seed_refs", [])
            if isinstance(item, dict)
        ],
    }
    if query.strip():
        compact["query_preview"] = _truncate_text(query, 240)
    return compact


def _compact_selected_result_evidence(item: dict[str, Any]) -> dict[str, Any]:
    compact = {
        key: item[key]
        for key in (
            "schema_version",
            "producer",
            "result_id",
            "episode_id",
            "authority",
            "ref",
        )
        if key in item
    }
    request = item.get("request")
    if not isinstance(request, dict):
        return compact
    producer = ResultProducer(item.get("producer"))
    parsed = parse_result_request(producer, request)
    compact["request_digest"] = digest_data(request)
    if isinstance(parsed, ContextResultRequest):
        compact["request_preview"] = {
            "kind": "context_query",
            "query": _truncate_text(parsed.query, COMPACT_RESULT_REQUEST_CHARS),
            "mode": parsed.mode,
        }
    elif isinstance(parsed, GraphResultRequest):
        compact["request_preview"] = {
            "kind": "graph_query",
            "selector": {
                key: _truncate_text(value, COMPACT_RESULT_REQUEST_CHARS)
                if isinstance(value, str)
                else value
                for key, value in parsed.selector.items()
            },
        }
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


def _compact_pack_item(item: dict[str, Any], *, excerpt_chars: int, reference_only: bool = False) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    if reference_only:
        for key in ("document_role", "requirement"):
            if item.get(key):
                compact[key] = item[key]
        ref = item.get("source_ref")
        if isinstance(ref, dict):
            compact["source_ref"] = {
                key: ref[key]
                for key in ("kind", "path", "section")
                if ref.get(key)
            }
        compact["projection"] = "reference_only"
        return compact
    for key in ("status", "record_id", "code", "document_role", "selection_reason", "requirement"):
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
    queries = _without_discovery_placeholders(discovery.get("Candidate query", []))
    return queries[-1] if queries else ""


def _without_discovery_placeholders(values: list[str]) -> list[str]:
    placeholders = {"none", "none yet", "n/a", "na", "tbd", "todo", "pending", "-"}
    return [value for value in values if value.strip().strip("`").lower() not in placeholders]


def _required_task_candidates(
    root: Path,
    *,
    target: RepoTarget,
    task: Task,
    query: str,
) -> tuple[list[ContextCandidate], list[Problem]]:
    candidates: list[ContextCandidate] = []
    problems: list[Problem] = []
    required_paths = ["AGENTS.md", task.rel_path]
    canonical_prd = root / "docs/PRD.md"
    if canonical_prd.is_file():
        required_paths.insert(1, "docs/PRD.md")
    else:
        split_prd = _select_split_prd_path(root, query=query, repository_path=target.display_path)
        if split_prd:
            required_paths.insert(1, split_prd)
        else:
            problems.append(
                Problem(
                    "warning",
                    "context_pack_product_authority_missing",
                    "required product authority is missing; provide docs/PRD.md or a document under docs/prd/",
                    "docs/PRD.md",
                )
            )
    for rel_path in required_paths:
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
                document_role=source_document_role(
                    kind=chunk.source_ref.kind,
                    path=chunk.source_ref.path,
                    repository_path=target.display_path,
                    assigned=chunk.document_role,
                ),
            )
        )
    return candidates, problems


def _select_split_prd_path(
    root: Path,
    *,
    query: str,
    repository_path: str,
) -> str:
    chunks = []
    for path in sorted((root / "docs/prd").glob("**/*.md")):
        if not path.is_file():
            continue
        try:
            chunks.extend(
                chunk
                for chunk in chunk_markdown_file(root, path)
                if chunk.document_role == DocumentRole.PRODUCT_AUTHORITY
            )
        except (OSError, UnicodeDecodeError):
            continue
    ranked = rank_context_chunks(
        query,
        chunks,
        limit=1,
        repository_path=repository_path,
    ) if query else []
    if ranked:
        return ranked[0].source_ref.path
    paths = sorted({chunk.source_ref.path for chunk in chunks})
    for preferred in ("docs/prd/README.md", "docs/prd/INDEX.md"):
        if preferred in paths:
            return preferred
    return paths[0] if paths else ""


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
                    document_role=source_document_role(
                        kind=chunk.source_ref.kind,
                        path=chunk.source_ref.path,
                        repository_path=target.display_path,
                        assigned=chunk.document_role,
                    ),
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


def _context_doc_digest_inputs(candidates: list[ContextCandidate]) -> list[dict[str, str]]:
    return [
        {
            "path": candidate.source_ref.path,
            "content_sha256": candidate.source_ref.content_sha256,
        }
        for candidate in candidates
    ]


def _task_content_digest(task: Task) -> str:
    try:
        return digest_data({"content": task.path.read_text(encoding="utf-8")})
    except OSError:
        return ""


CONTEXT_DOC_RE = re.compile(r"`([^`]+)`")


def _resolve_context_docs(root: Path, task: Task) -> tuple[list[ContextDocRef], list[Problem]]:
    context_docs: list[ContextDocRef] = []
    problems: list[Problem] = []
    seen_declared: set[str] = set()
    seen_workspace: set[str] = set()
    resolved_root = root.resolve()
    section = _section(task, "Context Docs")
    for match in CONTEXT_DOC_RE.finditer(section):
        declared_path = match.group(1).strip()
        if not declared_path or declared_path in seen_declared:
            continue
        seen_declared.add(declared_path)
        normalized = normalize_repo_path(declared_path)
        if not normalized or normalized != declared_path or "\\" in declared_path or Path(declared_path).is_absolute():
            problems.append(
                Problem(
                    "warning",
                    "context_pack_context_doc_invalid_path",
                    "task Context Docs paths must be normalized workspace-relative paths",
                    declared_path,
                )
            )
            continue
        path = root / normalized
        try:
            resolved = path.resolve()
            workspace_path = resolved.relative_to(resolved_root).as_posix()
        except (OSError, RuntimeError, ValueError):
            problems.append(
                Problem(
                    "error",
                    "context_pack_context_doc_outside_workspace",
                    "task Context Docs path must stay inside workspace",
                    declared_path,
                )
            )
            continue
        if workspace_path in seen_workspace:
            continue
        seen_workspace.add(workspace_path)
        context_docs.append(
            ContextDocRef(
                declared_path=declared_path,
                workspace_path=workspace_path,
                path=resolved,
            )
        )
    return context_docs, problems


def _explicit_context_doc_candidates(
    root: Path,
    *,
    target: RepoTarget,
    context_docs: list[ContextDocRef],
) -> tuple[list[ContextCandidate], list[Problem]]:
    candidates: list[ContextCandidate] = []
    problems: list[Problem] = []
    for context_doc in context_docs:
        rel_path = context_doc.workspace_path
        path = context_doc.path
        if not path.is_file():
            problems.append(Problem("warning", "context_pack_context_doc_missing", "task Context Docs path is missing", rel_path))
            continue
        document_role = source_document_role(
            kind="document",
            path=rel_path,
            repository_path=target.display_path,
        )
        if document_role in SOURCE_EXCLUDED_DOCUMENT_ROLES:
            problems.append(
                Problem(
                    "warning",
                    "context_pack_generated_view_excluded",
                    "generated Knowledge views are non-authoritative and cannot be used as Context Docs",
                    rel_path,
                )
            )
            continue
        try:
            chunk = chunk_text_source(
                root,
                rel_path,
                path.read_text(encoding="utf-8"),
                kind="document",
                section=path.name,
                document_role=document_role,
            )
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
                document_role=chunk.document_role,
            )
        )
    return candidates, problems


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
                    document_role=source_document_role(
                        kind=chunk.source_ref.kind,
                        path=chunk.source_ref.path,
                        repository_path=target.display_path,
                        assigned=chunk.document_role,
                    ),
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


def _task_pack_graph_freshness(
    root: Path,
    *,
    target: RepoTarget,
    snapshot: Any,
    bundle: ContextBundle | None,
) -> tuple[dict[str, Any], list[Problem]]:
    if bundle is not None:
        freshness = bundle.completeness.get("graph_freshness")
        if isinstance(freshness, dict) and freshness.get("status"):
            return freshness, []
    if snapshot is None:
        return {"status": "missing", "changed_paths": []}, []
    freshness, problems = graph_materialization_freshness(
        root,
        target=target,
        snapshot=snapshot,
    )
    return freshness, problems


def _direct_task_graph_evidence(
    snapshot: Any,
    *,
    target: RepoTarget,
    chosen: list[str],
    freshness: dict[str, Any],
) -> list[dict[str, Any]]:
    freshness_status = str(freshness.get("status") or "")
    if freshness_status not in {"current", "stale"}:
        return []
    stale_paths = {
        normalized
        for path in graph_stale_paths(freshness)
        if (normalized := normalize_repo_path(path))
    }
    prefix = f"{target.display_path.rstrip('/')}/"
    seed_paths: list[str] = []
    for workspace_path in chosen:
        normalized = normalize_repo_path(workspace_path)
        if not normalized.startswith(prefix):
            continue
        repo_path = normalize_repo_path(normalized[len(prefix) :])
        if repo_path and repo_path not in stale_paths and repo_path not in seed_paths:
            seed_paths.append(repo_path)
    if not seed_paths:
        return []
    projection = project_context_neighborhood(
        snapshot,
        anchors=[GraphContextAnchor(kind=GraphContextAnchorKind.FILE, path=path) for path in seed_paths],
        mode="file_impact",
    )
    relations = projection.get("relations") if isinstance(projection.get("relations"), list) else []
    return _dedupe_dict_items([
        _graph_relation_item(relation, reason="provider-confirmed relation from active Chosen files")
        for relation in relations
        if isinstance(relation, dict)
        and normalize_repo_path(str(relation.get("from_path") or "")) not in stale_paths
        and normalize_repo_path(str(relation.get("to_path") or "")) not in stale_paths
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
    seen: set[tuple[str, str, str, str, int, int, str]] = set()
    for candidate in candidates:
        key = candidate.source_ref.key()
        if key in seen:
            continue
        seen.add(key)
        deduped.append(candidate)
    return deduped


def _task_pack_bundle_candidates(
    bundle: ContextBundle | None,
    *,
    excluded_paths: set[str],
    limit: int,
) -> list[ContextCandidate]:
    if bundle is None or limit <= 0:
        return []
    allowed_kinds = {
        *CONTEXT_SOURCE_KIND_VALUES,
        "product_manifest",
        "verification_hint",
        "graph_relation",
    }
    eligible = [
        candidate
        for candidate in bundle.evidence
        if candidate.source_ref.path not in excluded_paths
        and (
            candidate.source_ref.kind in allowed_kinds
            or candidate.document_role in AUTHORITY_DOCUMENT_ROLES
            or candidate.document_role == DocumentRole.PROCEDURE
        )
    ]
    reserved: list[ContextCandidate] = []
    authority = next(
        (candidate for candidate in eligible if candidate.document_role in AUTHORITY_DOCUMENT_ROLES),
        None,
    )
    procedure = next(
        (candidate for candidate in eligible if candidate.document_role == DocumentRole.PROCEDURE),
        None,
    )
    if authority is not None:
        reserved.append(authority)
    if procedure is not None:
        reserved.append(procedure)
    fill = [candidate for candidate in eligible if candidate.document_role == DocumentRole.UNSPECIFIED]
    selected: list[ContextCandidate] = []
    seen: set[tuple[Any, ...]] = set()
    for candidate in [*reserved, *fill]:
        key: tuple[Any, ...]
        if candidate.source_ref.kind == "graph_relation":
            key = ("source_ref", *candidate.source_ref.key())
        else:
            key = ("source_path", candidate.source_ref.kind, candidate.source_ref.path)
        if key in seen:
            continue
        seen.add(key)
        selected.append(candidate)
        if len(selected) >= limit:
            break
    return selected


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


def _valid_sha256(value: str) -> bool:
    return bool(re.fullmatch(r"sha256:[0-9a-f]{64}", value))


def _read_context_pack_markdown_metadata(path: Path, raw: bytes) -> tuple[dict[str, Any], list[Problem]]:
    problems: list[Problem] = []
    try:
        text = raw.decode("utf-8")
    except UnicodeDecodeError as exc:
        return {}, [Problem("error", "context_pack_binding_invalid", str(exc), path.as_posix())]
    first_line, separator, body = text.partition("\n")
    if not separator or not first_line.startswith(TASK_CONTEXT_PACK_MARKDOWN_ENVELOPE_PREFIX) or not first_line.endswith(" -->"):
        return {}, [
            Problem(
                "error",
                "context_pack_binding_metadata_missing",
                "legacy Markdown Context Pack has no machine-verifiable binding envelope",
                path.as_posix(),
            )
        ]
    encoded = first_line[len(TASK_CONTEXT_PACK_MARKDOWN_ENVELOPE_PREFIX) : -4]
    try:
        envelope = json.loads(encoded)
    except json.JSONDecodeError as exc:
        return {}, [Problem("error", "context_pack_binding_invalid", str(exc), path.as_posix())]
    expected_keys = {
        "schema",
        "schema_version",
        "task_pack_schema_version",
        "task_id",
        "repo_id",
        "input_digest",
        "body_sha256",
    }
    if not isinstance(envelope, dict) or set(envelope) != expected_keys:
        return {}, [Problem("error", "context_pack_binding_invalid", "Markdown Context Pack envelope has invalid fields", path.as_posix())]
    if (
        envelope.get("schema") != "repoctl.context.task_pack.markdown_envelope"
        or type(envelope.get("schema_version")) is not int
        or envelope.get("schema_version") != TASK_CONTEXT_PACK_MARKDOWN_ENVELOPE_SCHEMA_VERSION
        or type(envelope.get("task_pack_schema_version")) is not int
        or envelope.get("task_pack_schema_version") != TASK_CONTEXT_PACK_SCHEMA_VERSION
    ):
        problems.append(Problem("error", "context_pack_binding_invalid", "Markdown Context Pack envelope has invalid schema", path.as_posix()))
    for key in ("input_digest", "body_sha256"):
        if not _valid_sha256(str(envelope.get(key) or "")):
            problems.append(Problem("error", "context_pack_binding_invalid", f"Markdown Context Pack has invalid {key}", path.as_posix()))
    if str(envelope.get("body_sha256") or "") != _sha256_bytes(body.encode("utf-8")):
        problems.append(Problem("error", "context_pack_binding_invalid", "Markdown Context Pack body digest does not match", path.as_posix()))
    return {
        "task_id": str(envelope.get("task_id") or ""),
        "repo_id": str(envelope.get("repo_id") or ""),
        "input_digest": str(envelope.get("input_digest") or ""),
    }, problems


def _read_bindable_context_pack(path: Path) -> tuple[dict[str, Any], list[Problem]]:
    if not path.is_file():
        return {}, [Problem("error", "context_pack_missing", "bound Context Pack is missing", path.as_posix())]
    try:
        raw = path.read_bytes()
    except OSError as exc:
        return {}, [Problem("error", "context_pack_invalid", str(exc), path.as_posix())]
    if path.suffix.lower() == ".md" or raw.startswith(TASK_CONTEXT_PACK_MARKDOWN_ENVELOPE_PREFIX.encode("utf-8")):
        metadata, problems = _read_context_pack_markdown_metadata(path, raw)
        if metadata:
            metadata["artifact_sha256"] = _sha256_bytes(raw)
        return metadata, problems
    problems: list[Problem] = []
    data = _read_pack_artifact(path, problems, label="bound")
    if not data:
        return {}, problems
    if (
        data.get("schema") != "repoctl.context.task_pack"
        or type(data.get("schema_version")) is not int
        or data.get("schema_version") != TASK_CONTEXT_PACK_SCHEMA_VERSION
    ):
        problems.append(Problem("error", "context_pack_binding_invalid", "active Context Pack requires the current task-pack schema", path.as_posix()))
    task = data.get("task") if isinstance(data.get("task"), dict) else {}
    return {
        "task_id": str(task.get("id") or ""),
        "repo_id": str(task.get("repo_id") or ""),
        "input_digest": str(data.get("input_digest") or ""),
        "artifact_sha256": _sha256_bytes(raw),
    }, problems


def inspect_task_context_pack_binding(
    root: Path,
    *,
    target: RepoTarget,
    task_id: str,
    binding: dict[str, str] | None,
) -> dict[str, Any]:
    if binding is None:
        return {"status": "not_bound", "active": False, "path": "", "reason_codes": []}
    path_value = str(binding.get("path") or "")
    candidate = Path(path_value)
    if not path_value or candidate.is_absolute() or ".." in candidate.parts or "\\" in path_value:
        return {"status": "invalid", "active": False, "path": path_value, "reason_codes": ["pack_path_invalid"]}
    path = root / candidate
    if not path.is_file():
        return {"status": "missing", "active": False, "path": path_value, "reason_codes": ["pack_missing"]}
    metadata, metadata_problems = _read_bindable_context_pack(path)
    if any(problem.severity == "error" for problem in metadata_problems):
        return {
            "status": "invalid",
            "active": False,
            "path": path_value,
            "reason_codes": sorted({problem.code for problem in metadata_problems}),
        }
    if metadata.get("task_id") != normalize_task_id(task_id) or metadata.get("repo_id") != target.id:
        return {"status": "invalid", "active": False, "path": path_value, "reason_codes": ["pack_identity_mismatch"]}
    reason_codes: list[str] = []
    if str(metadata.get("artifact_sha256") or "") != str(binding.get("artifact_sha256") or ""):
        reason_codes.append("pack_artifact_changed")
    if str(metadata.get("input_digest") or "") != str(binding.get("input_digest") or ""):
        reason_codes.append("pack_identity_changed")
    current_input_digest, input_problems = current_task_context_pack_input_digest(
        root,
        target=target,
        task_id=task_id,
    )
    input_errors = [problem for problem in input_problems if problem.severity == "error"]
    if current_input_digest != str(binding.get("input_digest") or ""):
        reason_codes.append("pack_inputs_changed")
    if input_errors and not reason_codes:
        return {
            "status": "unknown",
            "active": False,
            "path": path_value,
            "reason_codes": ["pack_input_observation_unavailable"],
            "recorded_input_digest": str(binding.get("input_digest") or ""),
            "current_input_digest": "",
        }
    reason_codes = list(dict.fromkeys(reason_codes))
    return {
        "status": "stale" if reason_codes else "current",
        "active": not reason_codes,
        "path": path_value,
        "reason_codes": reason_codes,
        "recorded_input_digest": str(binding.get("input_digest") or ""),
        "current_input_digest": current_input_digest,
    }


def prepare_task_context_pack_binding(
    root: Path,
    *,
    target: RepoTarget,
    task_id: str,
    path: Path,
) -> tuple[dict[str, str], list[Problem]]:
    try:
        relative = path.resolve().relative_to(root.resolve()).as_posix()
    except (OSError, ValueError):
        return {}, [Problem("error", "context_pack_binding_path_invalid", "Context Pack binding path must stay inside the workspace", path.as_posix())]
    metadata, problems = _read_bindable_context_pack(root / relative)
    if any(problem.severity == "error" for problem in problems):
        return {}, problems
    if metadata.get("task_id") != normalize_task_id(task_id) or metadata.get("repo_id") != target.id:
        return {}, [Problem("error", "context_pack_binding_identity_mismatch", "Context Pack task or repository identity does not match", relative)]
    binding = {
        "path": relative,
        "artifact_sha256": str(metadata.get("artifact_sha256") or ""),
        "input_digest": str(metadata.get("input_digest") or ""),
    }
    observation = inspect_task_context_pack_binding(
        root,
        target=target,
        task_id=task_id,
        binding=binding,
    )
    if observation.get("status") != "current":
        return {}, [
            Problem(
                "error",
                f"context_pack_{observation.get('status', 'invalid')}",
                "Context Pack must be current and verifiable before binding",
                relative,
            )
        ]
    return binding, []


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


def _group_candidates(candidates: list[ContextCandidate], *, repository_path: str) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {"must_read": [], "maybe_relevant": [], "verification_hints": []}
    for candidate in candidates:
        ref = candidate.source_ref
        item = candidate.to_dict()
        document_role = source_document_role(
            kind=ref.kind,
            path=ref.path,
            repository_path=repository_path,
            assigned=candidate.document_role,
        )
        if candidate.score_breakdown.get("required_task_context") or candidate.score_breakdown.get("explicit_context_doc"):
            groups["must_read"].append(item)
        elif candidate.score_breakdown.get("structured_discovery"):
            continue
        elif candidate.score_breakdown.get("startup_fallback"):
            groups["must_read"].append(item)
        elif document_role in AUTHORITY_DOCUMENT_ROLES or document_role == DocumentRole.PROCEDURE:
            groups["must_read"].append(item)
        elif ref.kind == "document" and document_role != DocumentRole.UNSPECIFIED:
            groups["maybe_relevant"].append(item)
        elif ref.kind == "verification_hint" or "Verification" in ref.section or classify_path_role(ref.path, repository_path=repository_path) in {PathRole.TEST, PathRole.WORKFLOW}:
            groups["verification_hints"].append(item)
        else:
            groups["maybe_relevant"].append(item)
    return groups


def _agent_pack_groups(
    groups: dict[str, list[dict[str, Any]]],
    bundle: ContextBundle | None,
    *,
    graph_freshness: dict[str, Any],
    graph_freshness_problems: list[Problem],
) -> dict[str, list[dict[str, Any]]]:
    likely_change = _copy_items(groups.get("maybe_relevant"))
    impact = [
        *_copy_items(groups.get("task_graph_evidence")),
        *_bundle_graph_relation_items(bundle),
        *_copy_items(_graph_items(groups.get("maybe_relevant", []))),
    ]
    verification = _copy_items(groups.get("verification_hints"))
    warnings = _warning_items(
        bundle,
        graph_freshness=graph_freshness,
        graph_freshness_problems=graph_freshness_problems,
    )
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


def _warning_items(
    bundle: ContextBundle | None,
    *,
    graph_freshness: dict[str, Any],
    graph_freshness_problems: list[Problem],
) -> list[dict[str, Any]]:
    warnings: list[dict[str, Any]] = []
    graph_completeness = (
        bundle.completeness.get("graph_completeness")
        if bundle is not None and isinstance(bundle.completeness.get("graph_completeness"), dict)
        else {}
    )
    if graph_completeness and not graph_completeness.get("code_facts_complete", True):
        warnings.append(
            {
                "status": "warning",
                "code": "context_pack_graph_code_facts_incomplete",
                "selection_reason": f"Graph parse errors: {graph_completeness.get('parse_error_count', 0)}",
            }
        )
    for warning in _task_pack_freshness_warnings(
        bundle,
        graph_freshness=graph_freshness,
        graph_freshness_problems=graph_freshness_problems,
    ):
        warnings.append(
            {
                "status": "warning",
                "selection_reason": warning["message"],
                **warning,
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
    data["render_projection"] = "full"
    data["stop_reason"] = "required_evidence_satisfied"
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
            data["stop_reason"] = "budget_reached"
            estimate = estimate_tokens(render_task_context_pack_markdown(data))
    if estimate <= budget_tokens:
        return "budget_reached" if removed else "required_evidence_satisfied"
    data["render_projection"] = "required_reference_manifest"
    data["stop_reason"] = "budget_reached"
    estimate = estimate_tokens(render_task_context_pack_markdown(data))
    if estimate <= budget_tokens:
        return "budget_reached"
    data["stop_reason"] = "required_evidence_exceeds_budget"
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


def _task_pack_freshness_warnings(
    bundle: ContextBundle | None,
    *,
    graph_freshness: dict[str, Any],
    graph_freshness_problems: list[Problem],
) -> list[dict[str, str]]:
    if bundle is not None:
        bundled = [
            {
                "code": str(item.get("code") or ""),
                "message": str(item.get("message") or item.get("selection_reason") or ""),
            }
            for item in bundle.groups.get("warnings_and_completeness", [])
            if isinstance(item, dict)
            and str(item.get("code") or "") in CONTEXT_GRAPH_FRESHNESS_WARNING_CODES
        ]
        if bundled:
            return bundled
    return context_graph_freshness_warnings(
        graph_freshness,
        freshness_problems=graph_freshness_problems,
    )


def _pack_warnings(
    bundle: Any,
    task: Task,
    *,
    graph_freshness: dict[str, Any],
    graph_freshness_problems: list[Problem],
) -> list[dict[str, str]]:
    warnings = [
        {
            "code": "context_pack_not_authoritative",
            "message": "task context pack uses structured Discovery as retrieval input; it does not set task scope or create knowledge",
        }
    ]
    warnings.extend(
        _task_pack_freshness_warnings(
            bundle,
            graph_freshness=graph_freshness,
            graph_freshness_problems=graph_freshness_problems,
        )
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

from __future__ import annotations

from pathlib import Path
from typing import Any

from .context_model import ContextBundle, ContextCandidate, ContextSourceRef
from .context_retrieval import excerpt_for_query, rank_context_chunks, retrieve_context
from .context_sources import collect_context_sources, context_graph_problems, context_overlay_chunks, current_source_chunks_for_paths
from .evidence_store import evidence_chunks_for_paths, query_evidence_index
from .graph import project_context_neighborhood
from .graph_model import digest_data
from .graph_store import compact_graph_freshness, graph_materialization_freshness, load_materialized_graph
from .io import RepoctlError
from .knowledge_candidates import query_knowledge_records
from .repositories import RepoTarget
from .tasks import Problem


CONTEXT_GROUPS = (
    "must_read",
    "likely_change_surface",
    "callers_and_dependents",
    "tests_and_verification",
    "reviewed_knowledge",
    "related_history",
    "supporting_evidence",
    "warnings_and_completeness",
)
CONTEXT_MODES = {
    "auto",
    "startup_reading",
    "code_location",
    "call_impact",
    "file_impact",
    "authority_or_contract",
    "past_decision",
    "invariant",
    "failure_mode",
}
GRAPH_EXPANSION_MODES = {"auto", "code_location", "call_impact", "file_impact"}
COMPACT_CONTINUATION_LIMIT = 8


def build_context_bundle(
    root: Path,
    *,
    target: RepoTarget,
    query: str,
    explain: bool = False,
    mode: str = "",
    graph_result: tuple[Any, list[Problem], dict[str, Any]] | None = None,
    graph_state_root: Path | None = None,
    include_linked_records: bool = True,
) -> tuple[ContextBundle | None, list[Problem], dict[str, Any]]:
    query_mode = normalize_context_mode(mode)
    snapshot, graph_problems, graph_meta = graph_result if graph_result is not None else load_materialized_graph(root, target=target)
    materialization = graph_meta.get("materialization") if isinstance(graph_meta.get("materialization"), dict) else {}
    if snapshot is None and str(materialization.get("status") or "") != "missing":
        return None, graph_problems, {"repository": target.to_dict(), "graph": graph_meta}
    if snapshot is not None:
        freshness, freshness_problems = graph_materialization_freshness(
            root,
            target=target,
            state_root=graph_state_root,
            snapshot=snapshot,
        )
    else:
        freshness, freshness_problems = {"status": "missing", "changed_paths": []}, []
    include_history = query_mode in {"past_decision", "failure_mode"}
    indexed_candidates: list[ContextCandidate] = []
    index_meta: dict[str, Any] = {}
    index_problems: list[Problem] = []
    index_available = False
    evidence_index_path = graph_state_root / target.id / "evidence.sqlite3" if graph_state_root is not None else None
    if snapshot is not None:
        indexed_candidates, index_meta, index_problems = query_evidence_index(
            root,
            target=target,
            query=query,
            mode=query_mode,
            snapshot_digest=snapshot.snapshot_digest,
            graph_input_digest=str(materialization.get("input_digest") or ""),
            limit=24,
            database_path=evidence_index_path,
        )
        if any(problem.severity == "error" for problem in index_problems):
            return None, [*index_problems, *context_graph_problems(graph_problems)], {
                "repository": target.to_dict(),
                "graph": graph_meta,
                "evidence_index": index_meta,
            }
        index_available = True
    if index_available:
        chunks: list[Any] = []
        indexed_chunk_counts = index_meta.get("chunk_counts") if isinstance(index_meta.get("chunk_counts"), dict) else {}
        source_count = sum(int(value or 0) for value in indexed_chunk_counts.values())
        source_snapshots = {
            key: str(index_meta.get(key) or "")
            for key in ("document_manifest_digest", "receipt_manifest_digest", "current_source_manifest_digest", "snapshot_digest")
            if index_meta.get(key)
        }
        receipt_problems = snapshot.completeness.get("receipt_problems", []) if snapshot is not None else []
        completeness = {
            "documents_checked": int((index_meta.get("chunk_counts") or {}).get("document") or 0),
            "manifests_checked": int((index_meta.get("chunk_counts") or {}).get("product_manifest") or 0),
            "receipts_checked": int((index_meta.get("chunk_counts") or {}).get("completion_receipt") or 0),
            "current_sources_checked": int((index_meta.get("chunk_counts") or {}).get("current_source") or 0),
            "history_loaded": include_history,
            "receipt_problem_count": len(receipt_problems),
            "receipt_problem_paths": sorted(str(problem.get("path") or "") for problem in receipt_problems if isinstance(problem, dict) and problem.get("path")),
            "graph_available": snapshot is not None,
            "graph_meta": graph_meta,
        }
        evidence_index_problems = [
            Problem(
                str(item.get("severity") or "warning"),
                str(item.get("code") or "context_evidence_index_problem"),
                str(item.get("message") or "evidence index source was not available"),
                str(item.get("path") or ""),
            )
            for item in index_meta.get("problems", [])
            if isinstance(item, dict)
        ]
        completeness["evidence_problem_count"] = len(evidence_index_problems)
        completeness["evidence_problem_paths"] = sorted(
            problem.path
            for problem in evidence_index_problems
            if problem.path
        )
        if snapshot is not None:
            completeness["graph_completeness"] = snapshot.completeness
        completeness["graph_freshness"] = freshness
        problems = [problem for problem in index_problems if problem.severity != "error"]
        problems.extend(evidence_index_problems)
        receipt_graph_problems = [
            Problem(
                str(problem.get("severity") or "warning"),
                str(problem.get("code") or "invalid_completion_receipt"),
                str(problem.get("message") or "completion receipt is invalid"),
                str(problem.get("path") or ""),
            )
            for problem in receipt_problems
            if isinstance(problem, dict)
        ]
        problems.extend(context_graph_problems([*graph_problems, *receipt_graph_problems]))
        changed_repo_paths = {str(path) for path in freshness.get("changed_paths", []) if str(path)}
        stale_workspace_paths = {
            *{f"{target.display_path.rstrip('/')}/{path}" for path in changed_repo_paths},
            *{str(path) for path in freshness.get("changed_root_paths", []) if str(path)},
        }
        indexed_candidates = [candidate for candidate in indexed_candidates if candidate.source_ref.path not in stale_workspace_paths]
        classifications = freshness.get("changed_path_classifications") if isinstance(freshness.get("changed_path_classifications"), dict) else {}
        overlay_candidates, overlay_chunks, overlay_problems = _changed_source_candidates(
            root,
            target=target,
            query=query,
            repo_paths={path for path in changed_repo_paths if str(classifications.get(path) or "") != "excluded"},
        )
        problems.extend(overlay_problems)
        static_overlay_chunks, static_overlay_problems = context_overlay_chunks(
            root,
            target=target,
            workspace_paths=stale_workspace_paths,
            include_history=include_history,
        )
        problems.extend(static_overlay_problems)
        static_overlay_candidates = retrieve_context(
            query,
            _retrieval_chunks(static_overlay_chunks, mode=query_mode, target=target),
            limit=24,
        )
        overlay_chunks.extend(static_overlay_chunks)
        if overlay_chunks:
            source_snapshots["overlay_manifest_digest"] = digest_data(
                [chunk.source_ref.to_dict() for chunk in sorted(overlay_chunks, key=lambda item: item.source_ref.key())]
            )
        retrieved_candidates = _dedupe_candidates([*indexed_candidates, *overlay_candidates, *static_overlay_candidates])[:24]
    else:
        overlay_chunks = []
        chunks, source_snapshots, completeness, source_problems = collect_context_sources(
            root,
            target=target,
            snapshot=snapshot,
            graph_problems=graph_problems,
            graph_meta=graph_meta,
            include_history=include_history,
        )
        problems = [*source_problems]
        source_count = len(chunks)
        retrieval_chunks = _retrieval_chunks(chunks, mode=query_mode, target=target)
        retrieved_candidates = retrieve_context(query, retrieval_chunks, limit=24)
    if _task_history_stale(freshness) and isinstance(completeness.get("graph_completeness"), dict):
        graph_completeness = dict(completeness["graph_completeness"])
        capabilities = dict(graph_completeness.get("capabilities") or {})
        capabilities["task_history"] = "partial"
        graph_completeness["capabilities"] = capabilities
        graph_completeness["status"] = "partial"
        graph_completeness["task_history_fresh"] = False
        completeness["graph_completeness"] = graph_completeness
    graph_projection: dict[str, Any] = {}
    if query_mode in GRAPH_EXPANSION_MODES:
        if snapshot is not None:
            stale_paths = {str(path) for path in freshness.get("changed_paths", []) if str(path)}
            seed_paths = [path for path in _graph_seed_paths(retrieved_candidates, target=target) if path not in stale_paths]
            graph_projection = project_context_neighborhood(snapshot, seed_paths=seed_paths) if seed_paths else {}
            graph_projection = _fresh_graph_projection(
                graph_projection,
                stale_paths=stale_paths,
                task_history_stale=_task_history_stale(freshness),
            )
            if index_available:
                projected_paths = {
                    f"{target.display_path.rstrip('/')}/{path}"
                    for path in [*seed_paths, *[str(path) for path in graph_projection.get("related_paths", [])]]
                    if path and path not in stale_paths
                }
                chunks, chunk_problems = evidence_chunks_for_paths(
                    root,
                    target=target,
                    workspace_paths=projected_paths,
                    kinds={"current_source"},
                    database_path=evidence_index_path,
                )
                problems.extend(chunk_problems)
        graph_candidates, graph_warnings, graph_projection = _graph_context_candidates(
            snapshot,
            chunks=chunks,
            target=target,
            source_candidates=retrieved_candidates,
            query=query,
            projection=graph_projection or None,
        )
    else:
        graph_candidates, graph_warnings = [], []
    if freshness.get("status") == "stale":
        stale_source_count = int(freshness.get("changed_path_count") or 0)
        stale_root_count = int(freshness.get("changed_root_path_count") or 0)
        graph_warnings.append(
            {
                "code": "context_graph_stale",
                "message": f"Graph snapshot is stale for {stale_source_count} product path(s) and {stale_root_count} workspace evidence path(s); stale evidence was excluded or overlaid from current files",
            }
        )
    if _task_history_stale(freshness):
        graph_warnings.append(
            {
                "code": "context_task_history_stale",
                "message": "Task receipt or artifact evidence changed after Graph materialization; related history is omitted until Graph is rebuilt",
            }
        )
    if freshness_problems:
        graph_warnings.append(
            {
                "code": "context_graph_freshness_unavailable",
                "message": "Graph freshness could not be fully verified; rebuild Graph before relying on semantic relations",
            }
        )
    if index_available and query_mode == "startup_reading":
        startup_chunks, startup_problems = evidence_chunks_for_paths(
            root,
            target=target,
            workspace_paths=set(_startup_source_priority(target)),
            database_path=evidence_index_path,
        )
        problems.extend(startup_problems)
        startup_chunks = [chunk for chunk in startup_chunks if chunk.source_ref.path not in stale_workspace_paths]
        startup_chunks.extend(overlay_chunks)
    else:
        startup_chunks = chunks
    startup_candidates = _startup_query_candidates(startup_chunks, target=target, mode=query_mode)
    evidence = _dedupe_candidates([*startup_candidates, *graph_candidates, *retrieved_candidates])
    selection = {"evidence_count": len(evidence)}
    knowledge_data: dict[str, Any] = {}
    if query_mode == "auto" and include_linked_records:
        related_paths = _knowledge_related_paths(target=target, evidence=evidence)
        knowledge_data, knowledge_problems, knowledge_warnings = query_knowledge_records(
            root,
            repo_id=target.id,
            query=query,
            include_stale=False,
            limit=3,
            explain=explain,
            related_paths=related_paths,
            require_related=True,
        )
        problems.extend(
            Problem(
                "warning",
                "context_linked_knowledge_unavailable",
                f"{problem.code}: {problem.message}; current source and Graph results remain available",
                problem.path,
            )
            for problem in knowledge_problems
        )
        problems.extend(knowledge_warnings)
    elif query_mode in {"authority_or_contract", "invariant", "past_decision", "failure_mode"}:
        knowledge_data, knowledge_problems, knowledge_warnings = query_knowledge_records(root, repo_id=target.id, query=query, include_stale=False, limit=10, explain=explain)
        problems.extend(knowledge_problems)
        problems.extend(knowledge_warnings)
    groups = _context_groups(
        evidence,
        knowledge_results=knowledge_data.get("results", []) if isinstance(knowledge_data.get("results"), list) else [],
        target=target,
        completeness=completeness,
        graph_warnings=graph_warnings,
        related_history=_related_path_history(
            target=target,
            evidence=evidence,
            history=graph_projection.get("history", []) if include_linked_records and isinstance(graph_projection.get("history"), list) else [],
        ),
    )
    bundle = ContextBundle(
        repository=target.to_dict(),
        query={"text": query, "type": "natural_language", "mode": query_mode, "explain": explain},
        source_snapshots=source_snapshots,
        completeness={
            **completeness,
            "source_count": source_count,
            "group_names": list(CONTEXT_GROUPS),
            "knowledge_available_record_count": int(knowledge_data.get("available_record_count") or 0),
            "knowledge_result_count": int(knowledge_data.get("result_count") or 0),
            "knowledge_lifecycle": knowledge_data.get("lifecycle", {}) if isinstance(knowledge_data.get("lifecycle"), dict) else {},
        },
        evidence=evidence,
        selection=selection,
        knowledge_results=knowledge_data.get("results", []) if isinstance(knowledge_data.get("results"), list) else [],
        groups=groups,
    ).with_digest()
    meta = {"repository": target.to_dict(), "graph": graph_meta}
    return bundle, problems, meta


def normalize_context_mode(explicit_mode: str = "") -> str:
    normalized = explicit_mode.strip().lower().replace("-", "_")
    aliases = {
        "authority": "authority_or_contract",
        "contract": "authority_or_contract",
        "call-impact": "call_impact",
        "file-impact": "file_impact",
        "code-location": "code_location",
        "past-decision": "past_decision",
        "failure-mode": "failure_mode",
    }
    normalized = aliases.get(normalized, normalized)
    if not normalized:
        return "auto"
    if normalized not in CONTEXT_MODES:
        raise RepoctlError(f"unsupported context mode: {explicit_mode}", code="invalid_context_mode", path=explicit_mode)
    return normalized


def _changed_source_candidates(
    root: Path,
    *,
    target: RepoTarget,
    query: str,
    repo_paths: set[str],
) -> tuple[list[ContextCandidate], list[Any], list[Problem]]:
    chunks, problems = current_source_chunks_for_paths(
        root,
        target=target,
        repo_paths=repo_paths,
    )
    return retrieve_context(query, chunks, limit=24), chunks, problems


def _fresh_graph_projection(
    projection: dict[str, Any],
    *,
    stale_paths: set[str],
    task_history_stale: bool,
) -> dict[str, Any]:
    relations = projection.get("relations") if isinstance(projection.get("relations"), list) else []
    fresh_relations = [
        relation
        for relation in relations
        if isinstance(relation, dict)
        and str(relation.get("from_path") or "") not in stale_paths
        and str(relation.get("to_path") or "") not in stale_paths
    ]
    return {
        **projection,
        "seed_paths": [path for path in projection.get("seed_paths", []) if str(path) not in stale_paths],
        "related_paths": [path for path in projection.get("related_paths", []) if str(path) not in stale_paths],
        "relations": fresh_relations,
        "history": [] if task_history_stale else projection.get("history", []),
    }


def _task_history_stale(freshness: dict[str, Any]) -> bool:
    prefixes = (
        "docs/tasks/.repoctl-state/completions/",
        "docs/tasks/",
        "docs/archive/tasks/",
    )
    return any(
        str(path).startswith(prefixes)
        for path in freshness.get("changed_root_paths", [])
    )


def _retrieval_chunks(chunks: list[Any], *, mode: str, target: RepoTarget) -> list[Any]:
    if mode == "auto":
        product_prefix = f"{target.display_path.rstrip('/')}/"
        return [
            chunk
            for chunk in chunks
            if chunk.source_ref.path.startswith(product_prefix)
            and chunk.source_ref.kind not in {"completion_receipt", "task_artifact"}
        ]
    if mode not in {"code_location", "call_impact", "file_impact"}:
        return chunks
    product_prefix = f"{target.display_path.rstrip('/')}/"
    allowed_kinds = {"current_source", "product_manifest", "verification_hint"}
    return [
        chunk
        for chunk in chunks
        if chunk.source_ref.kind in allowed_kinds
        and (
            chunk.source_ref.path.startswith(product_prefix)
        )
    ]


def render_context_markdown(bundle: ContextBundle) -> str:
    data = bundle.to_dict()
    query = data["query"]
    lines = [
        "# Context Bundle",
        "",
        f"- Query: {query.get('text', '')}",
        f"- Mode: `{query.get('mode', '')}`",
        f"- Repository: `{bundle.repository.get('id', '')}`",
        f"- Bundle digest: `{bundle.bundle_digest}`",
        "",
    ]
    titles = {
        "must_read": "Must Read",
        "likely_change_surface": "Likely Change Surface",
        "callers_and_dependents": "Callers And Dependents",
        "tests_and_verification": "Tests And Verification",
        "reviewed_knowledge": "Reviewed Knowledge",
        "related_history": "Related History",
        "supporting_evidence": "Supporting Evidence",
        "warnings_and_completeness": "Warnings And Completeness",
    }
    for group in CONTEXT_GROUPS:
        items = data.get("groups", {}).get(group, [])
        lines.extend([f"## {titles[group]}", ""])
        if not items:
            lines.extend(["- No evidence selected.", ""])
            continue
        for item in items[:10]:
            ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
            label = ref.get("path") or item.get("record_id") or item.get("code") or "evidence"
            sections = item.get("sections") if isinstance(item.get("sections"), list) else []
            section_names = [str(section.get("section") or "") for section in sections if isinstance(section, dict) and str(section.get("section") or "")]
            section = f" ({', '.join(section_names[:3])})" if section_names else ""
            reason = item.get("selection_reason") or item.get("status") or ""
            lines.append(f"- `{label}`{section}: {reason}")
            excerpt = str(item.get("excerpt") or "").strip()
            if excerpt:
                compact = " ".join(excerpt.split())
                lines.append(f"  {compact[:240]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def compact_context_bundle(bundle: ContextBundle, *, max_group_items: int = 8, excerpt_chars: int = 120) -> dict[str, Any]:
    """Return the default agent-facing view without full evidence diagnostics."""
    group_limits = {
        "must_read": 5,
        "likely_change_surface": 3,
        "callers_and_dependents": 2,
        "tests_and_verification": 2,
        "reviewed_knowledge": 2,
        "related_history": 3,
        "supporting_evidence": 1,
        "warnings_and_completeness": 3,
    }
    displayed_items, continuations, continuation_counts = _compact_bundle_projection(
        bundle.groups,
        group_limits=group_limits,
        max_group_items=max_group_items,
        continuation_limit=COMPACT_CONTINUATION_LIMIT,
    )
    groups = {
        group: [_compact_group_item(item, excerpt_chars=excerpt_chars) for item in items]
        for group, items in displayed_items.items()
    }
    group_names = _ordered_context_group_names(bundle.groups)
    group_counts = {group: len(bundle.groups[group]) for group in group_names}
    displayed_group_counts = {group: len(groups[group]) for group in group_names}
    omitted = {
        "group_counts": group_counts,
        "displayed_group_counts": displayed_group_counts,
        "omitted_group_items": {
            group: max(0, group_counts.get(group, 0) - displayed_group_counts.get(group, 0))
            for group in sorted(group_counts)
        },
    }
    return {
        "schema": bundle.schema,
        "schema_version": bundle.schema_version,
        "view": "compact",
        "authoritative": bundle.authoritative,
        "repository": bundle.repository,
        "query": bundle.query,
        "completeness": _compact_completeness(bundle.completeness),
        "groups": groups,
        "continuations": continuations,
        "selection": {**bundle.selection, **omitted, "continuations": continuation_counts},
        "knowledge_result_count": len(bundle.knowledge_results),
        "bundle_digest": bundle.bundle_digest,
    }


def _compact_completeness(completeness: dict[str, Any]) -> dict[str, Any]:
    graph = completeness.get("graph_completeness") if isinstance(completeness.get("graph_completeness"), dict) else {}
    capabilities = graph.get("capabilities") if isinstance(graph.get("capabilities"), dict) else {}
    provider_coverage = graph.get("provider_coverage") if isinstance(graph.get("provider_coverage"), dict) else {}
    return {
        "graph_available": bool(completeness.get("graph_available")),
        "graph_freshness": compact_graph_freshness(completeness.get("graph_freshness")),
        "status": str(graph.get("status") or ("unavailable" if not completeness.get("graph_available") else "partial")),
        "capabilities": {str(key): str(value) for key, value in sorted(capabilities.items())},
        "provider_coverage": {
            str(name): {
                "status": str(value.get("status") or ""),
                "evidence_level": str(value.get("evidence_level") or ""),
            }
            for name, value in sorted(provider_coverage.items())
            if isinstance(value, dict)
        },
        "code_facts_complete": bool(graph.get("code_facts_complete", False)),
        "receipt_set_complete": bool(graph.get("receipt_set_complete", False)),
        "receipt_problem_count": int(completeness.get("receipt_problem_count") or 0),
        "evidence_problem_count": int(completeness.get("evidence_problem_count") or 0),
        "knowledge_available_record_count": int(completeness.get("knowledge_available_record_count") or 0),
        "knowledge_result_count": int(completeness.get("knowledge_result_count") or 0),
    }


def _compact_group_item(item: dict[str, Any], *, excerpt_chars: int) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("record_id", "code", "evidence_role"):
        if item.get(key):
            compact[key] = item[key]
    if item.get("selection_reason"):
        compact["selection_reason"] = _truncate(str(item["selection_reason"]), 90)
    if item.get("status") not in {None, "", "current", "recorded"}:
        compact["status"] = item["status"]
    ref = item.get("source_ref")
    if isinstance(ref, dict):
        compact["source_ref"] = ref
    sections = item.get("sections") if isinstance(item.get("sections"), list) else []
    if sections:
        compact["sections"] = [
            {
                key: section[key]
                for key in ("section", "line_start", "line_end")
                if key in section
            }
            for section in sections[:2]
            if isinstance(section, dict)
        ]
        compact["section_count"] = len(sections)
    excerpt = item.get("excerpt")
    if excerpt:
        compact["excerpt"] = _truncate(str(excerpt), excerpt_chars)
    return compact


def _ordered_context_group_names(groups: dict[str, list[dict[str, Any]]]) -> list[str]:
    canonical = [group for group in CONTEXT_GROUPS if group in groups]
    return [*canonical, *sorted(group for group in groups if group not in CONTEXT_GROUPS)]


def _collect_bundle_continuations(groups: dict[str, list[dict[str, Any]]]) -> list[dict[str, Any]]:
    values: list[dict[str, Any]] = []
    for group in _ordered_context_group_names(groups):
        for item in groups[group]:
            continuations = item.get("continuations") if isinstance(item.get("continuations"), list) else []
            values.extend(value for value in continuations if isinstance(value, dict))
    return _dedupe_continuations(values)


def _compact_bundle_projection(
    all_groups: dict[str, list[dict[str, Any]]],
    *,
    group_limits: dict[str, int],
    max_group_items: int,
    continuation_limit: int,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, int]]:
    """Project evidence and its producer-owned primary continuations together."""
    group_names = _ordered_context_group_names(all_groups)
    displayed_groups: dict[str, list[dict[str, Any]]] = {group: [] for group in group_names}
    selected_continuations: list[dict[str, Any]] = []
    secondary_continuations: list[dict[str, Any]] = []

    for group in group_names:
        display_limit = min(max_group_items, group_limits.get(group, max_group_items))
        for item in all_groups[group]:
            if len(displayed_groups[group]) >= display_limit:
                break
            if group == "warnings_and_completeness":
                displayed_groups[group].append(item)
                continue
            raw_values = item.get("continuations")
            if not isinstance(raw_values, list) or not raw_values or not isinstance(raw_values[0], dict):
                continue
            primary_values = _dedupe_continuations([raw_values[0]])
            if len(primary_values) != 1:
                continue
            item_values = _dedupe_continuations(
                [
                    primary_values[0],
                    *(value for value in raw_values[1:] if isinstance(value, dict)),
                ]
            )
            reserved = _dedupe_continuations([*selected_continuations, item_values[0]])
            if len(reserved) > continuation_limit:
                continue
            selected_continuations = reserved
            displayed_groups[group].append(item)
            secondary_continuations.extend(item_values[1:])

    for continuation in secondary_continuations:
        expanded = _dedupe_continuations([*selected_continuations, continuation])
        if len(expanded) <= continuation_limit:
            selected_continuations = expanded

    all_values = _collect_bundle_continuations(all_groups)
    return displayed_groups, selected_continuations, {
        "total": len(all_values),
        "displayed": len(selected_continuations),
        "omitted": max(0, len(all_values) - len(selected_continuations)),
    }


def _truncate(value: str, limit: int) -> str:
    compact = " ".join(value.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def _knowledge_related_paths(*, target: RepoTarget, evidence: list[ContextCandidate]) -> set[str]:
    paths: set[str] = set()
    product_prefix = f"{target.display_path.rstrip('/')}/"
    for candidate in evidence:
        path = candidate.source_ref.path
        if path and not path.startswith("<"):
            paths.add(path)
            if path.startswith(product_prefix):
                paths.add(path.removeprefix(product_prefix))
    return paths


def _related_path_history(*, target: RepoTarget, evidence: list[ContextCandidate], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    product_prefix = f"{target.display_path.rstrip('/')}/"
    selected_paths = {
        candidate.source_ref.path.removeprefix(product_prefix)
        for candidate in evidence
        if candidate.source_ref.kind == "current_source" and candidate.source_ref.path.startswith(product_prefix)
    }
    return [item for item in history if isinstance(item, dict) and str(item.get("path") or "") in selected_paths]


def _startup_query_candidates(chunks: list[Any], *, target: RepoTarget, mode: str) -> list[ContextCandidate]:
    if mode != "startup_reading":
        return []
    wanted = _startup_source_priority(target)
    selected: list[ContextCandidate] = []
    chunks_by_path: dict[str, list[Any]] = {}
    for chunk in chunks:
        path = chunk.source_ref.path
        if path not in wanted:
            continue
        chunks_by_path.setdefault(path, []).append(chunk)
    for path, score in sorted(wanted.items(), key=lambda item: (-item[1], item[0])):
        path_chunks = chunks_by_path.get(path, [])
        if not path_chunks:
            continue
        chunk = next(
            (item for item in path_chunks if len(item.text.strip()) >= 80),
            max(path_chunks, key=lambda item: (len(item.text.strip()), -item.source_ref.line_start)),
        )
        selected.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=_truncate(chunk.text, 700),
                score=score,
                score_breakdown={"startup_reading": 1.0},
                selection_reasons=["startup/read-first source"],
                graph_path=[],
            )
        )
    return sorted(selected, key=lambda candidate: (-candidate.score, candidate.source_ref.path))[:8]


def _startup_source_priority(target: RepoTarget) -> dict[str, float]:
    repo_prefix = target.display_path.rstrip("/")
    paths = [
        (f"{repo_prefix}/README.md", 30.0),
        (f"{repo_prefix}/package.json", 29.0),
        (f"{repo_prefix}/tsconfig.json", 28.5),
        (f"{repo_prefix}/jsconfig.json", 28.5),
        (f"{repo_prefix}/pyproject.toml", 29.0),
        (f"{repo_prefix}/pubspec.yaml", 29.0),
        (f"{repo_prefix}/analysis_options.yaml", 28.5),
        (f"{repo_prefix}/Cargo.toml", 29.0),
        (f"{repo_prefix}/go.mod", 29.0),
        (f"{repo_prefix}/Packages/manifest.json", 28.5),
        (f"{repo_prefix}/ProjectSettings/ProjectVersion.txt", 28.5),
        (f"{repo_prefix}/docs/README.md", 28.0),
        (f"{repo_prefix}/docs/PRD.md", 27.0),
        ("AGENTS.md", 26.0),
        ("docs/BOARD.md", 25.0),
        ("docs/PRD.md", 24.0),
        ("README.md", 15.0),
        ("docs/README.md", 14.0),
    ]
    return {path: score for path, score in paths}


def _graph_context_candidates(
    snapshot: Any,
    *,
    chunks: list[Any],
    target: RepoTarget,
    source_candidates: list[ContextCandidate],
    query: str,
    projection: dict[str, Any] | None = None,
) -> tuple[list[ContextCandidate], list[dict[str, str]], dict[str, Any]]:
    if snapshot is None:
        return [], [{"code": "context_graph_unavailable", "message": "Graph snapshot was not available for context query"}], {}
    if not source_candidates:
        return [], [], {}
    product_prefix = f"{target.display_path.rstrip('/')}/"
    source_chunks: dict[str, list[Any]] = {}
    for chunk in chunks:
        if chunk.source_ref.kind != "current_source" or not chunk.source_ref.path.startswith(product_prefix):
            continue
        source_chunks.setdefault(chunk.source_ref.path.removeprefix(product_prefix), []).append(chunk)
    retrieval_by_path: dict[str, ContextCandidate] = {}
    for candidate in source_candidates:
        path = candidate.source_ref.path
        if candidate.source_ref.kind != "current_source" or not path.startswith(product_prefix):
            continue
        repo_path = path.removeprefix(product_prefix)
        retrieval_by_path.setdefault(repo_path, candidate)
    seed_paths = _graph_seed_paths(source_candidates, target=target)
    if not seed_paths:
        return [], [], {}

    candidates: list[ContextCandidate] = []
    projection = projection or project_context_neighborhood(snapshot, seed_paths=seed_paths)
    relations = projection.get("relations") if isinstance(projection.get("relations"), list) else []
    anchor_scores = {
        path: retrieval_by_path[path].score
        for path in seed_paths
        if path in retrieval_by_path
    }
    relations_by_path: dict[str, list[dict[str, Any]]] = {}
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        for key in ("from_path", "to_path"):
            path = str(relation.get(key) or "")
            if path:
                relations_by_path.setdefault(path, []).append(relation)
    for path_relations in relations_by_path.values():
        path_relations.sort(key=lambda relation: -_relation_relevance(relation, anchor_scores=anchor_scores, retrieval_by_path=retrieval_by_path))
    for relation in sorted(
        (relation for relation in relations if isinstance(relation, dict)),
        key=lambda item: -_relation_relevance(item, anchor_scores=anchor_scores, retrieval_by_path=retrieval_by_path),
    ):
        candidates.append(
            _graph_relation_candidate(
                relation,
                score=_relation_relevance(relation, anchor_scores=anchor_scores, retrieval_by_path=retrieval_by_path),
            )
        )

    projected_paths = list(dict.fromkeys([*seed_paths, *[str(path) for path in projection.get("related_paths", [])]]))
    seed_path_set = set(seed_paths)
    for path in projected_paths:
        path_chunks = source_chunks.get(str(path), [])
        chunk = _graph_source_chunk(path_chunks, query=query, retrieved=retrieval_by_path.get(str(path)))
        if chunk is None:
            continue
        path_relations = relations_by_path.get(str(path), [])
        scoring_relations = path_relations
        propagated_score = 0.0
        for relation in scoring_relations:
            distance = max(1, int(relation.get("distance") or 1))
            origins = relation.get("origin_paths") if isinstance(relation.get("origin_paths"), list) else []
            for origin in origins:
                origin_path = str(origin or "")
                if origin_path == path:
                    continue
                propagated_score = max(propagated_score, anchor_scores.get(origin_path, 0.0) / distance)
        if path in seed_path_set and propagated_score <= 0:
            continue
        reasons = [_relation_reason(relation) for relation in scoring_relations[:3]]
        retrieved = retrieval_by_path.get(str(path))
        lexical_score = retrieved.score if retrieved is not None else 0.0
        if retrieved is not None:
            reasons.extend(retrieved.selection_reasons)
        candidates.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=excerpt_for_query(chunk.text, query, limit=700),
                score=propagated_score + lexical_score,
                score_breakdown={
                    "exact": float(retrieved.score_breakdown.get("exact", 0.0)) if retrieved is not None else 0.0,
                    "fts": float(retrieved.score_breakdown.get("fts", 0.0)) if retrieved is not None else 0.0,
                    "authority": float(retrieved.score_breakdown.get("authority", 0.0)) if retrieved is not None else 0.0,
                    "graph": propagated_score,
                },
                selection_reasons=reasons or ["Graph direct file relation"],
                graph_path=scoring_relations[:3],
            )
        )
    return _dedupe_candidates(candidates), [], projection


def _graph_seed_paths(source_candidates: list[ContextCandidate], *, target: RepoTarget) -> list[str]:
    product_prefix = f"{target.display_path.rstrip('/')}/"
    source_paths: list[str] = []
    test_paths: list[str] = []
    for candidate in source_candidates:
        path = candidate.source_ref.path
        if candidate.source_ref.kind != "current_source" or not path.startswith(product_prefix):
            continue
        repo_path = path.removeprefix(product_prefix)
        selected = test_paths if _looks_like_test_ref(repo_path.lower()) else source_paths
        if repo_path not in selected:
            selected.append(repo_path)
    return [*source_paths, *test_paths]


def _graph_source_chunk(chunks: list[Any], *, query: str, retrieved: ContextCandidate | None) -> Any | None:
    if not chunks:
        return None
    if retrieved is not None:
        for chunk in chunks:
            if chunk.source_ref.key() == retrieved.source_ref.key():
                return chunk
    ranked = rank_context_chunks(query, chunks, fts_scores={}, limit=1)
    if ranked:
        wanted = ranked[0].source_ref.key()
        for chunk in chunks:
            if chunk.source_ref.key() == wanted:
                return chunk
    module_chunks = [chunk for chunk in chunks if chunk.source_ref.section.endswith(" module")]
    return sorted(module_chunks or chunks, key=lambda item: (item.source_ref.line_start, item.source_ref.line_end, item.source_ref.section))[0]


def _graph_relation_candidate(relation: dict[str, Any], *, score: float) -> ContextCandidate:
    relation_digest = digest_data(relation)
    return ContextCandidate(
        source_ref=ContextSourceRef(
            kind="graph_relation",
            path=f"<graph-relation:{relation_digest[7:19]}>",
            section=str(relation.get("edge") or "relation"),
            content_sha256=relation_digest,
        ),
        text=_relation_reason(relation),
        score=score,
        score_breakdown={"exact": 0.0, "fts": 0.0, "authority": 0.0, "graph": 1.0},
        selection_reasons=["Graph direct relation"],
        graph_path=[relation],
    )


def _relation_relevance(
    relation: dict[str, Any],
    *,
    anchor_scores: dict[str, float],
    retrieval_by_path: dict[str, ContextCandidate],
) -> float:
    distance = max(1, int(relation.get("distance") or 1))
    origins = relation.get("origin_paths") if isinstance(relation.get("origin_paths"), list) else []
    propagated = max((anchor_scores.get(str(origin or ""), 0.0) / distance for origin in origins), default=0.0)
    endpoint_score = sum(
        retrieval_by_path[path].score
        for path in {
            str(relation.get("from_path") or ""),
            str(relation.get("to_path") or ""),
        }
        if path in retrieval_by_path
    )
    return propagated + endpoint_score


def _relation_reason(relation: dict[str, Any]) -> str:
    from_path = str(relation.get("from_path") or "")
    to_path = str(relation.get("to_path") or "")
    edge = str(relation.get("edge") or "RELATED")
    from_symbol = relation.get("from_symbol") if isinstance(relation.get("from_symbol"), dict) else {}
    to_symbol = relation.get("to_symbol") if isinstance(relation.get("to_symbol"), dict) else {}
    from_label = str(from_symbol.get("qualified_name") or from_symbol.get("name") or from_path)
    to_label = str(to_symbol.get("qualified_name") or to_symbol.get("name") or to_path)
    return f"{from_label} --{edge}--> {to_label}"


def _dedupe_candidates(candidates: list[ContextCandidate]) -> list[ContextCandidate]:
    best: dict[tuple[str, str, str, int, int], ContextCandidate] = {}
    for candidate in candidates:
        key = candidate.source_ref.key()
        previous = best.get(key)
        if previous is None or candidate.score > previous.score:
            best[key] = candidate
    return sorted(best.values(), key=_candidate_sort_key)


def _candidate_sort_key(candidate: ContextCandidate) -> tuple[int, float, str, int]:
    breakdown = candidate.score_breakdown
    direct_query_evidence = _has_direct_query_evidence(candidate)
    stage = 0 if direct_query_evidence else 1 if float(breakdown.get("graph") or 0.0) > 0 else 2
    return (stage, -candidate.score, candidate.source_ref.path, candidate.source_ref.line_start)


def _has_direct_query_evidence(candidate: ContextCandidate) -> bool:
    return any(
        float(candidate.score_breakdown.get(key) or 0.0) > 0
        for key in ("startup_reading", "exact", "fts")
    )


def _context_groups(
    evidence: list[ContextCandidate],
    *,
    knowledge_results: list[dict[str, Any]],
    target: RepoTarget,
    completeness: dict[str, Any],
    graph_warnings: list[dict[str, str]],
    related_history: list[dict[str, Any]],
) -> dict[str, list[dict[str, Any]]]:
    repo_id = target.id
    groups: dict[str, list[dict[str, Any]]] = {group: [] for group in CONTEXT_GROUPS}
    grouped_candidates: dict[str, list[ContextCandidate]] = {group: [] for group in CONTEXT_GROUPS}
    for candidate in evidence:
        grouped_candidates[_candidate_group(candidate)].append(candidate)
    for group, candidates in grouped_candidates.items():
        groups[group].extend(_path_group_items(candidates, group=group, target=target))
    for result in knowledge_results:
        record = result.get("record") if isinstance(result.get("record"), dict) else {}
        matched_paths = result.get("matched_paths") if isinstance(result.get("matched_paths"), list) else []
        groups["reviewed_knowledge"].append(
            {
                "repo_id": repo_id,
                "record_id": record.get("id", ""),
                "status": result.get("status") or record.get("status") or "reviewed",
                "selection_reason": f"reviewed knowledge linked to {', '.join(str(path) for path in matched_paths)}" if matched_paths else "reviewed knowledge match",
                "score_breakdown": result.get("score_breakdown", {}),
                "excerpt": record.get("claim") or record.get("summary") or "",
                "source_ref": {"kind": "knowledge_record", "path": f"docs/knowledge/records/{record.get('id', '')}.json", "content_sha256": record.get("record_digest", "")},
                "continuations": _knowledge_continuations(record),
            }
        )
    history_by_task: dict[str, dict[str, Any]] = {}
    for item in related_history:
        if not isinstance(item, dict):
            continue
        task_id = str(item.get("task_id") or "")
        if not task_id:
            continue
        aggregate = history_by_task.setdefault(
            task_id,
            {
                "task_path": str(item.get("task_path") or ""),
                "completed_at": str(item.get("completed_at") or ""),
                "attribution": str(item.get("attribution") or ""),
                "paths": set(),
            },
        )
        changed_path = str(item.get("path") or "")
        if changed_path:
            aggregate["paths"].add(changed_path)
    for task_id, item in sorted(history_by_task.items(), key=lambda pair: (str(pair[1].get("completed_at") or ""), pair[0]), reverse=True):
        task_path = str(item.get("task_path") or "")
        changed_paths = sorted(str(path) for path in item.get("paths", set()) if str(path))
        groups["related_history"].append(
            {
                "repo_id": repo_id,
                "record_id": task_id,
                "status": "recorded",
                "selection_reason": f"completion evidence touched {', '.join(changed_paths)}",
                "excerpt": " ".join(
                    part
                    for part in (
                        str(item.get("completed_at") or ""),
                        str(item.get("attribution") or ""),
                    )
                    if part
                ),
                "source_ref": {"kind": "task_artifact", "path": task_path, "section": task_id} if task_path else {"kind": "task_history", "path": task_id},
                "continuations": _task_history_continuations(task_id=task_id, task_path=task_path),
            }
        )
    for warning in graph_warnings:
        groups["warnings_and_completeness"].append({"repo_id": repo_id, "status": "warning", "selection_reason": warning.get("message", ""), **warning})
    if completeness.get("graph_completeness"):
        graph_completeness = completeness["graph_completeness"]
        if not graph_completeness.get("code_facts_complete", True):
            groups["warnings_and_completeness"].append(
                {
                    "repo_id": repo_id,
                    "status": "warning",
                    "code": "context_graph_code_facts_incomplete",
                    "selection_reason": f"Graph parse errors: {graph_completeness.get('parse_error_count', 0)}",
                }
            )
    graph_meta = completeness.get("graph_meta") if isinstance(completeness.get("graph_meta"), dict) else {}
    provider_coverage = graph_meta.get("provider_coverage") if isinstance(graph_meta.get("provider_coverage"), dict) else {}
    incomplete_coverage = {
        name: value.get("status")
        for name, value in sorted(provider_coverage.items())
        if isinstance(value, dict) and value.get("status") != "complete"
    }
    if incomplete_coverage:
        groups["warnings_and_completeness"].append(
            {
                "repo_id": repo_id,
                "status": "warning",
                "code": "context_graph_provider_coverage",
                "selection_reason": f"Graph semantic provider coverage is incomplete: {incomplete_coverage}",
            }
        )
    return groups


def _candidate_group(candidate: ContextCandidate) -> str:
    ref = candidate.source_ref
    path = ref.path.lower()
    section = ref.section.lower()
    if candidate.score_breakdown.get("startup_reading"):
        return "must_read"
    if ref.kind == "current_source":
        if _looks_like_test_ref(path):
            return "tests_and_verification"
        return "likely_change_surface"
    if ref.kind == "graph_relation":
        paths = {
            str(relation.get(key) or "").lower()
            for relation in candidate.graph_path
            if isinstance(relation, dict)
            for key in ("from_path", "to_path")
            if str(relation.get(key) or "")
        }
        if any(_looks_like_test_ref(path) for path in paths):
            return "tests_and_verification"
        return "callers_and_dependents" if candidate.graph_path else "supporting_evidence"
    if _is_product_document_ref(ref):
        return "must_read"
    if path.startswith("docs/adr/") or path.startswith("docs/contracts/") or path in {"agents.md", "docs/prd.md"} or section in {"decision", "authority rules", "future layer rules"}:
        return "must_read"
    if ref.kind in {"completion_receipt", "verification_hint"} or _looks_like_test_ref(path):
        return "tests_and_verification"
    return "supporting_evidence"


def _candidate_group_item(candidate: ContextCandidate, *, target: RepoTarget, status: str) -> dict[str, Any]:
    source_ref = {
        "kind": candidate.source_ref.kind,
        "path": candidate.source_ref.path,
        "content_sha256": candidate.source_ref.content_sha256,
    }
    roles = _candidate_evidence_roles(candidate, target=target)
    return {
        "repo_id": target.id,
        "status": status,
        "source_ref": source_ref,
        "sections": [_candidate_section(candidate)],
        "content_sha256": candidate.source_ref.content_sha256,
        "selection_reason": "; ".join(candidate.selection_reasons) or "retrieval match",
        "selection_reasons": sorted(set(candidate.selection_reasons)) or ["retrieval match"],
        "score": candidate.score,
        "score_breakdown": candidate.score_breakdown,
        "excerpt": candidate.text,
        "graph_path": candidate.graph_path,
        "continuations": _candidate_continuations(candidate, target=target),
        "evidence_role": roles[0],
        "evidence_roles": roles,
    }


def _candidate_section(candidate: ContextCandidate) -> dict[str, Any]:
    section = {"kind": candidate.source_ref.kind}
    for key, value in (
        ("section", candidate.source_ref.section),
        ("line_start", candidate.source_ref.line_start),
        ("line_end", candidate.source_ref.line_end),
    ):
        if value not in {"", 0}:
            section[key] = value
    return section


def _path_group_items(
    candidates: list[ContextCandidate],
    *,
    group: str,
    target: RepoTarget,
) -> list[dict[str, Any]]:
    by_path: dict[str, list[ContextCandidate]] = {}
    for candidate in candidates:
        by_path.setdefault(candidate.source_ref.path, []).append(candidate)
    items = [
        (
            _merge_path_candidates(path_candidates, target=target),
            _direct_query_score(path_candidates),
        )
        for _path, path_candidates in sorted(by_path.items())
    ]
    return [
        item
        for item, direct_query_score in sorted(
            items,
            key=lambda value: _group_item_sort_key(
                group,
                value[0],
                direct_query_score=value[1],
            ),
        )
    ]


def _direct_query_score(candidates: list[ContextCandidate]) -> float:
    scores = []
    for candidate in candidates:
        breakdown = candidate.score_breakdown
        if not _has_direct_query_evidence(candidate):
            continue
        scores.append(max(0.0, candidate.score - float(breakdown.get("graph") or 0.0)))
    return max(scores, default=0.0)


def _merge_path_candidates(candidates: list[ContextCandidate], *, target: RepoTarget) -> dict[str, Any]:
    ranked = sorted(candidates, key=_candidate_sort_key)
    primary = ranked[0]
    item = _candidate_group_item(primary, target=target, status="current")
    primary_continuations = _candidate_continuations(primary, target=target)
    sections: dict[tuple[str, str, int, int], dict[str, Any]] = {}
    reasons: set[str] = set()
    roles: set[str] = set()
    continuations: list[dict[str, Any]] = []
    graph_paths: dict[str, dict[str, Any]] = {}
    breakdown: dict[str, float] = {}
    for candidate in ranked:
        section = _candidate_section(candidate)
        section_key = (
            str(section.get("kind") or ""),
            str(section.get("section") or ""),
            int(section.get("line_start") or 0),
            int(section.get("line_end") or 0),
        )
        sections.setdefault(section_key, section)
        reasons.update(candidate.selection_reasons or ["retrieval match"])
        roles.update(_candidate_evidence_roles(candidate, target=target))
        if primary_continuations:
            continuations.extend(primary_continuations if candidate is primary else _candidate_continuations(candidate, target=target))
        for relation in candidate.graph_path:
            if isinstance(relation, dict):
                graph_paths.setdefault(digest_data(relation), relation)
        for key, value in candidate.score_breakdown.items():
            breakdown[key] = max(breakdown.get(key, 0.0), float(value))
    ordered_roles = sorted(roles, key=lambda role: (_evidence_role_priority(role), role))
    item.update(
        {
            "sections": [sections[key] for key in sorted(sections)],
            "selection_reason": "; ".join(sorted(reasons)[:4]),
            "selection_reasons": sorted(reasons),
            "score": max(candidate.score for candidate in ranked),
            "score_breakdown": breakdown,
            "graph_path": [graph_paths[key] for key in sorted(graph_paths)],
            "continuations": _dedupe_continuations(continuations),
            "evidence_role": ordered_roles[0],
            "evidence_roles": ordered_roles,
        }
    )
    return item


def _candidate_evidence_roles(candidate: ContextCandidate, *, target: RepoTarget) -> list[str]:
    ref = candidate.source_ref
    path = ref.path
    product_prefix = f"{target.display_path.rstrip('/')}/"
    repo_path = path.removeprefix(product_prefix) if path.startswith(product_prefix) else path
    lowered = repo_path.lower()
    roles: set[str] = set()
    if ref.kind == "current_source":
        is_test = _looks_like_test_ref(lowered)
        if _has_direct_query_evidence(candidate):
            roles.add("test_candidate" if is_test else "change_candidate")
        for relation in candidate.graph_path:
            if not isinstance(relation, dict):
                continue
            edge = str(relation.get("edge") or "")
            from_path = str(relation.get("from_path") or "")
            to_path = str(relation.get("to_path") or "")
            other_path = to_path if from_path == repo_path else from_path if to_path == repo_path else ""
            if is_test and other_path and not _looks_like_test_ref(other_path.lower()) and edge in {"CALLS", "IMPORTS_FILE"}:
                roles.add("directly_connected_test")
            if to_path == repo_path and from_path != repo_path:
                if edge == "IMPORTS_FILE":
                    roles.add("imported_dependency")
                elif edge == "CALLS":
                    roles.add("called_dependency")
            elif from_path == repo_path and to_path != repo_path and edge in {"CALLS", "IMPORTS_FILE"}:
                roles.add("dependent_source")
        if not roles:
            roles.add("supporting_evidence")
    elif ref.kind == "product_manifest":
        roles.add("product_manifest")
    elif ref.kind == "verification_hint":
        roles.add("verification_hint")
    elif ref.kind == "graph_relation":
        roles.add("code_relation")
    elif _is_product_document_ref(ref) or ref.path.lower() in {"agents.md", "docs/prd.md"} or ref.path.lower().startswith(("docs/adr/", "docs/contracts/")):
        roles.add("authority_document")
    else:
        roles.add("supporting_evidence")
    return sorted(roles, key=lambda role: (_evidence_role_priority(role), role))


def _evidence_role_priority(role: str) -> int:
    priorities = {
        "change_candidate": 0,
        "test_candidate": 0,
        "authority_document": 0,
        "directly_connected_test": 1,
        "imported_dependency": 1,
        "called_dependency": 1,
        "product_manifest": 1,
        "dependent_source": 2,
        "code_relation": 2,
        "verification_hint": 4,
        "supporting_evidence": 5,
    }
    return priorities.get(role, 9)


def _group_item_sort_key(
    group: str,
    item: dict[str, Any],
    *,
    direct_query_score: float,
) -> tuple[int, float, int, float, str]:
    role = str(item.get("evidence_role") or "")
    role_priority = _evidence_role_priority(role)
    if group == "callers_and_dependents":
        role_priority = 0 if role == "code_relation" else role_priority
    direct_query_stage = 0
    if group in {"likely_change_surface", "tests_and_verification"}:
        direct_query_stage = 0 if direct_query_score > 0 else 1
    return (
        direct_query_stage,
        -direct_query_score,
        role_priority,
        -float(item.get("score") or 0.0),
        str((item.get("source_ref") or {}).get("path") or ""),
    )


def _candidate_continuations(candidate: ContextCandidate, *, target: RepoTarget) -> list[dict[str, Any]]:
    primary = _candidate_primary_continuation(candidate, target=target)
    if primary is None:
        return []
    continuations = [primary]
    for relation in candidate.graph_path:
        if not isinstance(relation, dict):
            continue
        symbol_first = str(relation.get("edge") or "") == "CALLS"
        for path_key, symbol_key in (("from_path", "from_symbol"), ("to_path", "to_symbol")):
            repo_path = str(relation.get(path_key) or "")
            symbol = relation.get(symbol_key) if isinstance(relation.get(symbol_key), dict) else {}
            qualified_name = str(symbol.get("qualified_name") or symbol.get("name") or "")
            if symbol_first and qualified_name:
                continuations.append(_symbol_continuation(qualified_name, in_file=repo_path))
            if repo_path:
                continuations.append(_file_continuation(repo_path))
            if not symbol_first and qualified_name:
                continuations.append(_symbol_continuation(qualified_name, in_file=repo_path))
    return _dedupe_continuations(continuations)[:4]


def _candidate_primary_continuation(candidate: ContextCandidate, *, target: RepoTarget) -> dict[str, Any] | None:
    product_prefix = f"{target.display_path.rstrip('/')}/"
    path = candidate.source_ref.path
    if candidate.source_ref.kind == "current_source":
        repo_path = path.removeprefix(product_prefix) if path.startswith(product_prefix) else ""
        return _file_continuation(repo_path) if repo_path else None
    if candidate.source_ref.kind != "graph_relation":
        return _document_continuation(path) if path and not path.startswith("<") else None
    if not candidate.graph_path or not isinstance(candidate.graph_path[0], dict):
        return None
    relation = candidate.graph_path[0]
    edge = str(relation.get("edge") or "")
    from_path = str(relation.get("from_path") or "")
    if edge == "IMPORTS_FILE":
        return _file_continuation(from_path) if from_path else None
    if edge != "CALLS" or not from_path:
        return None
    from_symbol = relation.get("from_symbol") if isinstance(relation.get("from_symbol"), dict) else {}
    qualified_name = str(from_symbol.get("qualified_name") or from_symbol.get("name") or "")
    return _symbol_continuation(qualified_name, in_file=from_path) if qualified_name else None


def _knowledge_continuations(record: dict[str, Any]) -> list[dict[str, Any]]:
    record_id = str(record.get("id") or "")
    if not record_id:
        return []
    continuations = [
        {
            "selector": {"kind": "knowledge_record", "value": record_id},
            "actions": ["knowledge.show"],
        }
    ]
    for ref in record.get("source_refs", []):
        if isinstance(ref, dict) and str(ref.get("path") or ""):
            continuations.append(_document_continuation(str(ref["path"])))
    return _dedupe_continuations(continuations)[:4]


def _task_history_continuations(*, task_id: str, task_path: str) -> list[dict[str, Any]]:
    if not task_id:
        return []
    continuations = [
        {
            "selector": {"kind": "task", "value": task_id},
            "actions": ["graph.task", "task.show"],
        }
    ]
    if task_path:
        document = _document_continuation(task_path)
        document["actions"].insert(0, "graph.artifact")
        continuations.append(document)
    return continuations


def _file_continuation(path: str) -> dict[str, Any]:
    return {
        "selector": {"kind": "file", "value": path},
        "actions": ["workspace.open", "graph.file", "graph.impact_file"],
    }


def _symbol_continuation(symbol: str, *, in_file: str) -> dict[str, Any]:
    return {
        "selector": {"kind": "symbol", "value": symbol, **({"in_file": in_file} if in_file else {})},
        "actions": ["graph.symbol"],
    }


def _document_continuation(path: str) -> dict[str, Any]:
    return {
        "selector": {"kind": "document", "value": path},
        "actions": ["workspace.open"],
    }


def _dedupe_continuations(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    ordered_keys: list[tuple[str, str, str]] = []
    for item in items:
        selector = item.get("selector") if isinstance(item.get("selector"), dict) else {}
        key = (
            str(selector.get("kind") or ""),
            str(selector.get("value") or ""),
            str(selector.get("in_file") or ""),
        )
        if not key[0] or not key[1]:
            continue
        existing = unique.get(key)
        if existing is None:
            unique[key] = {**item, "selector": dict(selector)}
            ordered_keys.append(key)
            continue
        for field in ("actions", "query_types"):
            previous_values = existing.get(field) if isinstance(existing.get(field), list) else []
            incoming_values = item.get(field) if isinstance(item.get(field), list) else []
            if incoming_values:
                merged_values = list(previous_values)
                for value in incoming_values:
                    if value not in merged_values:
                        merged_values.append(value)
                existing[field] = merged_values
    return [unique[key] for key in ordered_keys]


def _is_product_document_ref(ref: ContextSourceRef) -> bool:
    path = ref.path.lower()
    name = path.rsplit("/", 1)[-1]
    return ref.kind == "document" and path.startswith("repos/") and ("/docs/" in path or name.startswith("readme"))


def _looks_like_test_ref(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        "/tests/" in path
        or "/test/" in path
        or path.startswith("tests/")
        or path.startswith("test/")
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".test.ts", ".test.mjs", ".test.mts", "_test.mjs", "_test.mts", "_test.dart"))
    )

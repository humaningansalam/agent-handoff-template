from __future__ import annotations

from collections.abc import Iterable
from pathlib import Path
from typing import Any

from .context_model import (
    CONTEXT_ANCHOR_STRENGTH_PRIORITY,
    ContextAnchorResolution,
    ContextAnchorResolutionCode,
    ContextAnchorStatus,
    ContextAnchorStrength,
    ContextBundle,
    ContextCandidate,
    ContextEvidenceKind,
    ContextGraphAnchorCandidate,
    ContextSectionKind,
    ContextSourceRef,
)
from .context_retrieval import excerpt_for_query, retrieve_context_balanced
from .context_sources import collect_context_sources, context_document_paths, context_graph_problems, context_overlay_chunks, current_source_chunks_for_paths
from .document_roles import AUTHORITY_DOCUMENT_ROLES, DocumentRole, source_document_role
from .evidence_store import evidence_chunks_for_paths, query_evidence_index
from .graph import project_context_neighborhood
from .graph_model import GraphContextAnchor, GraphContextAnchorKind, digest_data
from .graph_store import compact_graph_freshness, graph_materialization_freshness, load_materialized_graph
from .graph_structured_relations import STRUCTURED_EDGE_KIND
from .io import RepoctlError
from .knowledge_candidates import (
    KnowledgeExplicitPathKind,
    KnowledgeExplicitPathRole,
    KnowledgeQueryMatchStrength,
    query_knowledge_records,
)
from .path_roles import is_test_path
from .repositories import RepoSelectorStatus, RepoTarget, resolve_repo_selector_path
from .tasks import Problem, normalize_task_id


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
COMPACT_ITEM_LIMIT = 8
COMPACT_CONTINUATION_LIMIT = 8
ACTIONABLE_PRODUCT_KINDS = {"current_source", "config"}
CONTEXT_GRAPH_FRESHNESS_WARNING_CODES = frozenset(
    {
        "context_graph_stale",
        "context_task_history_stale",
        "context_graph_freshness_unavailable",
    }
)


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
    if snapshot is not None:
        freshness, freshness_problems = graph_materialization_freshness(
            root,
            target=target,
            state_root=graph_state_root,
            snapshot=snapshot,
        )
    else:
        freshness, freshness_problems = {"status": "missing", "changed_paths": []}, []
    include_history = query_mode in {"auto", "past_decision", "failure_mode"}
    indexed_chunks: list[Any] = []
    index_meta: dict[str, Any] = {}
    index_problems: list[Problem] = []
    index_available = False
    stale_workspace_paths: set[str] = set()
    overlay_chunks: list[Any] = []
    evidence_index_path = graph_state_root / target.id / "evidence.sqlite3" if graph_state_root is not None else None
    if snapshot is not None:
        indexed_chunks, index_meta, index_problems = query_evidence_index(
            root,
            target=target,
            query=query,
            mode=query_mode,
            snapshot_digest=snapshot.snapshot_digest,
            graph_input_digest=str(materialization.get("input_digest") or ""),
            limit=24,
            database_path=evidence_index_path,
        )
        index_available = not any(problem.severity == "error" for problem in index_problems)
    if index_available:
        chunks: list[Any] = []
        indexed_chunk_counts = index_meta.get("chunk_counts") if isinstance(index_meta.get("chunk_counts"), dict) else {}
        indexed_source_path_counts = index_meta.get("source_path_counts") if isinstance(index_meta.get("source_path_counts"), dict) else {}
        source_count = sum(int(value or 0) for value in indexed_chunk_counts.values())
        source_snapshots = {
            key: str(index_meta.get(key) or "")
            for key in ("document_manifest_digest", "receipt_manifest_digest", "current_source_manifest_digest", "snapshot_digest")
            if index_meta.get(key)
        }
        receipt_problems = snapshot.completeness.get("receipt_problems", []) if snapshot is not None else []
        completeness = {
            "documents_checked": int(indexed_source_path_counts.get("document") or 0),
            "manifests_checked": int(indexed_source_path_counts.get("product_manifest") or 0),
            "receipts_checked": int(indexed_source_path_counts.get("completion_receipt") or 0),
            "current_sources_checked": int(indexed_source_path_counts.get("current_source") or 0)
            + int(indexed_source_path_counts.get("config") or 0),
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
        classifications = freshness.get("changed_path_classifications") if isinstance(freshness.get("changed_path_classifications"), dict) else {}
        overlay_chunks, overlay_problems = current_source_chunks_for_paths(
            root,
            target=target,
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
        overlay_chunks.extend(static_overlay_chunks)
        if overlay_chunks:
            source_snapshots["overlay_manifest_digest"] = digest_data(
                [chunk.source_ref.to_dict() for chunk in sorted(overlay_chunks, key=lambda item: item.source_ref.key())]
            )
        retrieval_chunks = _retrieval_chunks(
            _merge_retrieval_chunks(
                indexed_chunks,
                overlay_chunks,
                replaced_paths=stale_workspace_paths,
            ),
            mode=query_mode,
            target=target,
        )
        retrieved_candidates = retrieve_context_balanced(
            query,
            retrieval_chunks,
            mode=query_mode,
            repository_path=target.display_path,
            limit=24,
        )
    else:
        chunks, source_snapshots, completeness, source_problems = collect_context_sources(
            root,
            target=target,
            snapshot=snapshot,
            graph_problems=graph_problems,
            graph_meta=graph_meta,
            include_history=include_history,
        )
        problems = [*source_problems]
        problems.extend(_context_index_warnings(index_problems))
        source_count = len(chunks)
        retrieval_chunks = _retrieval_chunks(chunks, mode=query_mode, target=target)
        retrieved_candidates = retrieve_context_balanced(
            query,
            retrieval_chunks,
            mode=query_mode,
            repository_path=target.display_path,
            limit=24,
        )
    if _task_history_stale(freshness) and isinstance(completeness.get("graph_completeness"), dict):
        graph_completeness = dict(completeness["graph_completeness"])
        capabilities = dict(graph_completeness.get("capabilities") or {})
        capabilities["task_history"] = "partial"
        graph_completeness["capabilities"] = capabilities
        graph_completeness["status"] = "partial"
        graph_completeness["task_history_fresh"] = False
        completeness["graph_completeness"] = graph_completeness
    knowledge_data: dict[str, Any] = {}
    knowledge_results: list[dict[str, Any]] = []
    knowledge_path_resolutions: dict[str, list[dict[str, Any]]] = {}
    knowledge_paths_resolved = False
    knowledge_queried = False
    if query_mode in GRAPH_EXPANSION_MODES and include_linked_records:
        related_paths = _knowledge_related_paths(target=target, evidence=retrieved_candidates)
        knowledge_data, knowledge_problems, knowledge_warnings = query_knowledge_records(
            root,
            repo_id=target.id,
            query=query,
            include_stale=False,
            limit=3,
            explain=explain,
            related_paths=related_paths,
            require_related=False,
        )
        knowledge_queried = True
        knowledge_results = knowledge_data.get("results", []) if isinstance(knowledge_data.get("results"), list) else []
        problems.extend(
            Problem(
                "warning",
                "context_linked_knowledge_unavailable",
                f"{problem.message}; current source and Graph results remain available",
                problem.path,
                problem.code,
            )
            for problem in knowledge_problems
        )
        problems.extend(knowledge_warnings)
        knowledge_path_resolutions = _resolve_reviewed_knowledge_paths(
            target=target,
            knowledge_results=knowledge_results,
            known_paths=_known_context_product_paths(
                target=target,
                snapshot=snapshot,
                chunks=chunks,
                overlay_chunks=overlay_chunks,
                excluded_workspace_paths=stale_workspace_paths,
            ),
        )
        knowledge_paths_resolved = True
        knowledge_path_candidates, knowledge_path_problems = _reviewed_knowledge_path_candidates(
            root,
            target=target,
            query=query,
            knowledge_results=knowledge_results,
            path_resolutions_by_record=knowledge_path_resolutions,
            chunks=chunks,
            overlay_chunks=overlay_chunks,
            stale_workspace_paths=stale_workspace_paths,
            index_available=index_available,
            evidence_index_path=evidence_index_path,
        )
        problems.extend(knowledge_path_problems)
        retrieved_candidates = _dedupe_candidates([*knowledge_path_candidates, *retrieved_candidates])
    graph_projection: dict[str, Any] = {}
    graph_anchor_resolution: ContextAnchorResolution | None = None
    if query_mode in GRAPH_EXPANSION_MODES:
        graph_anchor_resolution = _resolve_graph_anchors(retrieved_candidates, target=target)
        completeness["graph_anchor"] = graph_anchor_resolution.to_dict()
        if snapshot is not None and graph_anchor_resolution.status == ContextAnchorStatus.RESOLVED:
            stale_paths = {str(path) for path in freshness.get("changed_paths", []) if str(path)}
            graph_anchors = [
                candidate.anchor
                for candidate in graph_anchor_resolution.anchors
                if candidate.anchor.path not in stale_paths
            ]
            if len(graph_anchors) != len(graph_anchor_resolution.anchors):
                graph_anchor_resolution = ContextAnchorResolution(
                    status=ContextAnchorStatus.UNRESOLVED,
                    code=ContextAnchorResolutionCode.UNRESOLVED,
                    candidates=graph_anchor_resolution.candidates,
                )
                completeness["graph_anchor"] = graph_anchor_resolution.to_dict()
            seed_paths = list(dict.fromkeys(anchor.path for anchor in graph_anchors))
            if graph_anchor_resolution.status == ContextAnchorStatus.RESOLVED:
                graph_projection = project_context_neighborhood(
                    snapshot,
                    anchors=graph_anchors,
                    mode=query_mode,
                )
            if graph_projection.get("ambiguous_anchors"):
                graph_anchor_resolution = ContextAnchorResolution(
                    status=ContextAnchorStatus.AMBIGUOUS,
                    code=ContextAnchorResolutionCode.AMBIGUOUS,
                    candidates=graph_anchor_resolution.candidates,
                )
                completeness["graph_anchor"] = graph_anchor_resolution.to_dict()
            elif graph_projection.get("unresolved_anchors"):
                graph_anchor_resolution = ContextAnchorResolution(
                    status=ContextAnchorStatus.UNRESOLVED,
                    code=ContextAnchorResolutionCode.UNRESOLVED,
                    candidates=graph_anchor_resolution.candidates,
                )
                completeness["graph_anchor"] = graph_anchor_resolution.to_dict()
            graph_projection = _fresh_graph_projection(
                graph_projection,
                stale_paths=stale_paths,
                task_history_stale=_task_history_stale(freshness),
            )
            if index_available and graph_anchor_resolution.status == ContextAnchorStatus.RESOLVED:
                projected_paths = {
                    f"{target.display_path.rstrip('/')}/{path}"
                    for path in [*seed_paths, *[str(path) for path in graph_projection.get("related_paths", [])]]
                    if path and path not in stale_paths
                }
                chunks, chunk_problems = evidence_chunks_for_paths(
                    root,
                    target=target,
                    workspace_paths=projected_paths,
                    kinds={"current_source", "config"},
                    database_path=evidence_index_path,
                )
                problems.extend(chunk_problems)
        if graph_anchor_resolution.status == ContextAnchorStatus.RESOLVED:
            graph_candidates, graph_warnings, graph_projection = _graph_context_candidates(
                snapshot,
                chunks=chunks,
                target=target,
                source_candidates=retrieved_candidates,
                query=query,
                anchor_resolution=graph_anchor_resolution,
                projection=graph_projection,
            )
        else:
            graph_candidates = []
            graph_warnings = (
                [_graph_anchor_warning(graph_anchor_resolution)]
                if graph_anchor_resolution.status == ContextAnchorStatus.AMBIGUOUS
                else []
            )
    else:
        graph_candidates, graph_warnings = [], []
    graph_warnings.extend(
        context_graph_freshness_warnings(
            freshness,
            freshness_problems=freshness_problems,
        )
    )
    startup_priorities = _startup_source_priority(root, target)
    if index_available and query_mode == "startup_reading":
        startup_chunks, startup_problems = evidence_chunks_for_paths(
            root,
            target=target,
            workspace_paths=set(startup_priorities),
            database_path=evidence_index_path,
        )
        problems.extend(startup_problems)
        startup_chunks = [chunk for chunk in startup_chunks if chunk.source_ref.path not in stale_workspace_paths]
        startup_chunks.extend(overlay_chunks)
    else:
        startup_chunks = chunks
    startup_candidates = _startup_query_candidates(
        startup_chunks,
        target=target,
        mode=query_mode,
        priorities=startup_priorities,
    )
    evidence = _dedupe_candidates([*startup_candidates, *graph_candidates, *retrieved_candidates])
    selection = {"evidence_count": len(evidence)}
    if graph_anchor_resolution is not None:
        selection["graph_anchor"] = graph_anchor_resolution.to_dict()
    if not knowledge_queried and query_mode in {"authority_or_contract", "invariant", "past_decision", "failure_mode"}:
        knowledge_data, knowledge_problems, knowledge_warnings = query_knowledge_records(root, repo_id=target.id, query=query, include_stale=False, limit=10, explain=explain)
        knowledge_queried = True
        problems.extend(knowledge_problems)
        problems.extend(knowledge_warnings)
    knowledge_results = knowledge_data.get("results", []) if isinstance(knowledge_data.get("results"), list) else []
    if not knowledge_paths_resolved:
        _resolve_reviewed_knowledge_paths(
            target=target,
            knowledge_results=knowledge_results,
            known_paths=_known_context_product_paths(
                target=target,
                snapshot=snapshot,
                chunks=chunks,
                overlay_chunks=overlay_chunks,
                excluded_workspace_paths=stale_workspace_paths,
            ),
        )
    groups = _context_groups(
        evidence,
        knowledge_results=knowledge_results,
        target=target,
        completeness=completeness,
        graph_warnings=graph_warnings,
        related_history=_related_path_history(
            target=target,
            evidence=evidence,
            history=graph_projection.get("history", []) if include_linked_records and isinstance(graph_projection.get("history"), list) else [],
        ),
    )
    _displayed_groups, _continuations, compact_projection = _compact_projection(
        groups,
        mode=query_mode,
        max_group_items=COMPACT_ITEM_LIMIT,
    )
    selection["compact_projection"] = compact_projection
    project_knowledge = _project_knowledge_summary(
        evidence=evidence,
        groups=groups,
        completeness=completeness,
        reviewed_knowledge=knowledge_data,
        reviewed_knowledge_queried=knowledge_queried,
    )
    bundle = ContextBundle(
        repository=target.to_dict(),
        query={"text": query, "type": "natural_language", "mode": query_mode, "explain": explain},
        source_snapshots=source_snapshots,
        completeness={
            **completeness,
            "source_count": source_count,
            "group_names": list(CONTEXT_GROUPS),
            "project_knowledge": project_knowledge,
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
    }
    normalized = aliases.get(normalized, normalized)
    if not normalized:
        return "auto"
    if normalized not in CONTEXT_MODES:
        raise RepoctlError(f"unsupported context mode: {explicit_mode}", code="invalid_context_mode", path=explicit_mode)
    return normalized


def _project_knowledge_summary(
    *,
    evidence: list[ContextCandidate],
    groups: dict[str, list[dict[str, Any]]],
    completeness: dict[str, Any],
    reviewed_knowledge: dict[str, Any],
    reviewed_knowledge_queried: bool,
) -> dict[str, Any]:
    document_kinds = {"document", "product_manifest", "verification_hint"}
    document_paths = {
        candidate.source_ref.path
        for candidate in evidence
        if candidate.source_ref.kind in document_kinds and candidate.source_ref.path
    }
    task_refs = {
        str(item.get("record_id") or "")
        for item in groups.get("related_history", [])
        if isinstance(item, dict)
    }
    task_refs.discard("")
    lifecycle = reviewed_knowledge.get("lifecycle") if isinstance(reviewed_knowledge.get("lifecycle"), dict) else {}
    return {
        "documents": {
            "loaded": True,
            "checked_count": int(completeness.get("documents_checked") or 0)
            + int(completeness.get("manifests_checked") or 0),
            "result_count": len(document_paths),
        },
        "task_history": {
            "loaded": bool(completeness.get("history_loaded")),
            "checked_receipt_count": int(completeness.get("receipts_checked") or 0)
            if completeness.get("history_loaded")
            else None,
            "result_count": len(task_refs) if completeness.get("history_loaded") else None,
        },
        "reviewed_records": {
            "queried": reviewed_knowledge_queried,
            "available_record_count": int(reviewed_knowledge.get("available_record_count") or 0)
            if reviewed_knowledge_queried
            else None,
            "result_count": int(reviewed_knowledge.get("result_count") or 0)
            if reviewed_knowledge_queried
            else None,
            "lifecycle": lifecycle if reviewed_knowledge_queried else None,
        },
    }


def _context_index_warnings(index_problems: list[Problem]) -> list[Problem]:
    return [
        Problem(
            "warning",
            "context_evidence_index_unavailable",
            f"{problem.message}; live source and document fallback was used",
            problem.path,
            problem.code,
        )
        for problem in index_problems
    ]


def _merge_retrieval_chunks(
    indexed_chunks: list[Any],
    overlay_chunks: list[Any],
    *,
    replaced_paths: set[str],
) -> list[Any]:
    merged: dict[tuple[str, str, str, str, int, int], Any] = {}
    for chunk in indexed_chunks:
        if chunk.source_ref.path not in replaced_paths:
            merged[chunk.source_ref.key()] = chunk
    for chunk in overlay_chunks:
        merged[chunk.source_ref.key()] = chunk
    return [merged[key] for key in sorted(merged)]


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


def context_graph_freshness_warnings(
    freshness: dict[str, Any],
    *,
    freshness_problems: list[Problem] | None = None,
) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if freshness.get("status") == "stale":
        stale_source_count = int(freshness.get("changed_path_count") or 0)
        stale_root_count = int(freshness.get("changed_root_path_count") or 0)
        warnings.append(
            {
                "code": "context_graph_stale",
                "message": f"Graph snapshot is stale for {stale_source_count} product path(s) and {stale_root_count} workspace evidence path(s); stale evidence was excluded or overlaid from current files",
            }
        )
    if _task_history_stale(freshness):
        warnings.append(
            {
                "code": "context_task_history_stale",
                "message": "Task receipt or artifact evidence changed after Graph materialization; related history is omitted until Graph is rebuilt",
            }
        )
    if freshness_problems:
        warnings.append(
            {
                "code": "context_graph_freshness_unavailable",
                "message": "Graph freshness could not be fully verified; rebuild Graph before relying on semantic relations",
            }
        )
    return warnings


def _retrieval_chunks(chunks: list[Any], *, mode: str, target: RepoTarget) -> list[Any]:
    if mode == "auto":
        return chunks
    if mode not in {"code_location", "call_impact", "file_impact"}:
        return chunks
    product_prefix = f"{target.display_path.rstrip('/')}/"
    allowed_kinds = {"current_source", "config", "product_manifest", "verification_hint"}
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
    mode = str(bundle.query.get("mode") or "auto")
    displayed_items, continuations, _projection_stats = _compact_projection(
        bundle.groups,
        mode=mode,
        max_group_items=max_group_items,
    )
    groups = {
        group: [_compact_group_item(item, excerpt_chars=excerpt_chars) for item in items]
        for group, items in displayed_items.items()
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
        "bundle_digest": bundle.bundle_digest,
    }


def _compact_projection(
    groups: dict[str, list[dict[str, Any]]],
    *,
    mode: str,
    max_group_items: int,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, Any]]:
    group_limits = {
        "must_read": 2,
        "likely_change_surface": 2,
        "callers_and_dependents": 1,
        "tests_and_verification": 1,
        "reviewed_knowledge": 1,
        "related_history": 1,
        "supporting_evidence": 1,
        "warnings_and_completeness": 1,
    }
    if mode == "auto":
        group_limits["likely_change_surface"] = 3
    elif mode == "startup_reading":
        group_limits["must_read"] = 5
    elif mode in {"authority_or_contract", "invariant"}:
        group_limits["must_read"] = 3
    return _compact_bundle_projection(
        groups,
        group_limits=group_limits,
        max_group_items=max_group_items,
        item_limit=COMPACT_ITEM_LIMIT,
        continuation_limit=COMPACT_CONTINUATION_LIMIT,
        mode=mode,
    )


def _compact_completeness(completeness: dict[str, Any]) -> dict[str, Any]:
    graph = completeness.get("graph_completeness") if isinstance(completeness.get("graph_completeness"), dict) else {}
    compact = {
        "graph_available": bool(completeness.get("graph_available")),
        "graph_freshness": compact_graph_freshness(completeness.get("graph_freshness")),
        "status": str(graph.get("status") or ("unavailable" if not completeness.get("graph_available") else "partial")),
    }
    project_knowledge = completeness.get("project_knowledge")
    if isinstance(project_knowledge, dict):
        compact["project_knowledge"] = project_knowledge
    anchor = completeness.get("graph_anchor") if isinstance(completeness.get("graph_anchor"), dict) else {}
    if anchor:
        anchors = anchor.get("anchors") if isinstance(anchor.get("anchors"), list) else []
        candidates = anchor.get("candidates") if isinstance(anchor.get("candidates"), list) else []
        compact["graph_anchor"] = {
            "status": str(anchor.get("status") or "unresolved"),
            "code": str(anchor.get("code") or ContextAnchorResolutionCode.UNRESOLVED.value),
            "seed_paths": [
                str((item.get("anchor") or {}).get("path") or "")
                for item in anchors
                if isinstance(item, dict) and isinstance(item.get("anchor"), dict) and str(item["anchor"].get("path") or "")
            ],
            "candidate_paths": list(
                dict.fromkeys(
                    str((item.get("anchor") or {}).get("path") or "")
                    for item in candidates[:5]
                    if isinstance(item, dict) and isinstance(item.get("anchor"), dict) and str(item["anchor"].get("path") or "")
                )
            ),
        }
    return compact


def _compact_group_item(item: dict[str, Any], *, excerpt_chars: int) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("record_id", "code", "document_role", "evidence_role"):
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
    provenance = item.get("provenance")
    if isinstance(provenance, dict) and provenance:
        compact["provenance"] = provenance
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
    item_limit: int,
    continuation_limit: int,
    mode: str,
) -> tuple[dict[str, list[dict[str, Any]]], list[dict[str, Any]], dict[str, Any]]:
    """Project evidence and its producer-owned primary continuations together."""
    group_names = _ordered_context_group_names(all_groups)
    displayed_groups: dict[str, list[dict[str, Any]]] = {group: [] for group in group_names}
    selected_continuations: list[dict[str, Any]] = []
    secondary_continuations: list[dict[str, Any]] = []

    warning_codes = {
        "context_graph_stale",
        "context_graph_unavailable",
        "context_task_history_stale",
        "context_graph_freshness_unavailable",
        ContextAnchorResolutionCode.AMBIGUOUS.value,
        "context_graph_anchor_snapshot_unresolved",
    }
    warning_priority = {
        "context_graph_stale": 0,
        "context_graph_unavailable": 1,
        "context_graph_freshness_unavailable": 2,
        "context_task_history_stale": 3,
        "context_graph_anchor_snapshot_unresolved": 4,
        ContextAnchorResolutionCode.AMBIGUOUS.value: 5,
    }
    displayed_groups["warnings_and_completeness"] = sorted(
        (
            item
            for item in all_groups.get("warnings_and_completeness", [])
            if isinstance(item, dict) and str(item.get("code") or "") in warning_codes
        ),
        key=lambda item: warning_priority.get(str(item.get("code") or ""), 99),
    )[:1]

    code_first = ["likely_change_surface", "tests_and_verification", "callers_and_dependents", "must_read", "reviewed_knowledge", "related_history", "supporting_evidence"]
    authority_first = ["must_read", "reviewed_knowledge", "related_history", "likely_change_surface", "tests_and_verification", "callers_and_dependents", "supporting_evidence"]
    lane_order = authority_first if mode in {"startup_reading", "authority_or_contract", "past_decision", "invariant", "failure_mode"} else code_first
    projection_groups = dict(all_groups)
    projection_groups["must_read"] = _role_diverse_must_read_items(all_groups.get("must_read", []))
    selected_item_count = 0

    def try_add(group: str, item: dict[str, Any]) -> bool:
        nonlocal selected_continuations, selected_item_count
        raw_values = item.get("continuations")
        if not isinstance(raw_values, list) or not raw_values or not isinstance(raw_values[0], dict):
            return False
        primary_values = _dedupe_continuations([raw_values[0]])
        if len(primary_values) != 1:
            return False
        item_values = _dedupe_continuations([primary_values[0], *[value for value in raw_values[1:] if isinstance(value, dict)]])
        reserved = _dedupe_continuations([*selected_continuations, item_values[0]])
        if len(reserved) > continuation_limit or selected_item_count >= item_limit:
            return False
        selected_continuations = reserved
        displayed_groups[group].append(item)
        secondary_continuations.extend(item_values[1:])
        selected_item_count += 1
        return True

    positions = {group: 0 for group in lane_order}
    for group in lane_order:
        limit = min(max_group_items, group_limits.get(group, max_group_items))
        initial_limit = min(
            2 if group == "must_read" and _has_authority_and_procedure(projection_groups[group]) else 1,
            limit,
        )
        while positions[group] < len(projection_groups.get(group, [])) and len(displayed_groups[group]) < initial_limit:
            item = projection_groups[group][positions[group]]
            positions[group] += 1
            if isinstance(item, dict):
                try_add(group, item)

    for group in lane_order:
        limit = min(max_group_items, group_limits.get(group, max_group_items))
        while selected_item_count < item_limit and positions[group] < len(projection_groups.get(group, [])) and len(displayed_groups[group]) < limit:
            item = projection_groups[group][positions[group]]
            positions[group] += 1
            if isinstance(item, dict):
                try_add(group, item)

    for continuation in secondary_continuations:
        expanded = _dedupe_continuations([*selected_continuations, continuation])
        if len(expanded) <= continuation_limit:
            selected_continuations = expanded

    total_item_count = sum(len(items) for items in all_groups.values())
    displayed_item_count = sum(len(items) for items in displayed_groups.values())
    all_values = _collect_bundle_continuations(all_groups)
    return displayed_groups, selected_continuations, {
        "items": {
            "total": total_item_count,
            "displayed": displayed_item_count,
            "omitted": max(0, total_item_count - displayed_item_count),
        },
        "continuations": {
            "total": len(all_values),
            "displayed": len(selected_continuations),
            "omitted": max(0, len(all_values) - len(selected_continuations)),
        },
    }


def _role_diverse_must_read_items(items: list[dict[str, Any]]) -> list[dict[str, Any]]:
    authority_roles = {role.value for role in AUTHORITY_DOCUMENT_ROLES}
    authority_index = next(
        (index for index, item in enumerate(items) if str(item.get("document_role") or "") in authority_roles),
        None,
    )
    procedure_index = next(
        (index for index, item in enumerate(items) if item.get("document_role") == DocumentRole.PROCEDURE.value),
        None,
    )
    if authority_index is None or procedure_index is None:
        return items
    reserved = sorted({authority_index, procedure_index})
    return [*(items[index] for index in reserved), *(item for index, item in enumerate(items) if index not in reserved)]


def _has_authority_and_procedure(items: list[dict[str, Any]]) -> bool:
    roles = {str(item.get("document_role") or "") for item in items}
    return bool(roles & {role.value for role in AUTHORITY_DOCUMENT_ROLES}) and DocumentRole.PROCEDURE.value in roles


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


def _known_context_product_paths(
    *,
    target: RepoTarget,
    snapshot: Any,
    chunks: list[Any],
    overlay_chunks: list[Any],
    excluded_workspace_paths: set[str] | None = None,
) -> set[str]:
    product_prefix = f"{target.display_path.rstrip('/')}/"
    excluded_repo_paths = {
        path.removeprefix(product_prefix)
        for path in (excluded_workspace_paths or set())
        if path.startswith(product_prefix)
    }
    known_paths = {
        str(node.identity.get("path") or "")
        for node in (snapshot.nodes if snapshot is not None else [])
        if node.kind == "file"
        and isinstance(node.facts.get("index"), dict)
        and str(node.identity.get("path") or "")
        and str(node.identity.get("path") or "") not in excluded_repo_paths
    }
    for chunk in chunks:
        path = chunk.source_ref.path
        if (
            chunk.source_ref.kind in ACTIONABLE_PRODUCT_KINDS
            and path.startswith(product_prefix)
            and path.removeprefix(product_prefix) not in excluded_repo_paths
        ):
            known_paths.add(path.removeprefix(product_prefix))
    for chunk in overlay_chunks:
        path = chunk.source_ref.path
        if chunk.source_ref.kind in ACTIONABLE_PRODUCT_KINDS and path.startswith(product_prefix):
            known_paths.add(path.removeprefix(product_prefix))
    return known_paths


def _resolve_reviewed_knowledge_paths(
    *,
    target: RepoTarget,
    knowledge_results: list[dict[str, Any]],
    known_paths: set[str],
) -> dict[str, list[dict[str, Any]]]:
    resolutions_by_record: dict[str, list[dict[str, Any]]] = {}
    for result in knowledge_results:
        record = result.get("record") if isinstance(result.get("record"), dict) else {}
        explicit_resolutions: list[dict[str, Any]] = []
        applicability_resolutions: list[dict[str, Any]] = []
        resolved_applicability_paths: list[str] = []
        if str(record.get("status") or "") == "reviewed":
            explicit_refs = record.get("explicit_path_refs") if isinstance(record.get("explicit_path_refs"), list) else []
            for path_ref in explicit_refs:
                if not isinstance(path_ref, dict):
                    continue
                kind = str(path_ref.get("kind") or "")
                if kind not in {
                    KnowledgeExplicitPathKind.APPLIES_TO_PATH.value,
                    KnowledgeExplicitPathKind.SOURCE_REF.value,
                }:
                    continue
                raw_path = str(path_ref.get("path") or "")
                if path_ref.get("role") != KnowledgeExplicitPathRole.CODE_ANCHOR.value:
                    explicit_resolutions.append(
                        {
                            "kind": kind,
                            "path": raw_path,
                            "status": KnowledgeExplicitPathRole.PROVENANCE_ONLY.value,
                        }
                    )
                    continue
                resolution = resolve_repo_selector_path(
                    raw_path,
                    repository_path=target.display_path,
                    known_paths=known_paths,
                )
                item = {
                    "kind": kind,
                    "path": raw_path,
                    "status": resolution.status.value,
                }
                if resolution.status == RepoSelectorStatus.RESOLVED:
                    item["resolved_path"] = resolution.path
                elif resolution.status == RepoSelectorStatus.AMBIGUOUS:
                    item["candidates"] = list(resolution.candidates)
                explicit_resolutions.append(item)
                if kind == KnowledgeExplicitPathKind.APPLIES_TO_PATH.value:
                    applicability_resolutions.append(dict(item))
                    if resolution.status == RepoSelectorStatus.RESOLVED:
                        resolved_applicability_paths.append(resolution.path)
        record_id = str(record.get("id") or "")
        if record_id:
            resolutions_by_record[record_id] = explicit_resolutions
        result["applicability_path_resolutions"] = applicability_resolutions
        result["resolved_applicability_paths"] = sorted(dict.fromkeys(resolved_applicability_paths))
    return resolutions_by_record


def _reviewed_knowledge_path_candidates(
    root: Path,
    *,
    target: RepoTarget,
    query: str,
    knowledge_results: list[dict[str, Any]],
    path_resolutions_by_record: dict[str, list[dict[str, Any]]],
    chunks: list[Any],
    overlay_chunks: list[Any],
    stale_workspace_paths: set[str],
    index_available: bool,
    evidence_index_path: Path | None,
) -> tuple[list[ContextCandidate], list[Problem]]:
    record_ids_by_path: dict[str, set[str]] = {}
    problems: list[Problem] = []
    for result in knowledge_results:
        record = result.get("record") if isinstance(result.get("record"), dict) else {}
        if str(record.get("status") or "") != "reviewed":
            continue
        match_strength = str(result.get("query_match_strength") or KnowledgeQueryMatchStrength.NONE.value)
        if match_strength not in {
            KnowledgeQueryMatchStrength.EXACT.value,
            KnowledgeQueryMatchStrength.STRONG.value,
        }:
            result["code_anchor_status"] = "ineligible_query_match"
            result["code_path_resolutions"] = []
            result["resolved_code_paths"] = []
            continue
        result["code_anchor_status"] = "eligible"
        record_id = str(record.get("id") or "")
        resolutions = [dict(item) for item in path_resolutions_by_record.get(record_id, [])]
        for item in resolutions:
            status = str(item.get("status") or "")
            raw_path = str(item.get("path") or "")
            if status == KnowledgeExplicitPathRole.PROVENANCE_ONLY.value:
                continue
            if status == RepoSelectorStatus.RESOLVED.value:
                resolved_path = str(item.get("resolved_path") or "")
                if resolved_path:
                    record_ids_by_path.setdefault(resolved_path, set()).add(record_id)
            elif status == RepoSelectorStatus.AMBIGUOUS.value:
                candidates = item.get("candidates") if isinstance(item.get("candidates"), list) else []
                problems.append(
                    Problem(
                        "warning",
                        "context_knowledge_path_ambiguous",
                        f"reviewed Knowledge path resolves to multiple current files: {candidates}",
                        raw_path,
                    )
                )
            else:
                problems.append(
                    Problem(
                        "warning",
                        "context_knowledge_path_unresolved",
                        "reviewed Knowledge code path does not resolve to a current file",
                        raw_path,
                    )
                )
        result["code_path_resolutions"] = resolutions
        result["resolved_code_paths"] = sorted(
            path
            for path, record_ids in record_ids_by_path.items()
            if record_id in record_ids
        )

    workspace_paths = {f"{target.display_path.rstrip('/')}/{path}" for path in record_ids_by_path}
    selected_chunks: list[Any] = []
    if index_available:
        fresh_paths = workspace_paths - stale_workspace_paths
        indexed_chunks, chunk_problems = evidence_chunks_for_paths(
            root,
            target=target,
            workspace_paths=fresh_paths,
            kinds=ACTIONABLE_PRODUCT_KINDS,
            database_path=evidence_index_path,
        )
        selected_chunks.extend(indexed_chunks)
        problems.extend(chunk_problems)
        selected_chunks.extend(
            chunk
            for chunk in overlay_chunks
            if chunk.source_ref.path in workspace_paths and chunk.source_ref.kind in ACTIONABLE_PRODUCT_KINDS
        )
    else:
        selected_chunks.extend(
            chunk
            for chunk in chunks
            if chunk.source_ref.path in workspace_paths and chunk.source_ref.kind in ACTIONABLE_PRODUCT_KINDS
        )

    chunks_by_path: dict[str, list[Any]] = {}
    for chunk in selected_chunks:
        chunks_by_path.setdefault(chunk.source_ref.path, []).append(chunk)
    candidates: list[ContextCandidate] = []
    for repo_path, record_ids in sorted(record_ids_by_path.items()):
        workspace_path = f"{target.display_path.rstrip('/')}/{repo_path}"
        path_chunks = chunks_by_path.get(workspace_path, [])
        if not path_chunks:
            problems.append(
                Problem(
                    "warning",
                    "context_knowledge_path_source_unavailable",
                    "reviewed Knowledge path resolved but no current Context source chunk is available",
                    workspace_path,
                )
            )
            continue
        file_chunks = [chunk for chunk in path_chunks if chunk.source_ref.section_kind == ContextSectionKind.FILE]
        chunk = sorted(
            file_chunks or path_chunks,
            key=lambda item: (item.source_ref.line_start, item.source_ref.line_end, item.source_ref.section),
        )[0]
        candidates.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=excerpt_for_query(chunk.text, query, limit=700),
                score=30.0,
                score_breakdown={"knowledge_path": 1.0},
                selection_reasons=[f"reviewed Knowledge explicit path: {', '.join(sorted(record_ids))}"],
                evidence_kinds=(ContextEvidenceKind.REVIEWED_KNOWLEDGE_PATH,),
                anchor_strength=ContextAnchorStrength.EXPLICIT,
                related_record_ids=tuple(sorted(record_ids)),
            )
        )
    return candidates, problems


def _related_path_history(*, target: RepoTarget, evidence: list[ContextCandidate], history: list[dict[str, Any]]) -> list[dict[str, Any]]:
    product_prefix = f"{target.display_path.rstrip('/')}/"
    selected_paths = {
        candidate.source_ref.path.removeprefix(product_prefix)
        for candidate in evidence
        if candidate.source_ref.kind in ACTIONABLE_PRODUCT_KINDS and candidate.source_ref.path.startswith(product_prefix)
    }
    return [item for item in history if isinstance(item, dict) and str(item.get("path") or "") in selected_paths]


def _startup_query_candidates(
    chunks: list[Any],
    *,
    target: RepoTarget,
    mode: str,
    priorities: dict[str, float],
) -> list[ContextCandidate]:
    if mode != "startup_reading":
        return []
    wanted = priorities
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
                evidence_kinds=(ContextEvidenceKind.STARTUP_READING,),
                document_role=source_document_role(
                    kind=chunk.source_ref.kind,
                    path=chunk.source_ref.path,
                    repository_path=target.display_path,
                    assigned=chunk.document_role,
                ),
            )
        )
    return sorted(selected, key=lambda candidate: (-candidate.score, candidate.source_ref.path))[:8]


def _startup_source_priority(root: Path, target: RepoTarget) -> dict[str, float]:
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
    priorities = {path: score for path, score in paths}
    for path in context_document_paths(root, target=target):
        rel = path.relative_to(root).as_posix()
        role = source_document_role(
            kind="document",
            path=rel,
            repository_path=target.display_path,
        )
        if role != DocumentRole.PRODUCT_AUTHORITY:
            continue
        priorities.setdefault(
            rel,
            27.0 if rel.startswith(f"{repo_prefix}/") else 24.0,
        )
    return priorities


def _graph_context_candidates(
    snapshot: Any,
    *,
    chunks: list[Any],
    target: RepoTarget,
    source_candidates: list[ContextCandidate],
    query: str,
    anchor_resolution: ContextAnchorResolution,
    projection: dict[str, Any],
) -> tuple[list[ContextCandidate], list[dict[str, str]], dict[str, Any]]:
    if snapshot is None:
        return [], [{"code": "context_graph_unavailable", "message": "Graph snapshot was not available for context query"}], {}
    if not source_candidates:
        return [], [], {}
    product_prefix = f"{target.display_path.rstrip('/')}/"
    source_chunks: dict[str, list[Any]] = {}
    for chunk in chunks:
        if chunk.source_ref.kind not in ACTIONABLE_PRODUCT_KINDS or not chunk.source_ref.path.startswith(product_prefix):
            continue
        source_chunks.setdefault(chunk.source_ref.path.removeprefix(product_prefix), []).append(chunk)
    retrieval_by_path: dict[str, ContextCandidate] = {}
    for candidate in source_candidates:
        path = candidate.source_ref.path
        if candidate.source_ref.kind not in ACTIONABLE_PRODUCT_KINDS or not path.startswith(product_prefix):
            continue
        repo_path = path.removeprefix(product_prefix)
        previous = retrieval_by_path.get(repo_path)
        if previous is None or _candidate_sort_key(candidate) < _candidate_sort_key(previous):
            retrieval_by_path[repo_path] = candidate
    seed_paths = list(dict.fromkeys(candidate.anchor.path for candidate in anchor_resolution.anchors))
    if anchor_resolution.status != ContextAnchorStatus.RESOLVED or not seed_paths:
        return [], [], {}

    candidates: list[ContextCandidate] = []
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

    def has_direct_anchor_origin(relation: dict[str, Any]) -> bool:
        origins = relation.get("origin_paths") if isinstance(relation.get("origin_paths"), list) else []
        return any(
            candidate is not None
            and bool(
                set(candidate.evidence_kinds)
                & {
                    ContextEvidenceKind.EXACT_PATH,
                    ContextEvidenceKind.EXACT_FILENAME,
                    ContextEvidenceKind.EXACT_SYMBOL,
                    ContextEvidenceKind.REVIEWED_KNOWLEDGE_PATH,
                }
            )
            for origin in origins
            for candidate in [retrieval_by_path.get(str(origin or ""))]
        )

    for path in projected_paths:
        path_chunks = source_chunks.get(str(path), [])
        chunk = _graph_source_chunk(path_chunks, query=query, retrieved=retrieval_by_path.get(str(path)))
        if chunk is None:
            continue
        path_relations = relations_by_path.get(str(path), [])
        scoring_relations = path_relations
        direct_anchor_dependency = any(
            str(relation.get("to_path") or "") == str(path)
            and str(relation.get("edge") or "") in {"CALLS", "IMPORTS_FILE", "TESTS_FILE", STRUCTURED_EDGE_KIND}
            and has_direct_anchor_origin(relation)
            for relation in path_relations
        )
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
                    "identity": float(retrieved.score_breakdown.get("identity", 0.0)) if retrieved is not None else 0.0,
                    "exact": float(retrieved.score_breakdown.get("exact", 0.0)) if retrieved is not None else 0.0,
                    "fts": float(retrieved.score_breakdown.get("fts", 0.0)) if retrieved is not None else 0.0,
                    "authority": float(retrieved.score_breakdown.get("authority", 0.0)) if retrieved is not None else 0.0,
                    "graph": propagated_score,
                    "direct_anchor_dependency": 1.0 if direct_anchor_dependency else 0.0,
                },
                selection_reasons=reasons or ["Graph direct file relation"],
                graph_path=scoring_relations[:3],
                evidence_kinds=retrieved.evidence_kinds if retrieved is not None else (),
                anchor_strength=retrieved.anchor_strength if retrieved is not None else ContextAnchorStrength.NONE,
                related_record_ids=retrieved.related_record_ids if retrieved is not None else (),
                document_role=retrieved.document_role if retrieved is not None else chunk.document_role,
            )
        )
    warnings = []
    if projection.get("unresolved_anchors"):
        warnings.append(
            {
                "code": "context_graph_anchor_snapshot_unresolved",
                "message": "A typed symbol anchor no longer resolved in the materialized Graph; its relations were omitted",
            }
        )
    return _dedupe_candidates(candidates), warnings, projection


_EXACT_GRAPH_ANCHOR_KINDS = {
    ContextEvidenceKind.EXACT_PATH,
    ContextEvidenceKind.EXACT_FILENAME,
    ContextEvidenceKind.EXACT_SYMBOL,
}
_EXACT_FILE_ANCHOR_KINDS = {
    ContextEvidenceKind.EXACT_PATH,
    ContextEvidenceKind.EXACT_FILENAME,
}


def _resolve_graph_anchors(
    source_candidates: list[ContextCandidate],
    *,
    target: RepoTarget,
) -> ContextAnchorResolution:
    product_prefix = f"{target.display_path.rstrip('/')}/"
    ranked: list[tuple[int, ContextCandidate, ContextGraphAnchorCandidate]] = []
    rejected: list[tuple[ContextCandidate, ContextGraphAnchorCandidate]] = []
    for candidate in source_candidates:
        path = candidate.source_ref.path
        if candidate.source_ref.kind not in ACTIONABLE_PRODUCT_KINDS or not path.startswith(product_prefix):
            continue
        repo_path = path.removeprefix(product_prefix)
        evidence_kinds = set(candidate.evidence_kinds)
        symbol_anchor = candidate.source_ref.section_kind == ContextSectionKind.PROVIDER_SYMBOL and (
            ContextEvidenceKind.EXACT_SYMBOL in evidence_kinds
            or (
                candidate.anchor_strength == ContextAnchorStrength.STRONG
                and ContextEvidenceKind.SECTION_TERMS in evidence_kinds
            )
        )
        anchor = GraphContextAnchor(
            kind=GraphContextAnchorKind.SYMBOL if symbol_anchor else GraphContextAnchorKind.FILE,
            path=repo_path,
            symbol=candidate.source_ref.section if symbol_anchor else "",
            line_start=candidate.source_ref.line_start if symbol_anchor else 0,
            line_end=candidate.source_ref.line_end if symbol_anchor else 0,
        )
        graph_candidate = ContextGraphAnchorCandidate(
            anchor=anchor,
            source_ref=candidate.source_ref,
            evidence_kinds=candidate.evidence_kinds,
            anchor_strength=candidate.anchor_strength,
            related_record_ids=candidate.related_record_ids,
        )
        tier = _graph_anchor_tier(candidate)
        if tier is None:
            rejected.append((candidate, graph_candidate))
            continue
        ranked.append((tier, candidate, graph_candidate))
    if not ranked:
        candidates = tuple(
            graph_candidate
            for _candidate, graph_candidate in sorted(
                rejected,
                key=lambda item: (
                    -CONTEXT_ANCHOR_STRENGTH_PRIORITY[item[0].anchor_strength],
                    -item[0].score,
                    item[0].source_ref.path,
                    item[0].source_ref.line_start,
                ),
            )[:5]
        )
        return ContextAnchorResolution(
            status=ContextAnchorStatus.UNRESOLVED,
            code=ContextAnchorResolutionCode.UNRESOLVED,
            candidates=candidates,
        )

    top_tier = max(tier for tier, _candidate, _graph_candidate in ranked)
    strongest = [
        (candidate, graph_candidate)
        for tier, candidate, graph_candidate in ranked
        if tier == top_tier
    ]
    if top_tier == 3:
        exact_symbols = _dedupe_graph_anchor_pairs(
            pair
            for pair in strongest
            if ContextEvidenceKind.EXACT_SYMBOL in set(pair[0].evidence_kinds)
        )
        exact_files = _dedupe_graph_anchor_pairs(
            pair
            for pair in strongest
            if set(pair[0].evidence_kinds) & _EXACT_FILE_ANCHOR_KINDS
            and ContextEvidenceKind.EXACT_SYMBOL not in set(pair[0].evidence_kinds)
        )
        if exact_symbols:
            symbol_paths = {pair[1].anchor.path for pair in exact_symbols}
            file_paths = {pair[1].anchor.path for pair in exact_files}
            if len(exact_symbols) != 1 or any(path not in symbol_paths for path in file_paths):
                return _ambiguous_graph_anchor_resolution([*exact_symbols, *exact_files])
            strongest = exact_symbols
        else:
            strongest = exact_files

    ordered = _dedupe_graph_anchor_pairs(strongest)
    if top_tier == 2:
        graph_candidates = tuple(graph_candidate for _candidate, graph_candidate in ordered)
        return ContextAnchorResolution(
            status=ContextAnchorStatus.RESOLVED,
            code=ContextAnchorResolutionCode.RESOLVED,
            anchors=graph_candidates,
            candidates=graph_candidates,
        )
    if len(ordered) != 1:
        return _ambiguous_graph_anchor_resolution(ordered)
    graph_candidates = (ordered[0][1],)
    return ContextAnchorResolution(
        status=ContextAnchorStatus.RESOLVED,
        code=ContextAnchorResolutionCode.RESOLVED,
        anchors=graph_candidates,
        candidates=graph_candidates,
    )


def _graph_anchor_tier(candidate: ContextCandidate) -> int | None:
    kinds = set(candidate.evidence_kinds)
    if (
        ContextEvidenceKind.REVIEWED_KNOWLEDGE_PATH in kinds
        and candidate.anchor_strength == ContextAnchorStrength.EXPLICIT
    ):
        return 2
    if kinds & _EXACT_GRAPH_ANCHOR_KINDS and candidate.anchor_strength in {
        ContextAnchorStrength.EXACT,
        ContextAnchorStrength.EXPLICIT,
    }:
        return 3
    eligible_strong_section = (
        candidate.anchor_strength == ContextAnchorStrength.STRONG
        and candidate.source_ref.section_kind == ContextSectionKind.PROVIDER_SYMBOL
        and ContextEvidenceKind.SECTION_TERMS in kinds
    )
    return 1 if eligible_strong_section else None


def _dedupe_graph_anchor_pairs(
    pairs: Iterable[tuple[ContextCandidate, ContextGraphAnchorCandidate]],
) -> list[tuple[ContextCandidate, ContextGraphAnchorCandidate]]:
    unique: dict[tuple[Any, ...], tuple[ContextCandidate, ContextGraphAnchorCandidate]] = {}
    for candidate, graph_candidate in pairs:
        anchor = graph_candidate.anchor
        identity = anchor.key() if anchor.kind == GraphContextAnchorKind.SYMBOL else (anchor.kind.value, anchor.path)
        previous = unique.get(identity)
        if previous is None or candidate.score > previous[0].score:
            unique[identity] = (candidate, graph_candidate)
    return [
        pair
        for _identity, pair in sorted(
            unique.items(),
            key=lambda item: (
                item[1][1].anchor.path,
                item[1][1].anchor.symbol,
                item[1][1].anchor.line_start,
                item[1][1].anchor.line_end,
            ),
        )
    ]


def _ambiguous_graph_anchor_resolution(
    pairs: list[tuple[ContextCandidate, ContextGraphAnchorCandidate]],
) -> ContextAnchorResolution:
    return ContextAnchorResolution(
        status=ContextAnchorStatus.AMBIGUOUS,
        code=ContextAnchorResolutionCode.AMBIGUOUS,
        candidates=tuple(graph_candidate for _candidate, graph_candidate in pairs),
    )


def _graph_anchor_warning(resolution: ContextAnchorResolution) -> dict[str, str]:
    return {
        "code": resolution.code.value,
        "message": "Multiple equally strong Graph anchors remain; no relation expansion was performed",
    }


def _graph_source_chunk(chunks: list[Any], *, query: str, retrieved: ContextCandidate | None) -> Any | None:
    del query
    if not chunks:
        return None
    if retrieved is not None:
        for chunk in chunks:
            if chunk.source_ref.key() == retrieved.source_ref.key():
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
        evidence_kinds=(ContextEvidenceKind.GRAPH_RELATION,),
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
    best: dict[tuple[str, str, str, str, int, int], ContextCandidate] = {}
    for candidate in candidates:
        key = candidate.source_ref.key()
        previous = best.get(key)
        if previous is None or _candidate_sort_key(candidate) < _candidate_sort_key(previous):
            best[key] = candidate
    return sorted(best.values(), key=_candidate_sort_key)


def _candidate_sort_key(candidate: ContextCandidate) -> tuple[int, int, float, str, int]:
    breakdown = candidate.score_breakdown
    direct_query_evidence = _has_direct_query_evidence(candidate)
    stage = 0 if direct_query_evidence else 1 if float(breakdown.get("graph") or 0.0) > 0 else 2
    return (
        stage,
        -CONTEXT_ANCHOR_STRENGTH_PRIORITY[candidate.anchor_strength],
        -candidate.score,
        candidate.source_ref.path,
        candidate.source_ref.line_start,
    )


def _has_direct_query_evidence(candidate: ContextCandidate) -> bool:
    return bool(set(candidate.evidence_kinds) - {ContextEvidenceKind.GRAPH_RELATION})


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
        if group == "related_history":
            continue
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
                "provenance": record.get("provenance", {}) if isinstance(record.get("provenance"), dict) else {},
                "source_ref": {"kind": "knowledge_record", "path": f"docs/knowledge/records/{record.get('id', '')}.json", "content_sha256": record.get("record_digest", "")},
                "continuations": _knowledge_continuations(result),
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
    recorded_task_ids = {str(item.get("record_id") or "") for item in groups["related_history"]}
    for item in _path_group_items(grouped_candidates["related_history"], group="related_history", target=target):
        ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        path = str(ref.get("path") or "")
        task_id = _task_id_from_history_item(item)
        if task_id and task_id in recorded_task_ids:
            continue
        item["record_id"] = task_id or path
        item["status"] = "recorded"
        item["selection_reason"] = "task history query match"
        item["continuations"] = _task_history_continuations(task_id=task_id, task_path=path) if task_id else item.get("continuations", [])
        groups["related_history"].append(item)
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


def _task_id_from_history_item(item: dict[str, Any]) -> str:
    sections = item.get("sections") if isinstance(item.get("sections"), list) else []
    source_ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
    selectors = [
        *(
            str(section.get("section") or "")
            for section in sections
            if isinstance(section, dict)
        ),
        str(source_ref.get("path") or ""),
    ]
    for selector in selectors:
        try:
            return normalize_task_id(selector)
        except RepoctlError:
            continue
    return ""


def _candidate_group(candidate: ContextCandidate) -> str:
    ref = candidate.source_ref
    path = ref.path.lower()
    document_role = candidate.document_role
    if candidate.score_breakdown.get("startup_reading"):
        return "must_read"
    if ref.kind in ACTIONABLE_PRODUCT_KINDS:
        if is_test_path(path):
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
        if any(is_test_path(path) for path in paths):
            return "tests_and_verification"
        return "callers_and_dependents" if candidate.graph_path else "supporting_evidence"
    if document_role in AUTHORITY_DOCUMENT_ROLES or document_role == DocumentRole.PROCEDURE:
        return "must_read"
    if ref.kind in {"completion_receipt", "task_artifact"}:
        return "related_history"
    if ref.kind == "verification_hint" or is_test_path(path):
        return "tests_and_verification"
    return "supporting_evidence"


def _candidate_group_item(candidate: ContextCandidate, *, target: RepoTarget, status: str) -> dict[str, Any]:
    source_ref = {
        "kind": candidate.source_ref.kind,
        "path": candidate.source_ref.path,
        "content_sha256": candidate.source_ref.content_sha256,
    }
    roles = _candidate_evidence_roles(candidate, target=target)
    item = {
        "repo_id": target.id,
        "status": status,
        "source_ref": source_ref,
        "sections": [_candidate_section(candidate)],
        "content_sha256": candidate.source_ref.content_sha256,
        "selection_reason": "; ".join(candidate.selection_reasons) or "retrieval match",
        "selection_reasons": sorted(set(candidate.selection_reasons)) or ["retrieval match"],
        "score": candidate.score,
        "score_breakdown": candidate.score_breakdown,
        "anchor_strength": candidate.anchor_strength.value,
        "evidence_kinds": sorted(kind.value for kind in set(candidate.evidence_kinds)),
        "excerpt": candidate.text,
        "graph_path": candidate.graph_path,
        "continuations": _candidate_continuations(candidate, target=target),
        "evidence_role": roles[0],
        "evidence_roles": roles,
    }
    if candidate.document_role != DocumentRole.UNSPECIFIED:
        item["document_role"] = candidate.document_role.value
    return item


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
    evidence_kinds: set[ContextEvidenceKind] = set()
    anchor_strength = ContextAnchorStrength.NONE
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
        evidence_kinds.update(candidate.evidence_kinds)
        if CONTEXT_ANCHOR_STRENGTH_PRIORITY[candidate.anchor_strength] > CONTEXT_ANCHOR_STRENGTH_PRIORITY[anchor_strength]:
            anchor_strength = candidate.anchor_strength
    ordered_roles = sorted(roles, key=lambda role: (_evidence_role_priority(role), role))
    item.update(
        {
            "sections": [sections[key] for key in sorted(sections)],
            "selection_reason": "; ".join(sorted(reasons)[:4]),
            "selection_reasons": sorted(reasons),
            "score": max(candidate.score for candidate in ranked),
            "score_breakdown": breakdown,
            "anchor_strength": anchor_strength.value,
            "evidence_kinds": sorted(kind.value for kind in evidence_kinds),
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
    if ref.kind in ACTIONABLE_PRODUCT_KINDS:
        is_test = is_test_path(lowered)
        if ref.kind == "config":
            roles.add("configuration")
        if ContextEvidenceKind.REVIEWED_KNOWLEDGE_PATH in candidate.evidence_kinds:
            roles.add("knowledge_linked_test" if is_test else "knowledge_linked_source")
        elif _has_direct_query_evidence(candidate):
            roles.add("test_candidate" if is_test else "change_candidate")
        for relation in candidate.graph_path:
            if not isinstance(relation, dict):
                continue
            edge = str(relation.get("edge") or "")
            from_path = str(relation.get("from_path") or "")
            to_path = str(relation.get("to_path") or "")
            other_path = to_path if from_path == repo_path else from_path if to_path == repo_path else ""
            if is_test and other_path and not is_test_path(other_path) and edge in {"CALLS", "IMPORTS_FILE", "TESTS_FILE", STRUCTURED_EDGE_KIND}:
                roles.add("directly_connected_test")
            if to_path == repo_path and from_path != repo_path:
                if edge in {"IMPORTS_FILE", "TESTS_FILE"}:
                    roles.add("imported_dependency")
                elif edge == "CALLS":
                    roles.add("called_dependency")
                elif edge == STRUCTURED_EDGE_KIND:
                    roles.add("structured_dependency")
            elif from_path == repo_path and to_path != repo_path and edge in {"CALLS", "IMPORTS_FILE", "TESTS_FILE", STRUCTURED_EDGE_KIND}:
                roles.add("dependent_source")
        if not roles:
            roles.add("supporting_evidence")
    elif ref.kind == "product_manifest":
        roles.add("product_manifest")
    elif ref.kind == "verification_hint":
        roles.add("verification_hint")
    elif ref.kind == "graph_relation":
        roles.add("code_relation")
    elif candidate.document_role in AUTHORITY_DOCUMENT_ROLES:
        roles.add("authority_document")
    elif candidate.document_role == DocumentRole.PROCEDURE:
        roles.add("procedure_document")
    elif candidate.document_role == DocumentRole.REFERENCE:
        roles.add("reference_document")
    else:
        roles.add("supporting_evidence")
    return sorted(roles, key=lambda role: (_evidence_role_priority(role), role))


def _evidence_role_priority(role: str) -> int:
    priorities = {
        "change_candidate": 0,
        "test_candidate": 0,
        "authority_document": 0,
        "procedure_document": 0,
        "directly_connected_test": 1,
        "knowledge_linked_source": 1,
        "knowledge_linked_test": 1,
        "imported_dependency": 1,
        "called_dependency": 1,
        "structured_dependency": 1,
        "product_manifest": 1,
        "configuration": 1,
        "dependent_source": 2,
        "code_relation": 2,
        "reference_document": 3,
        "verification_hint": 4,
        "supporting_evidence": 5,
    }
    return priorities.get(role, 9)


def _group_item_sort_key(
    group: str,
    item: dict[str, Any],
    *,
    direct_query_score: float,
) -> tuple[int, int, int, float, float, str]:
    role = str(item.get("evidence_role") or "")
    roles = {str(value) for value in item.get("evidence_roles", []) if str(value)} if isinstance(item.get("evidence_roles"), list) else {role}
    breakdown = item.get("score_breakdown") if isinstance(item.get("score_breakdown"), dict) else {}
    role_priority = _evidence_role_priority(role)
    try:
        anchor_strength = ContextAnchorStrength(str(item.get("anchor_strength") or ContextAnchorStrength.NONE.value))
    except ValueError:
        anchor_strength = ContextAnchorStrength.NONE
    anchor_priority = CONTEXT_ANCHOR_STRENGTH_PRIORITY[anchor_strength]
    if group == "callers_and_dependents":
        role_priority = 0 if role == "code_relation" else role_priority
    direct_query_stage = 0
    if group in {"likely_change_surface", "tests_and_verification"}:
        graph_score = float(breakdown.get("graph") or 0.0)
        if anchor_strength in {ContextAnchorStrength.EXPLICIT, ContextAnchorStrength.EXACT}:
            direct_query_stage = 0
        elif float(breakdown.get("direct_anchor_dependency") or 0.0) > 0:
            direct_query_stage = 1
        elif anchor_strength == ContextAnchorStrength.STRONG:
            direct_query_stage = 2
        elif direct_query_score > 0 and graph_score <= 0:
            direct_query_stage = 3
        elif direct_query_score > 0:
            direct_query_stage = 4
        elif roles & {"imported_dependency", "called_dependency", "structured_dependency", "directly_connected_test"}:
            direct_query_stage = 5
        else:
            direct_query_stage = 6
    return (
        direct_query_stage,
        -anchor_priority,
        role_priority,
        -direct_query_score,
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
    if candidate.source_ref.kind in ACTIONABLE_PRODUCT_KINDS:
        repo_path = path.removeprefix(product_prefix) if path.startswith(product_prefix) else ""
        return _file_continuation(repo_path) if repo_path else None
    if candidate.source_ref.kind != "graph_relation":
        return _document_continuation(path) if path and not path.startswith("<") else None
    if not candidate.graph_path or not isinstance(candidate.graph_path[0], dict):
        return None
    relation = candidate.graph_path[0]
    edge = str(relation.get("edge") or "")
    from_path = str(relation.get("from_path") or "")
    if edge in {"IMPORTS_FILE", "TESTS_FILE", STRUCTURED_EDGE_KIND}:
        return _file_continuation(from_path) if from_path else None
    if edge != "CALLS" or not from_path:
        return None
    from_symbol = relation.get("from_symbol") if isinstance(relation.get("from_symbol"), dict) else {}
    qualified_name = str(from_symbol.get("qualified_name") or from_symbol.get("name") or "")
    return _symbol_continuation(qualified_name, in_file=from_path) if qualified_name else None


def _knowledge_continuations(result: dict[str, Any]) -> list[dict[str, Any]]:
    record = result.get("record") if isinstance(result.get("record"), dict) else {}
    record_id = str(record.get("id") or "")
    if not record_id:
        return []
    continuations = [
        {
            "selector": {"kind": "knowledge_record", "value": record_id},
            "actions": ["knowledge.show"],
        }
    ]
    for path in result.get("resolved_applicability_paths", []):
        if isinstance(path, str) and path:
            continuations.append(_file_continuation(path, include_impact=False))
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


def _file_continuation(path: str, *, include_impact: bool = True) -> dict[str, Any]:
    actions = ["workspace.open", "graph.file"]
    if include_impact:
        actions.append("graph.impact_file")
    return {
        "selector": {"kind": "file", "value": path},
        "actions": actions,
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

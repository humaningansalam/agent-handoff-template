from __future__ import annotations

from collections.abc import Iterable
from dataclasses import dataclass
from pathlib import Path
import re
from typing import Any

from .context_chunks import chunk_text_source
from .context_model import (
    CONTEXT_ANCHOR_STRENGTH_PRIORITY,
    CONTEXT_SOURCE_KIND_VALUES,
    LEXICAL_CONTEXT_SOURCE_KIND_VALUES,
    ContextAnchorResolution,
    ContextAnchorResolutionCode,
    ContextAnchorStatus,
    ContextAnchorStrength,
    ContextBundle,
    ContextCandidate,
    ContextEvidenceKind,
    ContextGraphAnchorCandidate,
    ContextGraphAnchorProvenance,
    ContextGraphSeedRef,
    ContextEvidenceProjection,
    ContextResultMode,
    ContextSectionKind,
    ContextSourceKind,
    ContextSourceRef,
)
from .context_retrieval import (
    ContextRetrievalLane,
    canonical_identifier_sequence,
    context_query_terms,
    context_retrieval_lane,
    excerpt_for_query,
    provider_symbol_evidence_candidate,
    provider_symbol_evidence_supported,
    provider_symbol_query_supported,
    rank_context_chunks,
    retrieve_context_balanced,
)
from .context_sources import collect_context_sources, context_document_paths, context_graph_problems, context_overlay_chunks, current_source_chunks_for_paths
from .completion_catalogue import (
    CompletionCatalogueUnavailable,
    current_completion_frontiers,
    search_completion_history,
    versioned_completion_subject_key,
)
from .component_projection import component_projection
from .discovery_outcomes import current_path_subject
from .document_roles import AUTHORITY_DOCUMENT_ROLES, DocumentRole, source_document_role
from .evidence_store import evidence_chunks_for_paths, query_evidence_index
from .graph import build_context_projection_index, compact_relationship_candidates, context_path_support_profiles, graph_anchor_continuation, project_context_neighborhood, relationship_candidates_for_paths
from .graph_model import GraphContextAnchor, GraphContextAnchorKind, digest_data, symbol_id
from .graph_store import compact_graph_freshness, graph_materialization_freshness, graph_stale_paths, load_materialized_graph
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
    "prior_task_outcome",
    "related_history",
    "supporting_evidence",
    "warnings_and_completeness",
)
GRAPH_EXPANSION_MODES = {"auto", "code_location", "call_impact", "file_impact"}
COMPACT_ITEM_LIMIT = 8
COMPACT_CONTINUATION_LIMIT = 8
GRAPH_ANCHOR_LIMIT = 3
PRIOR_TASK_OUTCOME_SUBJECT_LIMIT = 4
PRIOR_TASK_OUTCOME_RECORD_LIMIT = 4
EXPLICIT_TASK_HISTORY_LIMIT = 2
_TASK_ID_QUERY_RE = re.compile(r"T-[0-9]{14}Z")
ACTIONABLE_PRODUCT_KINDS = CONTEXT_SOURCE_KIND_VALUES
LEXICAL_GRAPH_ANCHOR_KINDS = LEXICAL_CONTEXT_SOURCE_KIND_VALUES
GRAPH_DERIVED_CONTEXT_EVIDENCE_KINDS = {
    ContextEvidenceKind.GRAPH_SEED,
    ContextEvidenceKind.GRAPH_RELATION,
    ContextEvidenceKind.HISTORY_CORROBORATION,
}
_LEXICAL_QUERY_EVIDENCE_KINDS = {
    ContextEvidenceKind.PATH_TERMS,
    ContextEvidenceKind.SECTION_TERMS,
    ContextEvidenceKind.BODY_TERMS,
    ContextEvidenceKind.FTS,
}
_EXACT_GRAPH_ANCHOR_KINDS = {
    ContextEvidenceKind.EXACT_PATH,
    ContextEvidenceKind.EXACT_FILENAME,
    ContextEvidenceKind.EXACT_SYMBOL,
    ContextEvidenceKind.EXACT_RELATIONSHIP,
}
_NAMED_GRAPH_ANCHOR_KINDS = {
    ContextEvidenceKind.NAMED_FILE_IDENTITY,
    ContextEvidenceKind.NAMED_SYMBOL_IDENTITY,
}
_EXPLICIT_NAMED_GRAPH_ANCHOR_KINDS = {
    ContextEvidenceKind.EXPLICIT_NAMED_FILE_IDENTITY,
    ContextEvidenceKind.EXPLICIT_NAMED_SYMBOL_IDENTITY,
}


def _component_ids_by_path(component_data: dict[str, Any]) -> dict[str, tuple[str, ...]]:
    paths = component_data.get("paths")
    if not isinstance(paths, dict):
        return {}
    return {
        str(path): tuple(
            sorted(
                {
                    str(component_id)
                    for component_id in component_ids
                    if isinstance(component_id, str) and component_id
                }
            )
        )
        for path, component_ids in paths.items()
        if isinstance(path, str) and isinstance(component_ids, list)
    }


def _context_component_crossings(
    graph_projection: dict[str, Any],
    *,
    selected_paths: set[str],
) -> list[dict[str, Any]]:
    values = (
        graph_projection.get("component_crossings")
        if isinstance(graph_projection.get("component_crossings"), list)
        else []
    )
    return [
        dict(crossing)
        for crossing in values
        if isinstance(crossing, dict)
        and {
            _coverage_graph_repo_path(str(crossing.get("from_path") or "")),
            _coverage_graph_repo_path(str(crossing.get("to_path") or "")),
        }
        <= selected_paths
    ]


def _coverage_profile_component_ids(profile: dict[str, Any]) -> frozenset[str]:
    raw = profile.get("component_ids")
    if not isinstance(raw, (list, tuple, set, frozenset)):
        return frozenset()
    return frozenset(
        str(component_id)
        for component_id in raw
        if isinstance(component_id, str) and component_id
    )


def _coverage_selected_component_ids(
    profiles: Iterable[dict[str, Any]],
) -> frozenset[str]:
    return frozenset(
        component_id
        for profile in profiles
        for component_id in _coverage_profile_component_ids(profile)
    )


CONTEXT_GRAPH_FRESHNESS_WARNING_CODES = frozenset(
    {
        "context_graph_stale",
        "context_task_history_stale",
        "context_graph_freshness_unavailable",
    }
)


@dataclass(frozen=True)
class _CoverageContribution:
    new_pairs: frozenset[tuple[str, str]]
    new_lane: str
    new_roles: frozenset[str]
    new_components: frozenset[str]
    identity: bool
    lexical: bool
    component: bool

    @property
    def contributes(self) -> bool:
        return bool(
            self.identity
            or self.lexical
            or self.new_lane
            or self.new_roles
            or self.component
        )


@dataclass(frozen=True)
class _FinalContextProjection:
    groups: dict[str, list[dict[str, Any]]]
    continuations: list[dict[str, Any]]
    stats: dict[str, Any]


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
    component_data = component_projection(snapshot) if snapshot is not None else {}
    component_ids_by_path = _component_ids_by_path(component_data)
    explicit_history = include_linked_records and query_mode in {"past_decision", "failure_mode"}
    indexed_chunks: list[Any] = []
    index_meta: dict[str, Any] = {}
    index_problems: list[Problem] = []
    index_available = False
    stale_repo_paths = graph_stale_paths(freshness)
    stale_workspace_paths = {
        *{
            f"{target.display_path.rstrip('/')}/{path}"
            for path in stale_repo_paths
        },
        *{
            str(path)
            for path in freshness.get("changed_root_paths", [])
            if str(path)
        },
    }
    stale_path_classifications = (
        freshness.get("stale_path_classifications")
        if isinstance(freshness.get("stale_path_classifications"), dict)
        else freshness.get("changed_path_classifications")
        if isinstance(freshness.get("changed_path_classifications"), dict)
        else {}
    )
    overlay_chunks: list[Any] = []
    evidence_index_path = graph_state_root / target.id / "evidence.sqlite3" if graph_state_root is not None else None
    if snapshot is not None:
        overlay_chunks, overlay_problems = current_source_chunks_for_paths(
            root,
            target=target,
            repo_paths={
                path
                for path in stale_repo_paths
                if str(stale_path_classifications.get(path) or "") != "excluded"
            },
        )
        problems = [*overlay_problems]
        static_overlay_chunks, static_overlay_problems = context_overlay_chunks(
            root,
            target=target,
            workspace_paths=stale_workspace_paths,
            include_history=False,
        )
        problems.extend(static_overlay_problems)
        overlay_chunks.extend(static_overlay_chunks)
        indexed_chunks, index_meta, index_problems = query_evidence_index(
            root,
            target=target,
            query=query,
            mode=query_mode,
            snapshot_digest=snapshot.snapshot_digest,
            graph_input_digest=str(materialization.get("input_digest") or ""),
            limit=24,
            database_path=evidence_index_path,
            overlay_chunks=overlay_chunks,
            replaced_paths=stale_workspace_paths,
        )
        index_available = not any(problem.severity == "error" for problem in index_problems)
    if index_available:
        chunks: list[Any] = []
        indexed_chunk_counts = index_meta.get("chunk_counts") if isinstance(index_meta.get("chunk_counts"), dict) else {}
        indexed_source_path_counts = index_meta.get("source_path_counts") if isinstance(index_meta.get("source_path_counts"), dict) else {}
        source_count = sum(int(value or 0) for value in indexed_chunk_counts.values())
        source_snapshots = {
            key: str(index_meta.get(key) or "")
            for key in ("document_manifest_digest", "current_source_manifest_digest", "snapshot_digest")
            if index_meta.get(key)
        }
        receipt_problems = snapshot.completeness.get("receipt_problems", []) if snapshot is not None else []
        completeness = {
            "documents_checked": int(indexed_source_path_counts.get("document") or 0),
            "manifests_checked": int(indexed_source_path_counts.get("product_manifest") or 0),
            "receipts_checked": int(indexed_source_path_counts.get("completion_receipt") or 0),
            "current_sources_checked": int(indexed_source_path_counts.get("current_source") or 0)
            + int(indexed_source_path_counts.get("config") or 0)
            + int(indexed_source_path_counts.get("structured_data") or 0),
            "history_loaded": False,
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
        problems.extend(problem for problem in index_problems if problem.severity != "error")
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
        problems.extend(
            context_graph_problems(
                [
                    *(
                        problem
                        for problem in graph_problems
                        if problem not in evidence_index_problems
                    ),
                    *receipt_graph_problems,
                ],
                graph_available=snapshot is not None,
            )
        )
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
            include_history=False,
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
    explicit_history_candidates: list[ContextCandidate] = []
    completeness["graph_freshness"] = freshness
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
    graph_projection_index: Any = (
        build_context_projection_index(snapshot)
        if snapshot is not None and query_mode in GRAPH_EXPANSION_MODES
        else None
    )
    preselection_graph_support_by_path: dict[str, dict[str, Any]] = {}
    if query_mode in GRAPH_EXPANSION_MODES:
        stale_paths = graph_stale_paths(freshness)
        preselection_graph_support_by_path = _direct_query_graph_support(
            retrieved_candidates,
            target=target,
            snapshot=snapshot,
            excluded_paths=stale_paths,
            projection_index=graph_projection_index,
        )
        graph_anchor_resolution, graph_projection_index = _resolve_graph_anchors(
            retrieved_candidates,
            target=target,
            mode=query_mode,
            snapshot=snapshot,
            excluded_paths=stale_paths,
            projection_index=graph_projection_index,
            graph_support_by_path=preselection_graph_support_by_path,
            component_ids_by_path=component_ids_by_path,
        )
        completeness["graph_anchor"] = graph_anchor_resolution.to_dict()
        if snapshot is None and graph_anchor_resolution.status == ContextAnchorStatus.RESOLVED:
            graph_anchor_resolution = ContextAnchorResolution(
                status=ContextAnchorStatus.UNRESOLVED,
                code=ContextAnchorResolutionCode.UNRESOLVED,
                candidates=graph_anchor_resolution.candidates,
                selection_coverage=_anchor_selection_coverage_after_filter(
                    graph_anchor_resolution,
                    (),
                    eligible_paths=set(),
                    component_ids_by_path=component_ids_by_path,
                ),
            )
            completeness["graph_anchor"] = graph_anchor_resolution.to_dict()
        elif snapshot is not None and graph_anchor_resolution.status == ContextAnchorStatus.RESOLVED:
            fresh_anchor_candidates = tuple(
                candidate
                for candidate in graph_anchor_resolution.anchors
                if candidate.anchor.path not in stale_paths
            )
            if not fresh_anchor_candidates:
                graph_anchor_resolution = ContextAnchorResolution(
                    status=ContextAnchorStatus.UNRESOLVED,
                    code=ContextAnchorResolutionCode.UNRESOLVED,
                    candidates=graph_anchor_resolution.candidates,
                    selection_coverage=_anchor_selection_coverage_after_filter(
                        graph_anchor_resolution,
                        (),
                        eligible_paths=set(),
                        component_ids_by_path=component_ids_by_path,
                    ),
                )
                completeness["graph_anchor"] = graph_anchor_resolution.to_dict()
            elif len(fresh_anchor_candidates) != len(graph_anchor_resolution.anchors):
                graph_anchor_resolution = ContextAnchorResolution(
                    status=ContextAnchorStatus.RESOLVED,
                    code=ContextAnchorResolutionCode.RESOLVED,
                    anchors=fresh_anchor_candidates,
                    candidates=graph_anchor_resolution.candidates,
                    selection_coverage=_anchor_selection_coverage_after_filter(
                        graph_anchor_resolution,
                        fresh_anchor_candidates,
                        component_ids_by_path=component_ids_by_path,
                    ),
                )
                completeness["graph_anchor"] = graph_anchor_resolution.to_dict()
            graph_anchors = [candidate.anchor for candidate in graph_anchor_resolution.anchors]
            seed_paths = list(dict.fromkeys(anchor.path for anchor in graph_anchors))
            if graph_anchor_resolution.status == ContextAnchorStatus.RESOLVED:
                graph_projection = project_context_neighborhood(
                    snapshot,
                    anchors=graph_anchors,
                    mode=query_mode,
                    excluded_candidate_paths=stale_paths,
                    projection_index=graph_projection_index,
                    component_data=component_data,
                )
            if graph_projection.get("ambiguous_anchors"):
                graph_anchor_resolution = ContextAnchorResolution(
                    status=ContextAnchorStatus.AMBIGUOUS,
                    code=ContextAnchorResolutionCode.AMBIGUOUS,
                    candidates=graph_anchor_resolution.candidates,
                    selection_coverage=graph_anchor_resolution.selection_coverage,
                )
                completeness["graph_anchor"] = graph_anchor_resolution.to_dict()
            elif graph_projection.get("unresolved_anchors"):
                resolved_anchor_keys = {
                    GraphContextAnchor(
                        kind=GraphContextAnchorKind(str(item.get("kind") or GraphContextAnchorKind.FILE.value)),
                        path=str(item.get("path") or ""),
                        symbol=str(item.get("symbol") or ""),
                        line_start=int(item.get("line_start") or 0),
                        line_end=int(item.get("line_end") or 0),
                        provider=str(item.get("provider") or ""),
                        provider_symbol_id=str(item.get("provider_symbol_id") or ""),
                    ).key()
                    for item in graph_projection.get("seed_anchors", [])
                    if isinstance(item, dict) and str(item.get("path") or "")
                }
                resolved_candidates = tuple(
                    candidate
                    for candidate in graph_anchor_resolution.anchors
                    if candidate.anchor.key() in resolved_anchor_keys
                )
                unresolved_paths = {
                    str(item.get("path") or "")
                    for item in graph_projection.get("unresolved_anchors", [])
                    if isinstance(item, dict) and str(item.get("path") or "")
                }
                eligible_paths = {
                    str(path)
                    for path in graph_anchor_resolution.selection_coverage.get(
                        "eligible_paths", []
                    )
                    if str(path) and str(path) not in unresolved_paths
                }
                graph_anchor_resolution = ContextAnchorResolution(
                    status=(
                        ContextAnchorStatus.RESOLVED
                        if resolved_candidates
                        else ContextAnchorStatus.UNRESOLVED
                    ),
                    code=(
                        ContextAnchorResolutionCode.RESOLVED
                        if resolved_candidates
                        else ContextAnchorResolutionCode.UNRESOLVED
                    ),
                    anchors=resolved_candidates,
                    candidates=graph_anchor_resolution.candidates,
                    selection_coverage=_anchor_selection_coverage_after_filter(
                        graph_anchor_resolution,
                        resolved_candidates,
                        eligible_paths=eligible_paths,
                        component_ids_by_path=component_ids_by_path,
                    ),
                )
                completeness["graph_anchor"] = graph_anchor_resolution.to_dict()
            if index_available and graph_anchor_resolution.status == ContextAnchorStatus.RESOLVED:
                projected_paths = {
                    f"{target.display_path.rstrip('/')}/{path}"
                    for path in [
                        *seed_paths,
                        *[str(path) for path in graph_projection.get("related_paths", [])],
                    ]
                    if path and path not in stale_paths
                }
                chunks, chunk_problems = evidence_chunks_for_paths(
                    root,
                    target=target,
                    workspace_paths=projected_paths,
                    kinds=ACTIONABLE_PRODUCT_KINDS,
                    database_path=evidence_index_path,
                )
                problems.extend(chunk_problems)
        if snapshot is not None:
            candidate_anchors = (
                graph_anchor_resolution.anchors
                if graph_anchor_resolution.status == ContextAnchorStatus.RESOLVED
                else graph_anchor_resolution.candidates
            )
            product_prefix = f"{target.display_path.rstrip('/')}/"
            retrieved_relationship_sources = [
                candidate
                for candidate in retrieved_candidates
                if candidate.source_ref.section_kind == ContextSectionKind.PROVIDER_RELATIONSHIP
                and candidate.source_ref.source_fact_id
                and candidate.source_ref.path.startswith(product_prefix)
                and _has_direct_query_evidence(candidate)
            ]
            exact_relationship_sources = [
                candidate
                for candidate in retrieved_relationship_sources
                if ContextEvidenceKind.EXACT_RELATIONSHIP in set(candidate.evidence_kinds)
            ]
            section_relationship_sources = [
                candidate
                for candidate in retrieved_relationship_sources
                if ContextEvidenceKind.SECTION_TERMS in set(candidate.evidence_kinds)
            ]
            selected_relationship_sources = exact_relationship_sources or section_relationship_sources
            if selected_relationship_sources:
                relationship_paths = list(
                    dict.fromkeys(
                        candidate.source_ref.path.removeprefix(product_prefix)
                        for candidate in selected_relationship_sources
                    )
                )
                relationship_source_fact_ids: dict[str, set[str]] = {}
                for candidate in selected_relationship_sources:
                    path = candidate.source_ref.path.removeprefix(product_prefix)
                    relationship_source_fact_ids.setdefault(path, set())
                    relationship_source_fact_ids[path].add(candidate.source_ref.source_fact_id)
            else:
                relationship_paths = list(dict.fromkeys(candidate.anchor.path for candidate in candidate_anchors))
                relationship_source_fact_ids = None
            graph_projection["relationship_candidates"] = relationship_candidates_for_paths(
                snapshot,
                relationship_paths,
                source_fact_ids=relationship_source_fact_ids,
                excluded_paths=stale_paths,
            )
        graph_projection = _fresh_graph_projection(
            graph_projection,
            stale_paths=stale_paths,
            task_history_stale=_task_history_stale(freshness),
        )
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
    # Cold history is deliberately loaded only after current retrieval,
    # ranking anchors, Graph traversal, and component projection are fixed.
    # It is appended to the isolated related_history lane below and cannot
    # participate in those current-state decisions.
    (
        explicit_history_candidates,
        _explicit_history_owned_paths,
        explicit_history_completeness,
        explicit_history_warnings,
    ) = _explicit_task_history_candidates(
        root,
        target=target,
        query=query,
        enabled=explicit_history,
    )
    completeness["explicit_task_history"] = explicit_history_completeness
    completeness["history_loaded"] = explicit_history_completeness["status"] in {
        "available",
        "partial",
    }
    problems.extend(explicit_history_warnings)
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
    evidence = _dedupe_candidates(
        [
            *startup_candidates,
            *graph_candidates,
            *retrieved_candidates,
            *explicit_history_candidates,
        ]
    )
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
    for group in ("likely_change_surface", "tests_and_verification"):
        for item in groups[group]:
            if not isinstance(item, dict):
                continue
            ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
            repo_path = _coverage_source_ref_repo_path(
                str(ref.get("path") or ""),
                repository_path=target.display_path,
            )
            component_ids = component_ids_by_path.get(repo_path, ())
            if component_ids:
                item["component_ids"] = list(component_ids)
    graph_seed_refs, graph_seed_identity = _context_graph_seed_refs(
        target=target,
        resolution=graph_anchor_resolution,
        graph_completeness=snapshot.completeness if snapshot is not None else {},
    )
    current_projection = _build_context_evidence_projection(
        groups,
        repository_path=target.display_path,
        mode=query_mode,
        anchor_resolution=graph_anchor_resolution,
        graph_seed_refs=graph_seed_refs,
        graph_support_by_path=preselection_graph_support_by_path,
        component_ids_by_path=component_ids_by_path,
    )
    prior_task_outcome, prior_outcome_completeness, prior_outcome_warnings = (
        _prior_task_outcome_join(
            root,
            target=target,
            evidence=evidence,
            projection=current_projection,
        )
        if include_linked_records
        else (
            [],
            {
                "status": "disabled",
                "code": "context_prior_task_outcome_disabled",
                "subjects_checked": 0,
                "subjects_matched": 0,
                "records_selected": 0,
            },
            [],
        )
    )
    groups["prior_task_outcome"] = prior_task_outcome
    completeness["prior_task_outcome"] = prior_outcome_completeness
    groups["warnings_and_completeness"].extend(prior_outcome_warnings)
    positive_prior_paths = tuple(
        dict.fromkeys(
            str((item.get("source_ref") or {}).get("path") or "")
            for item in prior_task_outcome
            if item.get("status") == "corroborated"
            and isinstance(item.get("source_ref"), dict)
            and str(item["source_ref"].get("path") or "")
        )
    )
    evidence_projection = (
        _build_context_evidence_projection(
            groups,
            repository_path=target.display_path,
            mode=query_mode,
            anchor_resolution=graph_anchor_resolution,
            graph_seed_refs=graph_seed_refs,
            graph_support_by_path=preselection_graph_support_by_path,
            prior_outcome_paths=positive_prior_paths,
            component_ids_by_path=component_ids_by_path,
        )
        if positive_prior_paths
        else current_projection
    )
    groups = _apply_context_evidence_projection(
        groups,
        repository_path=target.display_path,
        projection=evidence_projection,
    )
    visible_component_paths = {
        *evidence_projection.visible_source_paths,
        *evidence_projection.visible_test_paths,
    }
    component_crossings = _context_component_crossings(
        graph_projection,
        selected_paths=visible_component_paths,
    )
    final_projection = _compact_projection(
        groups,
        mode=query_mode,
        max_group_items=COMPACT_ITEM_LIMIT,
        repository_path=target.display_path,
        graph_seed_refs=graph_seed_refs,
        preselection_graph_support_by_path=preselection_graph_support_by_path,
        evidence_projection=evidence_projection,
        component_ids_by_path=component_ids_by_path,
    )
    selection["evidence_projection"] = evidence_projection.to_dict()
    if graph_seed_identity:
        graph_anchor_completeness = completeness.get("graph_anchor")
        if isinstance(graph_anchor_completeness, dict):
            graph_anchor_completeness["identity_coverage"] = graph_seed_identity
        if graph_seed_identity.get("status") == "partial":
            problems.append(
                Problem(
                    "warning",
                    "context_graph_seed_identity_unavailable",
                    "one or more fresh Graph anchors could not retain an exact source identity in the compact result",
                    target.display_path,
                )
            )
    selection["compact_projection"] = final_projection.stats
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
        groups={
            **groups,
            "callers_and_dependents": final_projection.groups.get(
                "callers_and_dependents",
                [],
            ),
        },
        relationship_candidates=[
            item
            for item in graph_projection.get("relationship_candidates", [])
            if isinstance(item, dict)
        ] if isinstance(graph_projection.get("relationship_candidates"), list) else [],
        component_crossings=component_crossings,
        graph_seed_refs=graph_seed_refs,
        preselection_graph_support_by_path=preselection_graph_support_by_path,
        evidence_projection=evidence_projection,
    ).with_digest()
    meta = {"repository": target.to_dict(), "graph": graph_meta}
    return bundle, problems, meta


def _context_graph_seed_refs(
    *,
    target: RepoTarget,
    resolution: ContextAnchorResolution | None,
    graph_completeness: dict[str, Any],
) -> tuple[list[ContextGraphSeedRef], dict[str, Any]]:
    if resolution is None or resolution.status != ContextAnchorStatus.RESOLVED:
        return [], {}
    expected = list(resolution.anchors[:GRAPH_ANCHOR_LIMIT])
    refs: list[ContextGraphSeedRef] = []
    missing_selectors: list[dict[str, str]] = []
    product_prefix = f"{target.display_path.rstrip('/')}/"
    for candidate in expected:
        anchor = candidate.anchor
        continuation = graph_anchor_continuation(anchor, completeness=graph_completeness)
        source_ref = candidate.source_ref.to_dict()
        expected_workspace_path = f"{product_prefix}{anchor.path}"
        if (
            source_ref.get("path") != expected_workspace_path
            or not source_ref.get("content_sha256")
            or continuation is None
        ):
            if continuation is not None:
                missing_selectors.append(continuation.selector_dict())
            continue
        refs.append(
            ContextGraphSeedRef(
                anchor=anchor,
                source_ref=candidate.source_ref,
                provenance=candidate.anchor_provenance,
                anchor_strength=candidate.anchor_strength,
                continuation=continuation,
            )
        )
    complete = len(refs) == len(expected)
    coverage: dict[str, Any] = {
        "status": "complete" if complete else "partial",
        "code": "context_graph_seed_identity_complete" if complete else "context_graph_seed_identity_unavailable",
        "expected_count": len(expected),
        "retained_count": len(refs),
    }
    if missing_selectors:
        coverage["recovery_selectors"] = missing_selectors
    return refs, coverage


def normalize_context_mode(explicit_mode: str = "") -> str:
    normalized = explicit_mode.strip().lower().replace("-", "_")
    aliases = {
        "authority": "authority_or_contract",
        "contract": "authority_or_contract",
    }
    normalized = aliases.get(normalized, normalized)
    if not normalized:
        return ContextResultMode.AUTO.value
    try:
        return ContextResultMode(normalized).value
    except ValueError:
        raise RepoctlError(f"unsupported context mode: {explicit_mode}", code="invalid_context_mode", path=explicit_mode)


def _explicit_task_history_candidates(
    root: Path,
    *,
    target: RepoTarget,
    query: str,
    enabled: bool,
) -> tuple[
    list[ContextCandidate],
    dict[str, list[str]],
    dict[str, Any],
    list[Problem],
]:
    """Run the only Context cold-history scan, behind an explicit mode."""

    if not enabled:
        return (
            [],
            {},
            {
                "status": "disabled",
                "code": "context_explicit_task_history_disabled",
                "scanned_event_count": 0,
                "matched_event_count": 0,
                "selected_record_count": 0,
                "truncated": False,
            },
            [],
        )

    query_terms = context_query_terms(query)
    task_ids = sorted(set(_TASK_ID_QUERY_RE.findall(query)))
    try:
        search = search_completion_history(
            root,
            target.id,
            query_terms=query_terms,
            task_ids=task_ids,
            limit=EXPLICIT_TASK_HISTORY_LIMIT,
        )
    except CompletionCatalogueUnavailable as exc:
        completeness = {
            "status": "unavailable",
            "code": "context_explicit_task_history_unavailable",
            "reason": exc.reason.value,
            "scanned_event_count": 0,
            "matched_event_count": 0,
            "selected_record_count": 0,
            "truncated": False,
        }
        return (
            [],
            {},
            completeness,
            [
                Problem(
                    "warning",
                    "context_explicit_task_history_unavailable",
                    "explicit completion history is unavailable; current source and Graph evidence remain available",
                    exc.path or target.display_path,
                    exc.reason.value,
                )
            ],
        )
    except (OSError, RepoctlError, ValueError) as exc:
        reason = str(getattr(exc, "code", "context_explicit_task_history_invalid"))
        return (
            [],
            {},
            {
                "status": "unavailable",
                "code": "context_explicit_task_history_unavailable",
                "reason": reason,
                "scanned_event_count": 0,
                "matched_event_count": 0,
                "selected_record_count": 0,
                "truncated": False,
            },
            [
                Problem(
                    "warning",
                    "context_explicit_task_history_unavailable",
                    "explicit completion history is unavailable; current source and Graph evidence remain available",
                    str(getattr(exc, "path", "") or target.display_path),
                    reason,
                )
            ],
        )

    candidates: list[ContextCandidate] = []
    owned_paths_by_task: dict[str, list[str]] = {}
    for match in search.records:
        task_id = match.record.task_id
        receipt = match.record.receipt
        owned_paths_by_task[task_id] = sorted(
            {
                str(entry.get("path") or "")
                for entry in receipt.get("changed_entries", [])
                if isinstance(entry, dict)
                and str(entry.get("path") or "")
                and (target.root_path / str(entry.get("path") or "")).is_file()
            }
        )
        chunk = chunk_text_source(
            root,
            match.record.artifact_path,
            match.artifact_text,
            kind="task_artifact",
            section=task_id,
            section_kind=ContextSectionKind.TASK,
        )
        candidates.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=excerpt_for_query(match.artifact_text, query, limit=700),
                score=float(100 if match.exact_task else len(match.matched_terms)),
                score_breakdown={
                    "identity": 1.0 if match.exact_task else 0.0,
                    "body": float(len(match.matched_terms)),
                },
                selection_reasons=[
                    "exact completion task match"
                    if match.exact_task
                    else "explicit completion history term match",
                    "explicit completion history matched changed paths: "
                    + ", ".join(owned_paths_by_task.get(task_id, ())),
                ],
                evidence_kinds=(
                    (ContextEvidenceKind.EXACT_TASK,)
                    if match.exact_task
                    else (ContextEvidenceKind.BODY_TERMS,)
                ),
                anchor_strength=(
                    ContextAnchorStrength.EXACT
                    if match.exact_task
                    else ContextAnchorStrength.WEAK
                ),
                query_term_matches={"body": match.matched_terms},
            )
        )
    status = (
        "partial"
        if search.truncated or search.search_terms_truncated_event_count
        else "available"
    )
    completeness = {
        "status": status,
        "code": (
            "context_explicit_task_history_partial"
            if status == "partial"
            else "context_explicit_task_history_available"
        ),
        "scanned_event_count": search.scanned_event_count,
        "matched_event_count": search.matched_event_count,
        "selected_record_count": len(search.records),
        "result_limit": EXPLICIT_TASK_HISTORY_LIMIT,
        "truncated": search.truncated,
        "search_terms_truncated_event_count": search.search_terms_truncated_event_count,
        "checkpoint_sequence": search.checkpoint_sequence,
        "prefix_digest": search.prefix_digest,
    }
    return candidates, owned_paths_by_task, completeness, []


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
    merged: dict[tuple[str, str, str, str, int, int, str], Any] = {}
    overlays_by_key = {
        chunk.source_ref.key(): chunk
        for chunk in overlay_chunks
    }
    for chunk in indexed_chunks:
        key = chunk.source_ref.key()
        if chunk.source_ref.path in replaced_paths:
            overlay = overlays_by_key.get(key)
            if (
                overlay is None
                or overlay.source_ref.content_sha256
                != chunk.source_ref.content_sha256
            ):
                continue
        merged[key] = chunk
    for chunk in overlay_chunks:
        merged.setdefault(chunk.source_ref.key(), chunk)
    return [merged[key] for key in sorted(merged)]


def _fresh_graph_projection(
    projection: dict[str, Any],
    *,
    stale_paths: set[str],
    task_history_stale: bool,
) -> dict[str, Any]:
    relations = projection.get("relations") if isinstance(projection.get("relations"), list) else []
    fresh_seed_paths = [path for path in projection.get("seed_paths", []) if str(path) not in stale_paths]
    relation_candidates: list[dict[str, Any]] = []
    for relation in relations:
        if not isinstance(relation, dict):
            continue
        from_path = str(relation.get("from_path") or "")
        to_path = str(relation.get("to_path") or "")
        if not from_path or not to_path or from_path in stale_paths or to_path in stale_paths:
            continue
        relation_candidates.append(relation)

    reachable_paths = {str(path) for path in fresh_seed_paths if str(path)}
    while True:
        previous_count = len(reachable_paths)
        for relation in relation_candidates:
            from_path = str(relation.get("from_path") or "")
            to_path = str(relation.get("to_path") or "")
            if from_path in reachable_paths or to_path in reachable_paths:
                reachable_paths.update((from_path, to_path))
        if len(reachable_paths) == previous_count:
            break

    fresh_relations = [
        relation
        for relation in relation_candidates
        if str(relation.get("from_path") or "") in reachable_paths
        and str(relation.get("to_path") or "") in reachable_paths
    ]
    fresh_related_paths = [
        path
        for path in projection.get("related_paths", [])
        if str(path) in reachable_paths and str(path) not in fresh_seed_paths
    ]
    fresh_relationship_candidates = (
        projection.get("relationship_candidates")
        if isinstance(projection.get("relationship_candidates"), list)
        else []
    )
    history = projection.get("history") if isinstance(projection.get("history"), list) else []
    fresh_history = (
        []
        if task_history_stale
        else [
            item
            for item in history
            if isinstance(item, dict) and str(item.get("path") or "") in reachable_paths
        ]
    )
    return {
        **projection,
        "seed_paths": fresh_seed_paths,
        "related_paths": fresh_related_paths,
        "relations": fresh_relations,
        "relationship_candidates": fresh_relationship_candidates,
        "history": fresh_history,
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


def _query_term_match_set(matches: dict[str, Any]) -> frozenset[str]:
    return frozenset(
        str(term)
        for terms in matches.values()
        if isinstance(terms, (list, tuple, set, frozenset))
        for term in terms
        if str(term)
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
    allowed_kinds = {*ACTIONABLE_PRODUCT_KINDS, "product_manifest", "verification_hint"}
    return [
        chunk
        for chunk in chunks
        if chunk.source_ref.kind in allowed_kinds
        and (
            chunk.source_ref.path.startswith(product_prefix)
        )
    ]


def _field_term_evidence_text(value: Any) -> str:
    if not isinstance(value, dict):
        return ""
    parts = [
        f"{field_name}=[{', '.join(str(term) for term in terms)}]"
        for field_name, terms in sorted(value.items())
        if isinstance(terms, list) and terms
    ]
    return "; ".join(parts)


def _markdown_coverage_lines(label: str, coverage: dict[str, Any]) -> list[str]:
    lines = [
        f"- {label}: `{coverage.get('status', '')}` "
        f"({coverage.get('selected_count', 0)}/{coverage.get('eligible_count', 0)} eligible; "
        f"{coverage.get('coverage_omitted_count', 0)} distinct omission(s))"
    ]
    field_term_evidence = _field_term_evidence_text(
        coverage.get("unrepresented_field_term_evidence")
    )
    if field_term_evidence:
        lines.append(f"- Unrepresented field-term evidence: {field_term_evidence}")
    for field_name, title in (
        ("unrepresented_lanes", "Unrepresented lanes"),
        ("unrepresented_roles", "Unrepresented roles"),
        ("unrepresented_components", "Unrepresented components"),
    ):
        values = coverage.get(field_name)
        if isinstance(values, list) and values:
            lines.append(f"- {title}: " + ", ".join(f"`{value}`" for value in values))
    omitted_paths = coverage.get("coverage_omitted_paths")
    if isinstance(omitted_paths, list) and omitted_paths:
        lines.append(
            "- Distinct omitted paths: "
            + ", ".join(f"`{path}`" for path in omitted_paths)
        )
    return lines


def render_context_markdown(bundle: ContextBundle) -> str:
    data = bundle.to_dict()
    query = data["query"]
    projection = _bundle_final_projection(
        bundle,
        max_group_items=COMPACT_ITEM_LIMIT,
    )
    displayed = projection.groups
    compact_completeness = _compact_completeness(
        bundle.completeness,
        working_set_coverage=projection.stats.get("working_set_coverage"),
    )
    graph_anchor = compact_completeness.get("graph_anchor") if isinstance(compact_completeness.get("graph_anchor"), dict) else {}
    working_set_coverage = compact_completeness.get("working_set_coverage") if isinstance(compact_completeness.get("working_set_coverage"), dict) else {}
    lines = [
        "# Context Bundle",
        "",
        f"- Query: {query.get('text', '')}",
        f"- Mode: `{query.get('mode', '')}`",
        f"- Repository: `{bundle.repository.get('id', '')}`",
        f"- Bundle digest: `{bundle.bundle_digest}`",
        "",
    ]
    if graph_anchor:
        coverage = graph_anchor.get("selection_coverage") if isinstance(graph_anchor.get("selection_coverage"), dict) else {}
        lines.extend(
            [
                "## Graph Working Set",
                "",
                f"- Anchor resolution: `{graph_anchor.get('status', '')}` (`{graph_anchor.get('code', '')}`)",
            ]
        )
        if coverage:
            lines.extend(_markdown_coverage_lines("Selection coverage", coverage))
        for seed in bundle.graph_seed_refs:
            lines.append(
                f"- Seed `{seed.anchor.path}`: `{seed.provenance.value}`, "
                f"strength `{seed.anchor_strength.value}`, identity `{seed.identity_digest}`"
            )
        lines.append("")
    if working_set_coverage:
        lines.extend(["## Compact Working Set", ""])
        lines.extend(_markdown_coverage_lines("Working-set coverage", working_set_coverage))
        lines.append("")
    titles = {
        "must_read": "Must Read",
        "likely_change_surface": "Likely Change Surface",
        "callers_and_dependents": "Callers And Dependents",
        "tests_and_verification": "Tests And Verification",
        "reviewed_knowledge": "Reviewed Knowledge",
        "prior_task_outcome": "Prior Task Outcome",
        "related_history": "Related History",
        "supporting_evidence": "Supporting Evidence",
        "warnings_and_completeness": "Warnings And Completeness",
    }
    lines.extend(["## Relationship Candidates", ""])
    if not bundle.relationship_candidates:
        lines.extend(["- No non-authoritative relationship candidates selected.", ""])
    else:
        for candidate in bundle.relationship_candidates[:5]:
            source = candidate.get("source") if isinstance(candidate.get("source"), dict) else {}
            runtime_identity = source.get("runtime_identity") if isinstance(source.get("runtime_identity"), dict) else {}
            resolution = candidate.get("resolution") if isinstance(candidate.get("resolution"), dict) else {}
            target_labels = [
                f"{str(target.get('path') or '')}:{int((target.get('location') or {}).get('line') or 0)}"
                for target in candidate.get("targets", [])
                if isinstance(target, dict) and isinstance(target.get("location"), dict)
            ]
            lines.append(
                f"- `{source.get('path', '')}` `{runtime_identity.get('value', '')}` -> "
                f"{', '.join(f'`{label}`' for label in target_labels)} "
                f"(candidate: `{resolution.get('reason_code', '')}`)"
            )
        lines.append("")
    for group in CONTEXT_GROUPS:
        items = displayed.get(group, [])
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


def render_context_text(bundle: ContextBundle) -> str:
    data = compact_context_bundle(bundle)
    lines = [
        f"context repository={bundle.repository.get('id', '')} mode={bundle.query.get('mode', '')}",
    ]
    graph_anchor = (
        data.get("completeness", {}).get("graph_anchor", {})
        if isinstance(data.get("completeness"), dict)
        else {}
    )
    if isinstance(graph_anchor, dict) and graph_anchor:
        lines.append(
            f"graph_anchor status={graph_anchor.get('status', '')} code={graph_anchor.get('code', '')}"
        )
        for seed in data.get("graph_seed_refs", []):
            if not isinstance(seed, dict):
                continue
            lines.append(
                "  seed "
                f"{seed.get('path', '')} provenance={seed.get('provenance', '')} "
                f"strength={seed.get('anchor_strength', '')} identity={seed.get('identity_digest', '')}"
            )
        coverage = graph_anchor.get("selection_coverage") if isinstance(graph_anchor.get("selection_coverage"), dict) else {}
        if coverage:
            lines.append(
                "graph_anchor_selection_coverage "
                f"status={coverage.get('status', '')} "
                f"selected={coverage.get('selected_count', 0)} "
                f"eligible={coverage.get('eligible_count', 0)} "
                f"coverage_omitted={coverage.get('coverage_omitted_count', 0)}"
            )
            field_term_evidence = _field_term_evidence_text(
                coverage.get("unrepresented_field_term_evidence")
            )
            if field_term_evidence:
                lines.append(f"  unrepresented_field_term_evidence {field_term_evidence}")
    working_set_coverage = (
        data.get("completeness", {}).get("working_set_coverage", {})
        if isinstance(data.get("completeness"), dict)
        else {}
    )
    if isinstance(working_set_coverage, dict) and working_set_coverage:
        lines.append(
            "working_set_coverage "
            f"status={working_set_coverage.get('status', '')} "
            f"selected={working_set_coverage.get('selected_count', 0)} "
            f"eligible={working_set_coverage.get('eligible_count', 0)} "
            f"coverage_omitted={working_set_coverage.get('coverage_omitted_count', 0)}"
        )
        field_term_evidence = _field_term_evidence_text(
            working_set_coverage.get("unrepresented_field_term_evidence")
        )
        if field_term_evidence:
            lines.append(f"  unrepresented_field_term_evidence {field_term_evidence}")
    for group in (
        "likely_change_surface",
        "tests_and_verification",
        "callers_and_dependents",
        "must_read",
        "reviewed_knowledge",
        "prior_task_outcome",
        "related_history",
        "warnings_and_completeness",
    ):
        items = data.get("groups", {}).get(group, []) if isinstance(data.get("groups"), dict) else []
        if not items:
            continue
        lines.append(f"{group}:")
        for item in items:
            if not isinstance(item, dict):
                continue
            ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
            label = str(ref.get("path") or item.get("record_id") or item.get("code") or "evidence")
            reason = str(item.get("selection_reason") or item.get("status") or "")
            if ref.get("kind") == "graph_relation" and item.get("excerpt"):
                label = str(item["excerpt"])
            lines.append(f"  {label}" + (f" - {reason}" if reason else ""))
    continuations = data.get("continuations") if isinstance(data.get("continuations"), list) else []
    if continuations:
        lines.append("continuations:")
        for continuation in continuations[:5]:
            if not isinstance(continuation, dict):
                continue
            selector = continuation.get("selector") if isinstance(continuation.get("selector"), dict) else {}
            selector_text = f"{selector.get('kind', '')}={selector.get('value', '')}"
            if selector.get("in_file"):
                selector_text += f" in_file={selector['in_file']}"
            actions = continuation.get("actions") if isinstance(continuation.get("actions"), list) else []
            action_text = f" actions={','.join(str(action) for action in actions)}" if actions else ""
            lines.append(f"  {selector_text}{action_text}")
    return "\n".join(lines).rstrip() + "\n"


def compact_context_bundle(bundle: ContextBundle, *, max_group_items: int = 8, excerpt_chars: int = 120) -> dict[str, Any]:
    """Return the default agent-facing view without full evidence diagnostics."""
    projection = _bundle_final_projection(
        bundle,
        max_group_items=max_group_items,
    )
    groups = {
        group: [_compact_group_item(item, excerpt_chars=excerpt_chars) for item in items]
        for group, items in projection.groups.items()
    }
    relationship_candidate_projection = compact_relationship_candidates(bundle.relationship_candidates)
    return {
        "schema": bundle.schema,
        "schema_version": bundle.schema_version,
        "view": "compact",
        "authoritative": bundle.authoritative,
        "repository": bundle.repository,
        "query": bundle.query,
        "completeness": _compact_completeness(
            bundle.completeness,
            working_set_coverage=projection.stats.get("working_set_coverage"),
        ),
        "groups": groups,
        "relationship_candidates": relationship_candidate_projection["items"],
        "relationship_candidate_count": relationship_candidate_projection["total_count"],
        "relationship_candidates_truncated": relationship_candidate_projection["truncated"],
        "component_crossings": bundle.component_crossings,
        "component_crossing_count": len(bundle.component_crossings),
        "component_crossings_truncated": False,
        "graph_seed_refs": [seed.to_dict() for seed in bundle.graph_seed_refs],
        "continuations": projection.continuations,
        "bundle_digest": bundle.bundle_digest,
    }


def _bundle_final_projection(
    bundle: ContextBundle,
    *,
    max_group_items: int,
) -> _FinalContextProjection:
    projection = getattr(bundle, "_final_projection", None)
    if (
        isinstance(projection, _FinalContextProjection)
        and max_group_items == COMPACT_ITEM_LIMIT
    ):
        return projection
    return _compact_projection(
        bundle.groups,
        mode=str(bundle.query.get("mode") or "auto"),
        max_group_items=max_group_items,
        repository_path=str(bundle.repository.get("path") or ""),
        graph_seed_refs=bundle.graph_seed_refs,
        preselection_graph_support_by_path=bundle.preselection_graph_support_by_path,
        evidence_projection=bundle.evidence_projection,
        component_ids_by_path=None,
    )


def _selected_change_surface_paths(
    groups: dict[str, list[dict[str, Any]]],
    *,
    repository_path: str,
) -> tuple[str, ...]:
    paths: list[str] = []
    for item in groups.get("likely_change_surface", []):
        if not isinstance(item, dict):
            continue
        ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        path = _coverage_source_ref_repo_path(
            str(ref.get("path") or ""),
            repository_path=repository_path,
        )
        if path and path not in paths:
            paths.append(path)
    return tuple(paths)


def _ordered_actionable_items(
    items: list[dict[str, Any]],
    *,
    repository_path: str,
    ordered_paths: tuple[str, ...],
) -> list[dict[str, Any]]:
    by_path = {
        _coverage_source_ref_repo_path(
            str((item.get("source_ref") or {}).get("path") or ""),
            repository_path=repository_path,
        ): item
        for item in items
        if isinstance(item, dict) and isinstance(item.get("source_ref"), dict)
    }
    ordered = [by_path[path] for path in ordered_paths if path in by_path]
    ordered_ids = {id(item) for item in ordered}
    return [*ordered, *(item for item in items if id(item) not in ordered_ids)]


def _context_group_repo_paths(
    groups: dict[str, list[dict[str, Any]]],
    group: str,
    *,
    repository_path: str,
) -> tuple[str, ...]:
    return tuple(
        dict.fromkeys(
            path
            for item in groups.get(group, [])
            if isinstance(item, dict)
            and isinstance(item.get("source_ref"), dict)
            if (
                path := _coverage_source_ref_repo_path(
                    str(item["source_ref"].get("path") or ""),
                    repository_path=repository_path,
                )
            )
        )
    )


def _direct_evidence_dependency_path(
    source_path: str,
    *,
    candidate_paths: tuple[str, ...],
    graph_support_by_path: dict[str, dict[str, Any]],
) -> str:
    support = graph_support_by_path.get(source_path)
    connections = support.get("candidate_connections") if isinstance(support, dict) else []
    direct_dependencies = {
        _coverage_graph_repo_path(str(connection.get("to_path") or ""))
        for connection in connections
        if isinstance(connection, dict)
        and connection.get("assertion") == "resolved"
        and str(connection.get("edge") or "")
        in {"CALLS", "IMPORTS_FILE", STRUCTURED_EDGE_KIND}
        and _coverage_graph_repo_path(str(connection.get("from_path") or ""))
        == source_path
    }
    return next((path for path in candidate_paths if path in direct_dependencies), "")


def _build_context_evidence_projection(
    groups: dict[str, list[dict[str, Any]]],
    *,
    repository_path: str,
    mode: str,
    anchor_resolution: ContextAnchorResolution | None,
    graph_seed_refs: list[ContextGraphSeedRef] | None = None,
    graph_support_by_path: dict[str, dict[str, Any]],
    prior_outcome_paths: tuple[str, ...] = (),
    component_ids_by_path: dict[str, tuple[str, ...]] | None = None,
) -> ContextEvidenceProjection:
    source_items = groups.get("likely_change_surface", [])
    source_paths = _context_group_repo_paths(
        groups,
        "likely_change_surface",
        repository_path=repository_path,
    )
    source_profiles = _compact_group_coverage_profiles(
        source_items,
        group="likely_change_surface",
        repository_path=repository_path,
        graph_seed_refs=graph_seed_refs,
        preselection_graph_support_by_path=graph_support_by_path,
        component_ids_by_path=component_ids_by_path,
    )
    prior_outcome_set = set(prior_outcome_paths)
    exact_or_typed_paths = {
        _coverage_profile_path_identity(profile)
        for profile in source_profiles
        if _coverage_profile_has_explicit_identity(profile)
        or _coverage_profile_graph_supported(profile)
    }
    effective_prior_paths = (
        set()
        if exact_or_typed_paths
        else prior_outcome_set
    )
    evidence_order = tuple(
        _coverage_profile_path_identity(profile)
        for profile in sorted(
            _eligible_coverage_profiles(source_profiles),
            key=lambda profile: _coverage_profile_prior_outcome_rank_key(
                profile,
                prior_outcome_paths=effective_prior_paths,
            ),
        )
        if _coverage_profile_path_identity(profile)
    )
    resolution_candidates = (
        [*(anchor_resolution.anchors if anchor_resolution is not None else ())]
        + [*(anchor_resolution.candidates if anchor_resolution is not None else ())]
    )
    resolution_order = tuple(
        dict.fromkeys(
            candidate.anchor.path
            for candidate in resolution_candidates
            if candidate.retrieval_lane
            in {ContextRetrievalLane.PRODUCT_SOURCE.value, "product_config"}
            and candidate.anchor.path in source_paths
        )
    )
    # Rank all current-source evidence in one corpus. Graph resolution supplies
    # identity and relationships, but is not a second semantic ranker.
    source_order = tuple(
        dict.fromkeys([*evidence_order, *source_paths, *resolution_order])
    )
    candidates_by_path = {
        candidate.anchor.path: candidate for candidate in resolution_candidates
    }
    first_anchor = (
        candidates_by_path[source_order[0]]
        if source_order and source_order[0] in candidates_by_path
        else None
    )
    dependency = ""
    if (
        first_anchor is not None
        and first_anchor.anchor_strength
        in {ContextAnchorStrength.EXACT, ContextAnchorStrength.EXPLICIT}
        and source_order[0] == first_anchor.anchor.path
    ):
        dependency = _direct_evidence_dependency_path(
            first_anchor.anchor.path,
            candidate_paths=source_order[1:],
            graph_support_by_path=graph_support_by_path,
        )
        if dependency:
            source_order = (
                source_order[0],
                dependency,
                *(path for path in source_order[1:] if path != dependency),
            )
    source_limit = (
        3
        if mode == ContextResultMode.AUTO.value
        else 2
        if mode in GRAPH_EXPANSION_MODES
        else len(source_order)
    )

    test_profiles = _compact_group_coverage_profiles(
        groups.get("tests_and_verification", []),
        group="tests_and_verification",
        repository_path=repository_path,
        graph_seed_refs=graph_seed_refs,
        preselection_graph_support_by_path=graph_support_by_path,
        component_ids_by_path=component_ids_by_path,
    )
    test_paths = tuple(
        dict.fromkeys(
            _coverage_profile_path_identity(profile)
            for profile in test_profiles
            if _coverage_profile_path_identity(profile)
        )
    )
    if mode not in GRAPH_EXPANSION_MODES:
        return ContextEvidenceProjection(
            ranked_source_paths=source_order,
            visible_source_paths=source_order,
            ranked_test_paths=test_paths,
            visible_test_paths=test_paths,
            prior_outcome_paths=tuple(path for path in source_order if path in effective_prior_paths),
        )

    selected_sources = _select_compact_evidence_profiles(
        _eligible_coverage_profiles(source_profiles),
        limit=source_limit,
        primary_path=source_order[0] if source_order else "",
        required_paths=(*([dependency] if dependency else []),),
        prior_outcome_paths=effective_prior_paths,
    )
    visible_source_paths = tuple(
        _coverage_profile_path_identity(profile)
        for profile in selected_sources
        if _coverage_profile_path_identity(profile)
    )
    source_order = tuple(dict.fromkeys([*visible_source_paths, *source_order]))

    source_profiles = _compact_group_coverage_profiles(
        _ordered_actionable_items(
            source_items,
            repository_path=repository_path,
            ordered_paths=visible_source_paths,
        ),
        group="likely_change_surface",
        repository_path=repository_path,
        graph_seed_refs=graph_seed_refs,
        preselection_graph_support_by_path=graph_support_by_path,
        component_ids_by_path=component_ids_by_path,
    )
    source_profiles_by_path = {
        _coverage_profile_path_identity(profile): profile
        for profile in source_profiles
    }
    references = [
        source_profiles_by_path[path]
        for path in visible_source_paths
        if path in source_profiles_by_path
    ]
    visible_tests = _select_coverage_profiles(
        _eligible_coverage_profiles(test_profiles),
        limit=1,
        reference_profiles=references,
    )
    visible_test_paths = tuple(
        _coverage_profile_path_identity(profile)
        for profile in visible_tests
        if _coverage_profile_path_identity(profile)
    )
    test_order = tuple(dict.fromkeys([*visible_test_paths, *test_paths]))

    return ContextEvidenceProjection(
        ranked_source_paths=source_order,
        visible_source_paths=visible_source_paths,
        ranked_test_paths=test_order,
        visible_test_paths=visible_test_paths,
        prior_outcome_paths=tuple(path for path in source_order if path in effective_prior_paths),
    )


def _select_compact_evidence_profiles(
    profiles: list[dict[str, Any]],
    *,
    limit: int,
    primary_path: str = "",
    required_paths: tuple[str, ...] = (),
    prior_outcome_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Choose one bounded working set from typed evidence and query coverage."""
    if not profiles or limit <= 0:
        return []
    profiles_by_path = {
        _coverage_profile_path_identity(profile): profile
        for profile in profiles
        if _coverage_profile_path_identity(profile)
    }
    prior_outcome_paths = prior_outcome_paths or set()
    if primary_path:
        primary = profiles_by_path.get(primary_path, profiles[0])
    else:
        primary = min(
            profiles,
            key=lambda profile: _coverage_profile_prior_outcome_rank_key(
                profile,
                prior_outcome_paths=prior_outcome_paths,
            ),
        )
    selected = [primary]
    identity_bounded = _coverage_profile_has_query_identity(primary)

    def eligible_optional(profile: dict[str, Any]) -> bool:
        if not identity_bounded:
            return True
        return bool(
            _coverage_profile_has_query_identity(profile)
            or _coverage_profile_connected_to_selected(
                profile,
                selected=[primary],
            )
        )

    required = [
        profiles_by_path[path]
        for path in required_paths
        if path in profiles_by_path and profiles_by_path[path] is not primary
    ]
    remaining = [profile for profile in profiles if profile not in selected]
    optional_limit = max(0, limit - len(selected) - len(required))
    frequencies = _coverage_pair_frequencies(profiles) if remaining and optional_limit else {}
    while remaining and len(selected) < 1 + optional_limit:
        direct_remaining = [
            profile
            for profile in remaining
            if profile.get("direct_query", True)
            and _coverage_profile_contributes(profile, selected=selected)
        ]
        independent_direct = [
            profile
            for profile in direct_remaining
            if not _coverage_profile_evidence_connected_to_selected(
                profile,
                selected=selected,
            )
        ]
        candidates = independent_direct or direct_remaining or [
            profile
            for profile in remaining
            if _coverage_profile_contributes(profile, selected=selected)
        ]
        candidates = [profile for profile in candidates if eligible_optional(profile)]
        if not candidates:
            direct_cohort = [
                profile
                for profile in remaining
                if profile.get("direct_query", True)
                and _coverage_profile_is_direct_source(profile)
                and eligible_optional(profile)
            ]
            if (
                direct_cohort
                and len(profiles) <= limit
                and not any(
                    _coverage_profile_neighbor_paths(profile)
                    for profile in profiles
                )
            ):
                candidates = direct_cohort
        if not candidates:
            break
        next_profile = min(
            candidates,
            key=lambda profile: _coverage_profile_related_key(
                profile,
                selected=selected,
                frequencies=frequencies,
            ),
        )
        selected.append(next_profile)
        remaining.remove(next_profile)
    for profile in required:
        if len(selected) >= limit:
            break
        if profile not in selected:
            selected.append(profile)
    return selected


def _coverage_profile_body_terms(profile: dict[str, Any]) -> set[str]:
    return {
        term
        for field_name, term in _coverage_profile_pairs(profile)
        if field_name == "body"
    }


def _coverage_profile_anchor_rank_key(
    profile: dict[str, Any],
) -> tuple[Any, ...]:
    """Rank direct anchors without letting path echo outweigh body evidence."""
    body_terms = _coverage_profile_body_terms(profile)
    return (
        _coverage_profile_identity_tier(profile),
        0 if _coverage_profile_history_corroborated(profile) else 1,
        -_coverage_term_breadth_tier(body_terms),
        -len(body_terms),
        0 if _coverage_profile_graph_supported(profile) else 1,
        _coverage_profile_direct_rank_key(profile),
    )


def _select_anchor_evidence_profiles(
    profiles: list[dict[str, Any]],
    *,
    limit: int,
) -> list[dict[str, Any]]:
    """Select Graph seeds once from identity, typed relations, and query evidence."""
    if not profiles or limit <= 0:
        return []
    selectable = [
        profile for profile in profiles if profile.get("selection_available", True)
    ]
    if not selectable:
        return []
    if _coverage_profiles_use_weak_single_term_fallback(profiles):
        limit = min(limit, 1)
    frequencies = _coverage_pair_frequencies(profiles)
    primary = min(
        selectable,
        key=_coverage_profile_anchor_rank_key,
    )
    primary_pairs = _coverage_profile_pairs(primary)
    indistinguishable_weak_echoes = bool(
        _coverage_profile_anchor_priority(primary)
        == CONTEXT_ANCHOR_STRENGTH_PRIORITY[ContextAnchorStrength.WEAK]
        and primary_pairs
        and len(selectable) > 1
        and all(
            _coverage_profile_anchor_priority(profile)
            == CONTEXT_ANCHOR_STRENGTH_PRIORITY[ContextAnchorStrength.WEAK]
            and _coverage_profile_pairs(profile) == primary_pairs
            and not _coverage_profile_has_explicit_identity(profile)
            and not _coverage_profile_component_ids(profile)
            and not _coverage_profile_evidence_neighbor_paths(profile)
            for profile in selectable
        )
    )
    if indistinguishable_weak_echoes:
        return [primary]
    connected_ranked = sorted(
        (
            profile
            for profile in selectable
            if _coverage_profile_graph_supported(profile)
        ),
        key=_coverage_profile_anchor_rank_key,
    )
    if connected_ranked:
        connected_primary = connected_ranked[0]
        primary_lane = str(primary.get("lane_key") or "")
        connected_lane = str(connected_primary.get("lane_key") or "")
        if (
            primary_lane == ContextRetrievalLane.PRODUCT_TEST.value
            or (
                primary_lane
                in {ContextRetrievalLane.PRODUCT_SOURCE.value, "product_config"}
                and connected_lane
                in {ContextRetrievalLane.PRODUCT_SOURCE.value, "product_config"}
                and _coverage_profile_identity_tier(connected_primary)
                <= _coverage_profile_identity_tier(primary)
                and _coverage_profile_anchor_priority(primary)
                <= _coverage_profile_anchor_priority(connected_primary)
                and _coverage_term_breadth_tier(
                    _coverage_profile_body_terms(connected_primary)
                )
                >= _coverage_term_breadth_tier(
                    _coverage_profile_body_terms(primary)
                )
            )
        ):
            primary = connected_primary
    selected = [primary]
    remaining = [profile for profile in selectable if profile is not primary]
    while remaining and len(selected) < limit:
        primary_pairs = _coverage_profile_pairs(selected[0])
        primary_components = _coverage_profile_component_ids(selected[0])
        covered_terms = {
            term
            for item in selected
            for term in _coverage_profile_terms(item)
        }
        selected_components = _coverage_selected_component_ids(selected)
        co_primary = [
            profile
            for profile in remaining
            if primary_pairs
            and _coverage_profile_pairs(profile) == primary_pairs
            and _coverage_profile_component_ids(profile) == primary_components
        ]
        contributors = [
            profile
            for profile in remaining
            if _coverage_profile_contributes(profile, selected=selected)
            and (
                bool(_coverage_profile_terms(profile) - covered_terms)
                or bool(
                    _coverage_profile_component_ids(profile) - selected_components
                )
            )
        ]
        contributors.extend(
            profile for profile in co_primary if profile not in contributors
        )
        if not contributors:
            primary_body_terms = _coverage_profile_body_terms(selected[0])
            body_cohort = [
                profile
                for profile in remaining
                if _coverage_profile_structural_pairs(selected[0])
                and primary_body_terms
                and profile.get("direct_query", True)
                and not _coverage_profile_graph_supported(selected[0])
                and not _coverage_profile_graph_supported(profile)
                and primary_body_terms <= _coverage_profile_body_terms(profile)
            ]
            if body_cohort:
                next_profile = min(
                    body_cohort,
                    key=_coverage_profile_anchor_rank_key,
                )
                selected.append(next_profile)
                remaining.remove(next_profile)
                continue
            break
        connected_primary = [
            profile
            for profile in contributors
            if _coverage_profile_evidence_connected_to_selected(
                profile,
                selected=selected,
            )
        ]
        if connected_primary and not co_primary:
            candidate_pool = connected_primary
            next_profile = min(
                candidate_pool,
                key=lambda profile: _coverage_profile_related_key(
                    profile,
                    selected=selected,
                    frequencies=frequencies,
                ),
            )
            selected.append(next_profile)
            remaining.remove(next_profile)
            continue
        independent_direct = [
            profile
            for profile in contributors
            if profile.get("direct_query", True)
            and bool(_coverage_profile_terms(profile) - covered_terms)
            and not _coverage_profile_connected_to_selected(
                profile,
                selected=selected,
            )
        ]
        typed_connected = [
            profile
            for profile in contributors
            if _coverage_profile_evidence_connected_to_selected(
                profile,
                selected=selected,
            )
            or (
                str(profile.get("lane_key") or "")
                == ContextRetrievalLane.PRODUCT_TEST.value
                and bool(_coverage_profile_test_target_paths(profile))
            )
        ]
        new_components = [
            profile
            for profile in contributors
            if _coverage_profile_component_ids(profile) - selected_components
        ]
        candidate_pool = (
            independent_direct
            or typed_connected
            or new_components
            or contributors
        )
        next_profile = min(
            candidate_pool,
            key=lambda profile: _coverage_profile_related_key(
                profile,
                selected=selected,
                frequencies=frequencies,
            ),
        )
        selected.append(next_profile)
        remaining.remove(next_profile)
    selected_ids = {id(profile) for profile in selected}
    # Lexical novelty can be saturated before structural continuation is. Keep
    # one fresh typed neighbor when the query already selected more than one
    # independent source hypothesis; this gives the agent one bounded path to
    # follow without letting repeated disconnected weak matches fan Graph out.
    if len(selected) > 1 and len(selected) < limit:
        structural_candidates = [
            profile
            for profile in selectable
            if id(profile) not in selected_ids
            and _coverage_profile_evidence_connected_to_selected(
                profile,
                selected=selected,
            )
        ]
        if structural_candidates:
            next_profile = min(
                structural_candidates,
                key=lambda profile: _coverage_profile_related_key(
                    profile,
                    selected=selected,
                    frequencies=frequencies,
                ),
            )
            selected.append(next_profile)
            selected_ids.add(id(next_profile))
    while len(selected) < limit:
        coverage_candidates = [
            profile
            for profile in selectable
            if id(profile) not in selected_ids
            and _coverage_profile_contributes(profile, selected=selected)
        ]
        if not coverage_candidates:
            break
        next_profile = min(
            coverage_candidates,
            key=lambda profile: _coverage_profile_related_key(
                profile,
                selected=selected,
                frequencies=frequencies,
            ),
        )
        selected.append(next_profile)
        selected_ids.add(id(next_profile))
    return selected


def _apply_context_evidence_projection(
    groups: dict[str, list[dict[str, Any]]],
    *,
    repository_path: str,
    projection: ContextEvidenceProjection,
) -> dict[str, list[dict[str, Any]]]:
    return {
        **groups,
        "likely_change_surface": _ordered_actionable_items(
            groups.get("likely_change_surface", []),
            repository_path=repository_path,
            ordered_paths=projection.ranked_source_paths,
        ),
        "tests_and_verification": _ordered_actionable_items(
            groups.get("tests_and_verification", []),
            repository_path=repository_path,
            ordered_paths=projection.ranked_test_paths,
        ),
    }


def _prior_task_outcome_candidate_paths(
    evidence: list[ContextCandidate],
    *,
    repository_path: str,
    projection: ContextEvidenceProjection,
) -> tuple[str, ...]:
    """Bound the join to current candidates fixed without historical input."""
    eligible = {
        _coverage_source_ref_repo_path(
            candidate.source_ref.path,
            repository_path=repository_path,
        )
        for candidate in evidence
        if candidate.source_ref.kind in ACTIONABLE_PRODUCT_KINDS
        and _has_direct_query_evidence(candidate)
    }
    ranked = (*projection.ranked_source_paths, *projection.ranked_test_paths)
    return tuple(
        path
        for path in dict.fromkeys(ranked)
        if path in eligible
    )[:PRIOR_TASK_OUTCOME_SUBJECT_LIMIT]


def _prior_task_outcome_join(
    root: Path,
    *,
    target: RepoTarget,
    evidence: list[ContextCandidate],
    projection: ContextEvidenceProjection,
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]]]:
    """Join bounded current subjects to hot history without changing their rank."""
    paths = _prior_task_outcome_candidate_paths(
        evidence,
        repository_path=target.display_path,
        projection=projection,
    )
    completeness: dict[str, Any] = {
        "status": "available",
        "code": "context_prior_task_outcome_available",
        "subjects_checked": len(paths),
        "subjects_matched": 0,
        "records_selected": 0,
        "subject_limit": PRIOR_TASK_OUTCOME_SUBJECT_LIMIT,
        "record_limit": PRIOR_TASK_OUTCOME_RECORD_LIMIT,
    }
    if not paths:
        return [], completeness, []

    items: list[dict[str, Any]] = []
    seen_records: set[tuple[str, str]] = set()
    omitted_subjects = 0
    checkpoint_sequence = 0
    prefix_digest = ""
    try:
        current_subjects = [
            current_path_subject(
                root,
                target=target,
                path=path,
            )
            for path in paths
        ]
        lookups = current_completion_frontiers(
            root,
            target.id,
            (
                versioned_completion_subject_key(
                    str(subject["key"]),
                    str(subject["version_digest"]),
                )
                for subject in current_subjects
            ),
        )
        for path, current_subject, lookup in zip(
            paths,
            current_subjects,
            lookups,
            strict=True,
        ):
            if lookup.may_have_cold_history:
                omitted_subjects += 1
                if lookup.omitted_record_count:
                    completeness["records_omitted_by_hot_policy"] = int(
                        completeness.get("records_omitted_by_hot_policy") or 0
                    ) + lookup.omitted_record_count
            checkpoint_sequence = lookup.checkpoint_sequence
            prefix_digest = lookup.prefix_digest
            matched_subject = False
            for record in lookup.records[:PRIOR_TASK_OUTCOME_RECORD_LIMIT]:
                outcome = record.get("outcome") if isinstance(record.get("outcome"), dict) else {}
                roles = outcome.get("subject_roles") if isinstance(outcome.get("subject_roles"), dict) else {}
                role = roles.get(str(current_subject["key"]))
                if not isinstance(role, dict):
                    continue
                if (
                    role.get("kind") != current_subject["kind"]
                    or role.get("key") != current_subject["key"]
                    or role.get("identity") != current_subject["identity"]
                    or role.get("version_digest") != current_subject["version_digest"]
                ):
                    continue
                task_id = str(record.get("task_id") or "")
                if not task_id or (task_id, path) in seen_records:
                    continue
                seen_records.add((task_id, path))
                matched_subject = True
                statuses = sorted(
                    {
                        str(value)
                        for value in role.get("verification_statuses", [])
                        if str(value) in {"passed", "failed", "mixed", "blocked"}
                    }
                )
                positive = bool(
                    role.get("reviewed")
                    and role.get("chosen")
                    and not role.get("excluded")
                    and not role.get("outside_candidate_set")
                    and statuses == ["passed"]
                )
                items.append(
                    {
                        "repo_id": target.id,
                        "record_id": task_id,
                        "status": "corroborated" if positive else "recorded",
                        "subject": current_subject,
                        "roles": {
                            "reviewed": bool(role.get("reviewed")),
                            "chosen": bool(role.get("chosen")),
                            "excluded": bool(role.get("excluded")),
                            "outside_candidate_set": bool(role.get("outside_candidate_set")),
                        },
                        "verification_statuses": statuses,
                        "selection_reason": (
                            "fresh prior task outcome corroborates this current candidate"
                            if positive
                            else "fresh prior task outcome records this current candidate"
                        ),
                        "source_ref": {
                            "kind": "prior_task_outcome",
                            "path": path,
                            "section": task_id,
                            "content_sha256": str(outcome.get("outcome_digest") or ""),
                        },
                        "provenance": {
                            "task_id": task_id,
                            "completed_at": str(record.get("completed_at") or ""),
                            "outcome_digest": str(outcome.get("outcome_digest") or ""),
                            "receipt_sha256": str(record.get("receipt_sha256") or ""),
                            "subject_version_digest": str(current_subject["version_digest"]),
                        },
                        "continuations": [_file_continuation(path, include_impact=False)],
                    }
                )
            if matched_subject:
                completeness["subjects_matched"] += 1
    except CompletionCatalogueUnavailable as exc:
        completeness.update(
            {
                "status": "unavailable",
                "code": "context_prior_task_outcome_unavailable",
                "reason": exc.reason.value,
                "subjects_matched": 0,
                "records_selected": 0,
            }
        )
        warning = {
            "repo_id": target.id,
            "status": "warning",
            "code": "context_prior_task_outcome_unavailable",
            "reason_code": exc.reason.value,
            "selection_reason": (
                "bounded prior task outcomes are unavailable; current source and Graph evidence are unchanged"
            ),
        }
        return [], completeness, [warning]
    except (OSError, RepoctlError, ValueError) as exc:
        completeness.update(
            {
                "status": "unavailable",
                "code": "context_prior_task_outcome_unavailable",
                "reason": getattr(exc, "code", "context_prior_task_outcome_subject_invalid"),
                "subjects_matched": 0,
                "records_selected": 0,
            }
        )
        warning = {
            "repo_id": target.id,
            "status": "warning",
            "code": "context_prior_task_outcome_unavailable",
            "reason_code": completeness["reason"],
            "selection_reason": (
                "bounded prior task outcomes are unavailable; current source and Graph evidence are unchanged"
            ),
        }
        return [], completeness, [warning]

    items.sort(
        key=lambda item: (
            0 if item.get("status") == "corroborated" else 1,
            -int(str(item.get("record_id") or "")[2:16]),
            str(item.get("record_id") or ""),
            str(item.get("source_ref", {}).get("path") or ""),
        )
    )
    items = items[:PRIOR_TASK_OUTCOME_RECORD_LIMIT]
    completeness["records_selected"] = len(items)
    completeness["subjects_omitted_by_hot_policy"] = omitted_subjects
    if omitted_subjects:
        completeness["status"] = "partial"
        completeness["code"] = "context_prior_task_outcome_partial"
    if checkpoint_sequence:
        completeness["checkpoint_sequence"] = checkpoint_sequence
    if prefix_digest:
        completeness["prefix_digest"] = prefix_digest
    return items, completeness, []


def _resolved_relation_endpoint_paths(
    relation: dict[str, Any],
) -> tuple[str, ...]:
    if relation.get("assertion") != "resolved":
        return ()
    return tuple(
        dict.fromkeys(
            path
            for key in ("from_path", "to_path")
            if (path := _coverage_graph_repo_path(str(relation.get(key) or "")))
        )
    )


def _relation_visible_source_priority(
    relation: dict[str, Any],
    *,
    source_priority: dict[str, int],
) -> int | None:
    endpoints = _resolved_relation_endpoint_paths(relation)
    endpoint_priorities = [source_priority[path] for path in endpoints if path in source_priority]
    return min(endpoint_priorities) if endpoint_priorities else None


def _compact_source_incident_relations(
    items: list[dict[str, Any]],
    *,
    visible_source_paths: tuple[str, ...],
) -> list[dict[str, Any]]:
    source_priority = {
        path: index for index, path in enumerate(visible_source_paths)
    }
    ranked: list[tuple[int, int, dict[str, Any]]] = []
    for index, item in enumerate(items):
        relations = item.get("graph_path") if isinstance(item.get("graph_path"), list) else []
        incident = [
            (
                priority,
                relation_index,
                relation,
            )
            for relation_index, relation in enumerate(relations)
            if isinstance(relation, dict)
            and (
                priority := _relation_visible_source_priority(
                    relation,
                    source_priority=source_priority,
                )
            )
            is not None
        ]
        if incident:
            priority = min(entry[0] for entry in incident)
            selected_relations = [
                relation
                for relation_priority, _relation_index, relation in incident
                if relation_priority == priority
            ]
            projected_item = dict(item)
            projected_item["graph_path"] = selected_relations
            provenance = _graph_relation_provenance(selected_relations)
            if provenance:
                projected_item["provenance"] = provenance
            else:
                projected_item.pop("provenance", None)
            ranked.append((priority, index, projected_item))
    return [
        item
        for _priority, _index, item in sorted(
            ranked,
            key=lambda entry: (entry[0], entry[1]),
        )
    ]


def _compact_projection(
    groups: dict[str, list[dict[str, Any]]],
    *,
    mode: str,
    max_group_items: int,
    repository_path: str,
    graph_seed_refs: list[ContextGraphSeedRef] | None = None,
    preselection_graph_support_by_path: dict[str, dict[str, Any]] | None = None,
    evidence_projection: ContextEvidenceProjection | None = None,
    component_ids_by_path: dict[str, tuple[str, ...]] | None = None,
) -> _FinalContextProjection:
    group_limits = {
        "must_read": 2,
        "likely_change_surface": 2,
        "callers_and_dependents": 1,
        "tests_and_verification": 1,
        "reviewed_knowledge": 1,
        "prior_task_outcome": 1,
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
    projected_groups = _compact_graph_working_set_groups(
        groups,
        repository_path=repository_path,
        evidence_projection=evidence_projection,
    )
    if mode == "auto":
        projected_groups["callers_and_dependents"] = _compact_source_incident_relations(
            projected_groups.get("callers_and_dependents", []),
            visible_source_paths=_selected_change_surface_paths(
                projected_groups,
                repository_path=repository_path,
            ),
        )
    displayed, continuations, stats = _compact_bundle_projection(
        projected_groups,
        group_limits=group_limits,
        max_group_items=max_group_items,
        item_limit=COMPACT_ITEM_LIMIT,
        continuation_limit=COMPACT_CONTINUATION_LIMIT,
        mode=mode,
        repository_path=repository_path,
    )
    total_items = sum(len(items) for items in groups.values())
    displayed_items = sum(len(items) for items in displayed.values())
    total_continuations = len(
        _dedupe_continuations(_collect_bundle_continuations(groups))
    )
    stats["items"] = {
        "total": total_items,
        "displayed": displayed_items,
        "omitted": max(0, total_items - displayed_items),
    }
    stats["continuations"] = {
        "total": total_continuations,
        "displayed": len(continuations),
        "omitted": max(0, total_continuations - len(continuations)),
    }
    working_set_coverage = _compact_working_set_coverage(
        groups,
        displayed,
        repository_path=repository_path,
        graph_seed_refs=graph_seed_refs or [],
        preselection_graph_support_by_path=preselection_graph_support_by_path or {},
        component_ids_by_path=component_ids_by_path,
    )
    if working_set_coverage:
        stats["working_set_coverage"] = working_set_coverage
    return _FinalContextProjection(
        groups=displayed,
        continuations=continuations,
        stats=stats,
    )


def _compact_group_coverage_profiles(
    items: list[dict[str, Any]],
    *,
    group: str,
    repository_path: str,
    graph_seed_refs: list[ContextGraphSeedRef] | None = None,
    preselection_graph_support_by_path: dict[str, dict[str, Any]] | None = None,
    component_ids_by_path: dict[str, tuple[str, ...]] | None = None,
) -> list[dict[str, Any]]:
    profiles: list[dict[str, Any]] = []
    for rank, item in enumerate(items, start=1):
        if not isinstance(item, dict):
            continue
        ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
        evidence_kinds = {str(value) for value in item.get("evidence_kinds", []) if str(value)}
        matches = item.get("query_term_matches") if isinstance(item.get("query_term_matches"), dict) else {}
        try:
            strength = ContextAnchorStrength(str(item.get("anchor_strength") or ContextAnchorStrength.NONE.value))
        except ValueError:
            strength = ContextAnchorStrength.NONE
        graph_path = item.get("graph_path") if isinstance(item.get("graph_path"), list) else []
        source_kind = str(ref.get("kind") or "")
        if source_kind not in ACTIONABLE_PRODUCT_KINDS:
            continue
        lane_key = (
            ContextRetrievalLane.PRODUCT_TEST.value
            if group == "tests_and_verification"
            else "product_config"
            if source_kind == "config"
            else ContextRetrievalLane.PRODUCT_DATA.value
            if source_kind == ContextSourceKind.STRUCTURED_DATA.value
            else ContextRetrievalLane.PRODUCT_SOURCE.value
        )
        path = str(ref.get("path") or "")
        path_identity = _coverage_source_ref_repo_path(
            path,
            repository_path=repository_path,
        )
        preselection_graph_support = dict(
            (preselection_graph_support_by_path or {}).get(path_identity) or {}
        )
        relation_paths = {
            identity
            for relation in graph_path
            if isinstance(relation, dict)
            for key in ("from_path", "to_path")
            if (
                identity := _coverage_graph_repo_path(
                    str(relation.get(key) or "")
                )
            )
        }
        neighbor_paths = sorted(
            relation_path
            for relation_path in relation_paths
            if relation_path != path_identity
        )
        direct_call_neighbor_paths = sorted(
            relation_path
            for relation in graph_path
            if isinstance(relation, dict) and relation.get("edge") == "CALLS"
            for key in ("from_path", "to_path")
            if (
                relation_path := _coverage_graph_repo_path(
                    str(relation.get(key) or "")
                )
            )
            and relation_path != path_identity
        )
        exact_reference_symbol_ids = sorted(
            {
                symbol_id(
                    str(item.get("repo_id") or ""),
                    seed.source_ref.provider,
                    seed.source_ref.provider_symbol_id,
                )
                for seed in graph_seed_refs or []
                if path_identity == seed.anchor.path
                and seed.source_ref.provider
                and seed.source_ref.provider_symbol_id
                and str(item.get("repo_id") or "")
            }
        )
        direct_call_symbol_ids = sorted(
            {
                str(relation.get(key) or "")
                for relation in graph_path
                if isinstance(relation, dict) and relation.get("edge") == "CALLS"
                for key in ("from_id", "to_id")
                if str(relation.get(key) or "")
            }
        )
        breakdown = item.get("score_breakdown") if isinstance(item.get("score_breakdown"), dict) else {}
        evidence_roles = {
            str(role)
            for role in item.get("evidence_roles", [])
            if str(role)
        }
        connection_priority = (
            0
            if "anchor_connected_test" in evidence_roles
            else 1
            if "directly_connected_test" in evidence_roles
            else 2
            if ContextEvidenceKind.GRAPH_RELATION.value in evidence_kinds
            else 3
        )
        profiles.append(
            {
                "path": path,
                "path_identity": path_identity,
                "component_ids": tuple(
                    (component_ids_by_path or {}).get(path_identity, ())
                ),
                "lane_key": lane_key,
                "query_term_matches": matches,
                "graph_support": {
                    "direct_relation_count": len(graph_path),
                    "direct_test_count": sum(
                        1
                        for relation in graph_path
                        if isinstance(relation, dict) and relation.get("edge") == "TESTS_FILE"
                    ),
                    "candidate_neighbor_count": len(neighbor_paths),
                    "candidate_neighbor_paths": neighbor_paths,
                    "direct_call_neighbor_paths": direct_call_neighbor_paths,
                    "direct_call_symbol_ids": direct_call_symbol_ids,
                },
                "preselection_graph_support": preselection_graph_support,
                "exact_reference_symbol_ids": exact_reference_symbol_ids,
                "graph_supported": ContextEvidenceKind.GRAPH_RELATION.value in evidence_kinds,
                "history_corroborated": ContextEvidenceKind.HISTORY_CORROBORATION.value
                in evidence_kinds,
                "related_record_ids": {
                    str(record_id)
                    for record_id in item.get("related_record_ids", [])
                    if str(record_id)
                },
                "direct_query": bool(evidence_kinds - {kind.value for kind in GRAPH_DERIVED_CONTEXT_EVIDENCE_KINDS}),
                "structured_relationship_source": any(
                    isinstance(section, dict)
                    and section.get("section_kind")
                    == ContextSectionKind.PROVIDER_RELATIONSHIP.value
                    for section in item.get("sections", [])
                ),
                "provider_symbol": any(
                    isinstance(section, dict)
                    and section.get("section_kind")
                    == ContextSectionKind.PROVIDER_SYMBOL.value
                    for section in item.get("sections", [])
                ),
                "strong_symbol": ContextEvidenceKind.EXACT_SYMBOL.value in evidence_kinds,
                "anchor_strength": strength.value,
                "field_count": sum(1 for terms in matches.values() if isinstance(terms, (list, tuple)) and terms),
                "query_coverage": float(breakdown.get("exact") or 0.0),
                "path_name_coverage": float(breakdown.get("path_name") or 0.0),
                "path_area_coverage": float(breakdown.get("path_area") or 0.0),
                "path_scope_coverage": float(breakdown.get("path_scope") or 0.0),
                "role_priority": _evidence_role_priority(str(item.get("evidence_role") or "")),
                "coverage_roles": evidence_roles,
                "connection_priority": connection_priority,
                "compact_projection_candidate": True,
                "rank": rank,
                "score": float(item.get("score") or 0.0),
                "item": item,
            }
        )
    return profiles


def _compact_graph_working_set_groups(
    groups: dict[str, list[dict[str, Any]]],
    *,
    repository_path: str,
    evidence_projection: ContextEvidenceProjection | None = None,
) -> dict[str, list[dict[str, Any]]]:
    projected = {group: list(items) for group, items in groups.items()}
    if evidence_projection is None:
        return projected

    visible_by_group = {
        "likely_change_surface": evidence_projection.visible_source_paths,
        "tests_and_verification": evidence_projection.visible_test_paths,
    }
    for group, visible_paths in visible_by_group.items():
        items_by_path = {
            _coverage_source_ref_repo_path(
                str((item.get("source_ref") or {}).get("path") or ""),
                repository_path=repository_path,
            ): item
            for item in groups.get(group, [])
            if isinstance(item, dict)
            and isinstance(item.get("source_ref"), dict)
        }
        projected[group] = [
            items_by_path[path]
            for path in visible_paths
            if path in items_by_path
        ]
    return projected


def _compact_working_set_coverage(
    all_groups: dict[str, list[dict[str, Any]]],
    displayed_groups: dict[str, list[dict[str, Any]]],
    *,
    repository_path: str,
    graph_seed_refs: list[ContextGraphSeedRef] | None = None,
    preselection_graph_support_by_path: dict[str, dict[str, Any]] | None = None,
    component_ids_by_path: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    actionable_groups = ("likely_change_surface", "tests_and_verification")
    profiles = [
        profile
        for group in actionable_groups
        for profile in _compact_group_coverage_profiles(
            all_groups.get(group, []),
            group=group,
            repository_path=repository_path,
            graph_seed_refs=graph_seed_refs or [],
            preselection_graph_support_by_path=preselection_graph_support_by_path or {},
            component_ids_by_path=component_ids_by_path,
        )
    ]
    if not profiles:
        return {}
    selected_paths = {
        str((item.get("source_ref") or {}).get("path") or "")
        for group in actionable_groups
        for item in displayed_groups.get(group, [])
        if isinstance(item, dict) and isinstance(item.get("source_ref"), dict)
    }
    diagnostics = _coverage_omission_diagnostics(profiles, selected_paths=selected_paths)
    omitted = diagnostics["omitted"]
    coverage_omissions = diagnostics["coverage_omissions"]
    weak_single_term_fallback = _coverage_profiles_use_weak_single_term_fallback(profiles)
    return {
        "status": "partial" if coverage_omissions else "complete",
        "reason": (
            "weak_single_term_fallback"
            if coverage_omissions and weak_single_term_fallback
            else "compact_budget_exhausted"
            if coverage_omissions
            else ""
        ),
        "candidate_count": len(profiles),
        "eligible_count": len(profiles),
        "selected_count": len(profiles) - len(omitted),
        "omitted_count": len(omitted),
        "coverage_omitted_count": len(coverage_omissions),
        "eligible_paths": [str(profile.get("path") or "") for profile in profiles],
        "selected_paths": sorted(selected_paths),
        "omitted_paths": [str(profile.get("path") or "") for profile in omitted],
        "coverage_omitted_paths": [
            str(profile.get("path") or "") for profile in coverage_omissions
        ],
        "unrepresented_field_term_evidence": diagnostics["unrepresented_field_term_evidence"],
        "unrepresented_lanes": diagnostics["unrepresented_lanes"],
        "unrepresented_roles": diagnostics["unrepresented_roles"],
        "unrepresented_components": diagnostics["unrepresented_components"],
    }


def _compact_field_term_evidence(value: Any, *, limit: int = 8) -> dict[str, list[str]]:
    if not isinstance(value, dict):
        return {}
    remaining = limit
    compact: dict[str, list[str]] = {}
    for field_name, terms in sorted(value.items()):
        if remaining <= 0 or not isinstance(terms, list):
            continue
        selected = sorted({str(term) for term in terms if str(term)})[:remaining]
        if selected:
            compact[str(field_name)] = selected
            remaining -= len(selected)
    return compact


def _compact_coverage_diagnostics(value: Any) -> dict[str, Any]:
    if not isinstance(value, dict):
        return {}
    compact = {
        key: value.get(key)
        for key in (
            "status",
            "reason",
            "candidate_count",
            "eligible_count",
            "selected_count",
            "omitted_count",
            "coverage_omitted_count",
        )
    }
    compact["omitted_paths"] = [str(path) for path in value.get("omitted_paths", []) if str(path)][:5]
    compact["coverage_omitted_paths"] = [
        str(path) for path in value.get("coverage_omitted_paths", []) if str(path)
    ][:5]
    compact["unrepresented_field_term_evidence"] = _compact_field_term_evidence(
        value.get("unrepresented_field_term_evidence")
    )
    for key in ("unrepresented_lanes", "unrepresented_roles", "unrepresented_components"):
        compact[key] = [str(item) for item in value.get(key, []) if str(item)][:5]
    return compact


def _compact_completeness(
    completeness: dict[str, Any],
    *,
    working_set_coverage: Any = None,
) -> dict[str, Any]:
    graph = completeness.get("graph_completeness") if isinstance(completeness.get("graph_completeness"), dict) else {}
    compact = {
        "graph_available": bool(completeness.get("graph_available")),
        "graph_freshness": compact_graph_freshness(completeness.get("graph_freshness")),
        "status": str(graph.get("status") or ("unavailable" if not completeness.get("graph_available") else "partial")),
    }
    project_knowledge = completeness.get("project_knowledge")
    if isinstance(project_knowledge, dict):
        compact["project_knowledge"] = project_knowledge
    prior_task_outcome = completeness.get("prior_task_outcome")
    if isinstance(prior_task_outcome, dict):
        compact["prior_task_outcome"] = {
            key: prior_task_outcome[key]
            for key in (
                "status",
                "code",
                "reason",
                "subjects_checked",
                "subjects_matched",
                "records_selected",
                "subject_limit",
                "record_limit",
                "checkpoint_sequence",
                "prefix_digest",
                "subjects_omitted_by_hot_policy",
                "records_omitted_by_hot_policy",
            )
            if key in prior_task_outcome
        }
    anchor = completeness.get("graph_anchor") if isinstance(completeness.get("graph_anchor"), dict) else {}
    if anchor:
        anchors = anchor.get("anchors") if isinstance(anchor.get("anchors"), list) else []
        candidates = anchor.get("candidates") if isinstance(anchor.get("candidates"), list) else []
        seed_anchors: list[dict[str, Any]] = []
        for item in anchors:
            if not isinstance(item, dict) or not isinstance(item.get("anchor"), dict):
                continue
            raw_anchor = item["anchor"]
            path = str(raw_anchor.get("path") or "")
            if not path:
                continue
            seed: dict[str, Any] = {
                "path": path,
                "provenance": str(item.get("anchor_provenance") or ""),
                "anchor_strength": str(item.get("anchor_strength") or ContextAnchorStrength.NONE.value),
            }
            for key in ("kind", "symbol"):
                if raw_anchor.get(key):
                    seed[key] = raw_anchor[key]
            for key in ("retrieval_lane", "lexical_rank"):
                if item.get(key):
                    seed[key] = item[key]
            if isinstance(item.get("component_ids"), list) and item["component_ids"]:
                seed["component_ids"] = list(item["component_ids"])
            seed_anchors.append(seed)
        compact["graph_anchor"] = {
            "status": str(anchor.get("status") or "unresolved"),
            "code": str(anchor.get("code") or ContextAnchorResolutionCode.UNRESOLVED.value),
            "seed_anchors": seed_anchors,
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
        selection_coverage = anchor.get("selection_coverage") if isinstance(anchor.get("selection_coverage"), dict) else {}
        if selection_coverage:
            compact["graph_anchor"]["selection_coverage"] = _compact_coverage_diagnostics(
                selection_coverage
            )
        identity_coverage = anchor.get("identity_coverage") if isinstance(anchor.get("identity_coverage"), dict) else {}
        if identity_coverage:
            compact["graph_anchor"]["identity_coverage"] = {
                "status": str(identity_coverage.get("status") or "partial"),
                "code": str(identity_coverage.get("code") or "context_graph_seed_identity_unavailable"),
                "expected_count": int(identity_coverage.get("expected_count") or 0),
                "retained_count": int(identity_coverage.get("retained_count") or 0),
                "recovery_selectors": [
                    selector
                    for selector in identity_coverage.get("recovery_selectors", [])
                    if isinstance(selector, dict)
                ][:GRAPH_ANCHOR_LIMIT],
            }
    compact_working_set_coverage = _compact_coverage_diagnostics(working_set_coverage)
    if compact_working_set_coverage:
        compact["working_set_coverage"] = compact_working_set_coverage
    return compact


def _compact_group_item(item: dict[str, Any], *, excerpt_chars: int) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in (
        "record_id",
        "code",
        "document_role",
        "evidence_role",
        "subject",
        "roles",
        "verification_statuses",
        "component_ids",
    ):
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


def _compact_item_has_primary_continuation(item: dict[str, Any]) -> bool:
    """Accept only producer-shaped evidence with one executable primary selector."""
    values = item.get("continuations")
    if not isinstance(values, list) or not values or not isinstance(values[0], dict):
        return False
    return len(_dedupe_continuations([values[0]])) == 1


def _compact_bundle_projection(
    all_groups: dict[str, list[dict[str, Any]]],
    *,
    group_limits: dict[str, int],
    max_group_items: int,
    item_limit: int,
    continuation_limit: int,
    mode: str,
    repository_path: str,
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
        "context_prior_task_outcome_unavailable",
    }
    warning_priority = {
        "context_graph_stale": 0,
        "context_graph_unavailable": 1,
        "context_graph_freshness_unavailable": 2,
        "context_task_history_stale": 3,
        "context_graph_anchor_snapshot_unresolved": 4,
        ContextAnchorResolutionCode.AMBIGUOUS.value: 5,
        "context_prior_task_outcome_unavailable": 6,
    }
    displayed_groups["warnings_and_completeness"] = sorted(
        (
            item
            for item in all_groups.get("warnings_and_completeness", [])
            if isinstance(item, dict) and str(item.get("code") or "") in warning_codes
        ),
        key=lambda item: warning_priority.get(str(item.get("code") or ""), 99),
    )[:1]

    code_first = ["likely_change_surface", "tests_and_verification", "callers_and_dependents", "must_read", "reviewed_knowledge", "prior_task_outcome", "related_history", "supporting_evidence"]
    authority_first = ["must_read", "reviewed_knowledge", "prior_task_outcome", "related_history", "likely_change_surface", "tests_and_verification", "callers_and_dependents", "supporting_evidence"]
    lane_order = authority_first if mode in {"startup_reading", "authority_or_contract", "past_decision", "invariant", "failure_mode"} else code_first
    projection_groups = {
        group: [
            item
            for item in items
            if isinstance(item, dict) and _compact_item_has_primary_continuation(item)
        ]
        for group, items in all_groups.items()
    }
    if (
        mode in GRAPH_EXPANSION_MODES
        and _compact_has_actionable_product_evidence(projection_groups)
    ):
        projection_groups["must_read"] = [
            item
            for item in projection_groups.get("must_read", [])
            if not _compact_is_weak_root_operational_document(
                item,
                repository_path=repository_path,
            )
        ]
    projection_groups["must_read"] = _role_diverse_must_read_items(
        projection_groups.get("must_read", [])
    )
    selected_item_count = 0

    def try_add(group: str, item: dict[str, Any]) -> bool:
        nonlocal selected_continuations, selected_item_count
        if selected_item_count >= item_limit:
            return False
        displayed_groups[group].append(item)
        selected_item_count += 1
        raw_values = item.get("continuations")
        if not isinstance(raw_values, list) or not raw_values or not isinstance(raw_values[0], dict):
            return True
        primary_values = _dedupe_continuations([raw_values[0]])
        if len(primary_values) != 1:
            return True
        item_values = _dedupe_continuations([primary_values[0], *[value for value in raw_values[1:] if isinstance(value, dict)]])
        reserved = _dedupe_continuations([*selected_continuations, item_values[0]])
        if len(reserved) <= continuation_limit:
            selected_continuations = reserved
            secondary_continuations.extend(item_values[1:])
        return True

    positions = {group: 0 for group in lane_order}
    for group in ("likely_change_surface", "tests_and_verification"):
        if group not in positions:
            continue
        limit = min(max_group_items, group_limits.get(group, max_group_items))
        while (
            selected_item_count < item_limit
            and positions[group] < len(projection_groups.get(group, []))
            and len(displayed_groups[group]) < limit
        ):
            item = projection_groups[group][positions[group]]
            positions[group] += 1
            if isinstance(item, dict):
                try_add(group, item)

    for group in lane_order:
        if group in {"likely_change_surface", "tests_and_verification"}:
            continue
        limit = min(max_group_items, group_limits.get(group, max_group_items))
        initial_limit = min(
            2
            if group == "must_read"
            and _has_authority_and_procedure(projection_groups[group])
            else 1,
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
    all_values = _dedupe_continuations(_collect_bundle_continuations(all_groups))
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


def _compact_has_actionable_product_evidence(
    groups: dict[str, list[dict[str, Any]]],
) -> bool:
    return any(
        str(ref.get("kind") or "") in ACTIONABLE_PRODUCT_KINDS
        for group in ("likely_change_surface", "tests_and_verification")
        for item in groups.get(group, [])
        if isinstance(item, dict)
        and isinstance((ref := item.get("source_ref")), dict)
    )


def _compact_is_weak_root_operational_document(
    item: dict[str, Any],
    *,
    repository_path: str,
) -> bool:
    role = str(item.get("document_role") or "")
    if role not in {
        DocumentRole.OPERATING_AUTHORITY.value,
        DocumentRole.GOVERNANCE_AUTHORITY.value,
        DocumentRole.PROCEDURE.value,
    }:
        return False
    ref = item.get("source_ref") if isinstance(item.get("source_ref"), dict) else {}
    path = str(ref.get("path") or "").replace("\\", "/").strip("/")
    product_prefix = repository_path.replace("\\", "/").strip("/")
    if product_prefix and path.startswith(f"{product_prefix}/"):
        return False
    if str(item.get("anchor_strength") or "") != ContextAnchorStrength.WEAK.value:
        return False
    evidence_kinds = {
        str(value)
        for value in item.get("evidence_kinds", [])
        if str(value)
    }
    direct_kinds = evidence_kinds - {
        kind.value for kind in GRAPH_DERIVED_CONTEXT_EVIDENCE_KINDS
    }
    return bool(
        direct_kinds
        and direct_kinds <= {
            kind.value for kind in _LEXICAL_QUERY_EVIDENCE_KINDS
        }
    )


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
        ):
            known_paths.add(path.removeprefix(product_prefix))
    for chunk in overlay_chunks:
        path = chunk.source_ref.path
        if chunk.source_ref.kind in ACTIONABLE_PRODUCT_KINDS and path.startswith(product_prefix):
            known_paths.add(path.removeprefix(product_prefix))
    return known_paths


def _current_product_selector_paths(*, target: RepoTarget, value: str) -> set[str]:
    """Return current files represented by either accepted selector spelling."""
    candidates: set[str] = set()
    for repository_path in ("", target.display_path):
        resolution = resolve_repo_selector_path(
            value,
            repository_path=repository_path,
        )
        if resolution.status == RepoSelectorStatus.RESOLVED and resolution.path:
            candidates.add(resolution.path)

    target_root = target.root_path.resolve()
    current: set[str] = set()
    for candidate in candidates:
        path = target.root_path / candidate
        try:
            resolved = path.resolve()
        except OSError:
            continue
        if resolved.is_relative_to(target_root) and path.is_file():
            current.add(candidate)
    return current


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
                current_known_paths = {
                    *known_paths,
                    *_current_product_selector_paths(target=target, value=raw_path),
                }
                resolution = resolve_repo_selector_path(
                    raw_path,
                    repository_path=target.display_path,
                    known_paths=current_known_paths,
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

    related_paths = [str(path) for path in projection.get("related_paths", [])]
    projected_paths = list(dict.fromkeys([*seed_paths, *related_paths]))
    seed_path_set = set(seed_paths)
    graph_path_set = {*seed_paths, *related_paths}
    projected_retrieval_by_path: dict[str, ContextCandidate] = {}
    projected_chunks = [
        chunk
        for path in projected_paths
        for chunk in source_chunks.get(path, [])
    ]
    for candidate in rank_context_chunks(
        query,
        projected_chunks,
        limit=-1,
        repository_path=target.display_path,
    ):
        path = candidate.source_ref.path
        if not path.startswith(product_prefix):
            continue
        repo_path = path.removeprefix(product_prefix)
        projected_retrieval_by_path.setdefault(repo_path, candidate)

    for path in projected_paths:
        path_chunks = source_chunks.get(str(path), [])
        retrieved = retrieval_by_path.get(str(path)) or projected_retrieval_by_path.get(str(path))
        chunk = _graph_source_chunk(path_chunks, retrieved=retrieved)
        if chunk is None:
            continue
        if str(path) not in graph_path_set:
            continue
        path_relations = relations_by_path.get(str(path), [])
        scoring_relations = _section_coherent_graph_relations(
            path_relations,
            candidate=retrieved,
            target=target,
            mode=str(projection.get("mode") or ""),
        )
        propagated_score = 0.0
        for relation in scoring_relations:
            origins = relation.get("origin_paths") if isinstance(relation.get("origin_paths"), list) else []
            for origin in origins:
                origin_path = str(origin or "")
                if origin_path == path:
                    continue
                distance = _relation_origin_distance(relation, origin_path)
                propagated_score = max(propagated_score, anchor_scores.get(origin_path, 0.0) / distance)
        is_graph_seed = path in seed_path_set
        reasons = [_relation_reason(relation) for relation in scoring_relations[:3]]
        lexical_score = retrieved.score if retrieved is not None else 0.0
        if retrieved is not None:
            reasons.extend(retrieved.selection_reasons)
        if is_graph_seed:
            reasons.append("bounded Context Graph seed")
        evidence_kinds = set(retrieved.evidence_kinds if retrieved is not None else ())
        if is_graph_seed:
            evidence_kinds.add(ContextEvidenceKind.GRAPH_SEED)
        if scoring_relations:
            evidence_kinds.add(ContextEvidenceKind.GRAPH_RELATION)
        candidates.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=excerpt_for_query(chunk.text, query, limit=700),
                score=propagated_score + lexical_score,
                score_breakdown={
                    **(retrieved.score_breakdown if retrieved is not None else {}),
                    "graph": propagated_score,
                },
                selection_reasons=reasons or ["Graph direct file relation"],
                graph_path=scoring_relations[:3],
                evidence_kinds=tuple(sorted(evidence_kinds, key=lambda kind: kind.value)),
                anchor_strength=(
                    retrieved.anchor_strength
                    if retrieved is not None
                    else ContextAnchorStrength.NONE
                ),
                query_term_matches=retrieved.query_term_matches if retrieved is not None else {},
                related_record_ids=tuple(
                    sorted(
                        {
                            *(retrieved.related_record_ids if retrieved is not None else ()),
                        }
                    )
                ),
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


_EXACT_FILE_ANCHOR_KINDS = {
    ContextEvidenceKind.EXACT_PATH,
    ContextEvidenceKind.EXACT_FILENAME,
    ContextEvidenceKind.EXACT_RELATIONSHIP,
}


def _direct_query_graph_support(
    source_candidates: list[ContextCandidate],
    *,
    target: RepoTarget,
    snapshot: Any = None,
    excluded_paths: set[str] | None = None,
    projection_index: Any = None,
) -> dict[str, dict[str, Any]]:
    if snapshot is None:
        return {}
    product_prefix = f"{target.display_path.rstrip('/')}/"
    paths = {
        candidate.source_ref.path.removeprefix(product_prefix)
        for candidate in source_candidates
        if candidate.source_ref.kind in LEXICAL_GRAPH_ANCHOR_KINDS
        and candidate.source_ref.path.startswith(product_prefix)
        and _has_direct_query_evidence(candidate)
    }
    if not paths:
        return {}
    return context_path_support_profiles(
        snapshot,
        paths=paths,
        excluded_paths=excluded_paths,
        projection_index=projection_index,
    )


def _resolve_graph_anchors(
    source_candidates: list[ContextCandidate],
    *,
    target: RepoTarget,
    mode: str,
    snapshot: Any = None,
    excluded_paths: set[str] | None = None,
    projection_index: Any = None,
    graph_support_by_path: dict[str, dict[str, Any]] | None = None,
    component_ids_by_path: dict[str, tuple[str, ...]] | None = None,
) -> tuple[ContextAnchorResolution, Any]:
    product_prefix = f"{target.display_path.rstrip('/')}/"
    snapshot_file_paths = {
        str(node.identity.get("path") or "")
        for node in (snapshot.nodes if snapshot is not None else [])
        if node.kind == "file" and str(node.identity.get("path") or "")
    }
    explicitly_unavailable_paths = set(excluded_paths or set())

    def selection_available(path: str) -> bool:
        return bool(
            snapshot is not None
            and path in snapshot_file_paths
            and path not in explicitly_unavailable_paths
        )

    exact_pairs: list[tuple[ContextCandidate, ContextGraphAnchorCandidate]] = []
    knowledge_pairs: list[tuple[ContextCandidate, ContextGraphAnchorCandidate]] = []
    named_file_pairs: list[tuple[ContextCandidate, ContextGraphAnchorCandidate]] = []
    diagnostic_pairs: list[tuple[ContextCandidate, ContextGraphAnchorCandidate]] = []
    heuristic_candidates_by_path: dict[str, list[ContextCandidate]] = {}
    for candidate in source_candidates:
        path = candidate.source_ref.path
        if candidate.source_ref.kind not in ACTIONABLE_PRODUCT_KINDS or not path.startswith(product_prefix):
            continue
        evidence_kinds = set(candidate.evidence_kinds)
        if (
            evidence_kinds & _EXACT_GRAPH_ANCHOR_KINDS
            and candidate.anchor_strength in {ContextAnchorStrength.EXACT, ContextAnchorStrength.EXPLICIT}
        ):
            graph_candidate = _context_graph_anchor_candidate(
                [candidate],
                target=target,
                provenance=ContextGraphAnchorProvenance.EXACT_IDENTITY,
            )
            exact_pairs.append((candidate, graph_candidate))
            diagnostic_pairs.append((candidate, graph_candidate))
            continue
        if (
            ContextEvidenceKind.REVIEWED_KNOWLEDGE_PATH in evidence_kinds
            and candidate.anchor_strength == ContextAnchorStrength.EXPLICIT
        ):
            graph_candidate = _context_graph_anchor_candidate(
                [candidate],
                target=target,
                provenance=ContextGraphAnchorProvenance.REVIEWED_KNOWLEDGE,
            )
            knowledge_pairs.append((candidate, graph_candidate))
            diagnostic_pairs.append((candidate, graph_candidate))
            continue

        if candidate.source_ref.kind not in LEXICAL_GRAPH_ANCHOR_KINDS:
            continue

        provenance = (
            ContextGraphAnchorProvenance.PROVIDER_SYMBOL
            if mode != ContextResultMode.FILE_IMPACT.value
            and _is_provider_symbol_candidate(candidate)
            else ContextGraphAnchorProvenance.LEXICAL_FILE
        )
        graph_candidate = _context_graph_anchor_candidate(
            [candidate],
            target=target,
            provenance=provenance,
        )
        diagnostic_pairs.append((candidate, graph_candidate))
        if ContextEvidenceKind.EXPLICIT_NAMED_FILE_IDENTITY in evidence_kinds:
            named_file_pairs.append((candidate, graph_candidate))
        if _is_direct_lexical_anchor_candidate(candidate) or _is_provider_symbol_candidate(candidate):
            heuristic_candidates_by_path.setdefault(path.removeprefix(product_prefix), []).append(candidate)

    named_groups: dict[
        tuple[str, ...],
        list[tuple[ContextCandidate, ContextGraphAnchorCandidate]],
    ] = {}
    for pair in _dedupe_graph_anchor_pairs(named_file_pairs):
        identity = _named_file_identity(pair[0].source_ref.path)
        if identity:
            named_groups.setdefault(identity, []).append(pair)

    if exact_pairs:
        exact_symbols = _dedupe_graph_anchor_pairs(
            pair
            for pair in exact_pairs
            if ContextEvidenceKind.EXACT_SYMBOL in set(pair[0].evidence_kinds)
        )
        exact_files = _dedupe_graph_anchor_pairs(
            pair
            for pair in exact_pairs
            if set(pair[0].evidence_kinds) & _EXACT_FILE_ANCHOR_KINDS
            and ContextEvidenceKind.EXACT_SYMBOL not in set(pair[0].evidence_kinds)
        )
        if exact_symbols:
            symbol_paths = {pair[1].anchor.path for pair in exact_symbols}
            file_paths = {pair[1].anchor.path for pair in exact_files}
            if len(exact_symbols) != 1 or any(path not in symbol_paths for path in file_paths):
                return (
                    _ambiguous_graph_anchor_resolution([*exact_symbols, *exact_files]),
                    projection_index,
                )
            if not exact_files and len(named_groups) == 1:
                named_pairs = next(iter(named_groups.values()))
                named_paths = {
                    pair[1].anchor.path
                    for pair in named_pairs
                }
                symbol_path = exact_symbols[0][1].anchor.path
                if len(named_paths) != 1 or symbol_path not in named_paths:
                    return (
                        _ambiguous_graph_anchor_resolution(
                            _dedupe_graph_anchor_pairs(
                                [*exact_symbols, *named_pairs]
                            )
                        ),
                        projection_index,
                    )
            selected = exact_symbols
        else:
            selected = exact_files
        ordered = _dedupe_graph_anchor_pairs(selected)
        if len(ordered) != 1:
            return _ambiguous_graph_anchor_resolution(ordered), projection_index
        graph_candidates = (ordered[0][1],)
        selectable_candidates = tuple(
            candidate
            for candidate in graph_candidates
            if selection_available(candidate.anchor.path)
        )
        return (
            ContextAnchorResolution(
                status=(
                    ContextAnchorStatus.RESOLVED
                    if selectable_candidates
                    else ContextAnchorStatus.UNRESOLVED
                ),
                code=(
                    ContextAnchorResolutionCode.RESOLVED
                    if selectable_candidates
                    else ContextAnchorResolutionCode.UNRESOLVED
                ),
                anchors=selectable_candidates,
                candidates=graph_candidates,
                selection_coverage=_anchor_selection_coverage(
                    candidates=graph_candidates,
                    anchors=selectable_candidates,
                    eligible_paths={
                        candidate.anchor.path
                        for candidate in selectable_candidates
                    },
                    component_ids_by_path=component_ids_by_path,
                ),
            ),
            projection_index,
        )

    if knowledge_pairs:
        ranked_knowledge_pairs = sorted(
            _dedupe_graph_anchor_pairs(knowledge_pairs),
            key=lambda pair: _candidate_sort_key(pair[0]),
        )
        graph_candidates = tuple(
            graph_candidate for _candidate, graph_candidate in ranked_knowledge_pairs
        )
        selectable_candidates = tuple(
            graph_candidate
            for _candidate, graph_candidate in ranked_knowledge_pairs
            if selection_available(graph_candidate.anchor.path)
        )
        anchors = selectable_candidates[:GRAPH_ANCHOR_LIMIT]
        return (
            ContextAnchorResolution(
                status=(
                    ContextAnchorStatus.RESOLVED
                    if anchors
                    else ContextAnchorStatus.UNRESOLVED
                ),
                code=(
                    ContextAnchorResolutionCode.RESOLVED
                    if anchors
                    else ContextAnchorResolutionCode.UNRESOLVED
                ),
                anchors=anchors,
                candidates=graph_candidates,
                selection_coverage=_anchor_selection_coverage(
                    candidates=graph_candidates,
                    anchors=anchors,
                    eligible_paths={
                        candidate.anchor.path
                        for candidate in selectable_candidates
                    },
                    component_ids_by_path=component_ids_by_path,
                ),
            ),
            projection_index,
        )

    if named_file_pairs:
        ambiguous_named_pairs = [
            pair
            for pairs in named_groups.values()
            if len({candidate.anchor.path for _source, candidate in pairs}) > 1
            for pair in pairs
        ]
        if ambiguous_named_pairs:
            return (
                _ambiguous_graph_anchor_resolution(
                    _dedupe_graph_anchor_pairs(ambiguous_named_pairs)
                ),
                projection_index,
            )
        named_paths = {
            graph_candidate.anchor.path
            for pairs in named_groups.values()
            for _candidate, graph_candidate in pairs
        }
        named_candidates_by_path = {
            path: candidates
            for path, candidates in heuristic_candidates_by_path.items()
            if path in named_paths
        }
        named_anchors, named_candidates, named_coverage = (
            _ranked_heuristic_graph_anchors(
                named_candidates_by_path,
                target=target,
                graph_support=graph_support_by_path or {},
                allow_provider_symbol=mode != ContextResultMode.FILE_IMPACT.value,
                unavailable_paths={
                    path
                    for path in named_candidates_by_path
                    if not selection_available(path)
                },
                component_ids_by_path=component_ids_by_path,
                prefer_product_sources=False,
            )
        )
        if named_anchors:
            return (
                ContextAnchorResolution(
                    status=ContextAnchorStatus.RESOLVED,
                    code=ContextAnchorResolutionCode.RESOLVED,
                    anchors=named_anchors,
                    candidates=named_candidates,
                    selection_coverage=named_coverage,
                ),
                projection_index,
            )
        if named_candidates:
            return (
                ContextAnchorResolution(
                    status=ContextAnchorStatus.UNRESOLVED,
                    code=ContextAnchorResolutionCode.UNRESOLVED,
                    candidates=named_candidates,
                    selection_coverage=named_coverage,
                ),
                projection_index,
            )

    if projection_index is None and snapshot is not None and heuristic_candidates_by_path:
        projection_index = build_context_projection_index(snapshot)
    graph_support = graph_support_by_path or {}
    heuristic_anchors, heuristic_candidates, selection_coverage = _ranked_heuristic_graph_anchors(
        heuristic_candidates_by_path,
        target=target,
        graph_support=graph_support,
        allow_provider_symbol=mode != ContextResultMode.FILE_IMPACT.value,
        unavailable_paths={
            path
            for path in heuristic_candidates_by_path
            if not selection_available(path)
        },
        component_ids_by_path=component_ids_by_path,
    )
    if heuristic_anchors:
        return (
            ContextAnchorResolution(
                status=ContextAnchorStatus.RESOLVED,
                code=ContextAnchorResolutionCode.RESOLVED,
                anchors=heuristic_anchors,
                candidates=heuristic_candidates,
                selection_coverage=selection_coverage,
            ),
            projection_index,
        )
    if heuristic_candidates:
        return (
            ContextAnchorResolution(
                status=ContextAnchorStatus.UNRESOLVED,
                code=ContextAnchorResolutionCode.UNRESOLVED,
                candidates=heuristic_candidates,
                selection_coverage=selection_coverage,
            ),
            projection_index,
        )

    candidates = tuple(
        graph_candidate
        for _candidate, graph_candidate in sorted(
            diagnostic_pairs,
            key=lambda item: (
                -CONTEXT_ANCHOR_STRENGTH_PRIORITY[item[0].anchor_strength],
                -item[0].score,
                item[0].source_ref.path,
                item[0].source_ref.line_start,
            ),
        )[:5]
    )
    return (
        ContextAnchorResolution(
            status=ContextAnchorStatus.UNRESOLVED,
            code=ContextAnchorResolutionCode.UNRESOLVED,
            candidates=candidates,
        ),
        projection_index,
    )


def _context_graph_anchor_candidate(
    candidates: list[ContextCandidate],
    *,
    target: RepoTarget,
    provenance: ContextGraphAnchorProvenance,
    lexical_rank: int = 0,
    graph_support: dict[str, Any] | None = None,
    primary_candidate: ContextCandidate | None = None,
    component_ids: tuple[str, ...] = (),
) -> ContextGraphAnchorCandidate:
    ranked = sorted(candidates, key=_candidate_sort_key)
    primary = primary_candidate or ranked[0]
    repo_path = primary.source_ref.path.removeprefix(f"{target.display_path.rstrip('/')}/")
    symbol_anchor = bool(
        _is_provider_symbol_candidate(primary)
        and (
            provenance == ContextGraphAnchorProvenance.PROVIDER_SYMBOL
            or (
                provenance == ContextGraphAnchorProvenance.EXACT_IDENTITY
                and ContextEvidenceKind.EXACT_SYMBOL in set(primary.evidence_kinds)
            )
        )
    )
    evidence_kinds = tuple(
        sorted(
            {kind for candidate in candidates for kind in candidate.evidence_kinds},
            key=lambda kind: kind.value,
        )
    )
    anchor_strength = max(
        (candidate.anchor_strength for candidate in candidates),
        key=lambda strength: CONTEXT_ANCHOR_STRENGTH_PRIORITY[strength],
    )
    score_breakdown: dict[str, float] = {}
    query_term_matches: dict[str, set[str]] = {}
    for candidate in candidates:
        for key, value in candidate.score_breakdown.items():
            score_breakdown[key] = max(score_breakdown.get(key, 0.0), float(value))
        for field_name, terms in candidate.query_term_matches.items():
            query_term_matches.setdefault(field_name, set()).update(terms)
    lane = context_retrieval_lane(
        kind=primary.source_ref.kind,
        path=primary.source_ref.path,
        repository_path=target.display_path,
        document_role=primary.document_role,
    )
    return ContextGraphAnchorCandidate(
        anchor=GraphContextAnchor(
            kind=GraphContextAnchorKind.SYMBOL if symbol_anchor else GraphContextAnchorKind.FILE,
            path=repo_path,
            symbol=primary.source_ref.section if symbol_anchor else "",
            line_start=primary.source_ref.line_start if symbol_anchor else 0,
            line_end=primary.source_ref.line_end if symbol_anchor else 0,
            provider=primary.source_ref.provider if symbol_anchor else "",
            provider_symbol_id=primary.source_ref.provider_symbol_id if symbol_anchor else "",
        ),
        source_ref=primary.source_ref,
        evidence_kinds=evidence_kinds,
        anchor_strength=anchor_strength,
        anchor_provenance=provenance,
        retrieval_lane=lane.value,
        lexical_rank=lexical_rank,
        retrieval_score=max(candidate.score for candidate in candidates),
        score_breakdown=score_breakdown,
        query_term_matches={
            field_name: tuple(sorted(terms))
            for field_name, terms in sorted(query_term_matches.items())
            if terms
        },
        graph_support=dict(graph_support or {}),
        component_ids=tuple(sorted(set(component_ids))),
        related_record_ids=tuple(
            sorted({record_id for candidate in candidates for record_id in candidate.related_record_ids})
        ),
    )


def _graph_corroborated_provider_symbols(
    candidates: list[ContextCandidate],
    *,
    target: RepoTarget,
    graph_support: dict[str, Any],
) -> set[tuple[str, str]]:
    call_endpoint_ids = {
        str(connection.get(key) or "")
        for connection in graph_support.get("candidate_connections", [])
        if isinstance(connection, dict)
        and connection.get("assertion") == "resolved"
        and connection.get("edge") == "CALLS"
        for key in ("from_id", "to_id")
        if str(connection.get(key) or "")
    }
    return {
        (candidate.source_ref.provider, candidate.source_ref.provider_symbol_id)
        for candidate in candidates
        if _is_provider_symbol_candidate(candidate)
        and _coverage_term_breadth_tier(
            set(_query_term_match_set(candidate.query_term_matches))
        )
        >= 2
        and symbol_id(
            target.id,
            candidate.source_ref.provider,
            candidate.source_ref.provider_symbol_id,
        )
        in call_endpoint_ids
    }


def _ranked_heuristic_graph_anchors(
    candidates_by_path: dict[str, list[ContextCandidate]],
    *,
    target: RepoTarget,
    graph_support: dict[str, dict[str, Any]] | None = None,
    allow_provider_symbol: bool = True,
    unavailable_paths: set[str] | None = None,
    component_ids_by_path: dict[str, tuple[str, ...]] | None = None,
    prefer_product_sources: bool = True,
) -> tuple[
    tuple[ContextGraphAnchorCandidate, ...],
    tuple[ContextGraphAnchorCandidate, ...],
    dict[str, Any],
]:
    entries: list[dict[str, Any]] = []
    graph_support = graph_support or {}
    unavailable_paths = unavailable_paths or set()
    for repo_path, path_candidates in candidates_by_path.items():
        if _direct_query_score(path_candidates) <= 0:
            continue
        ranked = sorted(path_candidates, key=_candidate_sort_key)
        primary = ranked[0]
        strong_symbol = any(_is_strong_provider_symbol_candidate(candidate) for candidate in path_candidates)
        path_graph_support = graph_support.get(repo_path, {})
        provider_evidence = provider_symbol_evidence_candidate(
            path_candidates,
            corroborated_provider_symbols=_graph_corroborated_provider_symbols(
                path_candidates,
                target=target,
                graph_support=path_graph_support,
            ),
        )
        provider_symbol = provider_evidence is not None
        lane = context_retrieval_lane(
            kind=primary.source_ref.kind,
            path=primary.source_ref.path,
            repository_path=target.display_path,
            document_role=primary.document_role,
        )
        entries.append(
            {
                "path": repo_path,
                "path_identity": repo_path,
                "candidates": path_candidates,
                "primary": primary,
                "strong_symbol": strong_symbol,
                "provider_symbol": provider_symbol,
                "provider_evidence": provider_evidence,
                "field_count": _lexical_anchor_field_count(path_candidates),
                "query_coverage": max(
                    float(candidate.score_breakdown.get("exact") or 0.0)
                    for candidate in path_candidates
                ),
                "path_name_coverage": max(
                    float(candidate.score_breakdown.get("path_name") or 0.0)
                    for candidate in path_candidates
                ),
                "path_area_coverage": max(
                    float(candidate.score_breakdown.get("path_area") or 0.0)
                    for candidate in path_candidates
                ),
                "path_scope_coverage": max(
                    float(candidate.score_breakdown.get("path_scope") or 0.0)
                    for candidate in path_candidates
                ),
                "lane_key": _lexical_anchor_lane_key(lane, source_kind=primary.source_ref.kind),
                "coverage_roles": {_lexical_anchor_lane_key(lane, source_kind=primary.source_ref.kind)},
                "component_ids": tuple(
                    (component_ids_by_path or {}).get(repo_path, ())
                ),
                "query_term_matches": _merge_query_term_matches(
                    candidate.query_term_matches for candidate in path_candidates
                ),
                "graph_support": path_graph_support,
                "history_corroborated": any(
                    ContextEvidenceKind.HISTORY_CORROBORATION
                    in set(candidate.evidence_kinds)
                    for candidate in path_candidates
                ),
                "related_record_ids": {
                    record_id
                    for candidate in path_candidates
                    for record_id in candidate.related_record_ids
                },
                "selection_available": repo_path not in unavailable_paths,
                "parallel_anchor_coverage": allow_provider_symbol,
                "direct_query": True,
                "structured_relationship_source": any(
                    candidate.source_ref.section_kind
                    is ContextSectionKind.PROVIDER_RELATIONSHIP
                    for candidate in path_candidates
                ),
                "role_priority": 0,
            }
        )
    if not entries:
        return (), (), {}
    eligible_entries = _eligible_coverage_profiles(entries)
    source_entries = [
        entry
        for entry in eligible_entries
        if str(entry.get("lane_key") or "")
        in {ContextRetrievalLane.PRODUCT_SOURCE.value, "product_config"}
    ]
    anchor_entries = (
        source_entries
        if prefer_product_sources and source_entries
        else eligible_entries
    )
    ordered_entries = _select_coverage_profiles(
        anchor_entries,
        limit=len(anchor_entries),
    )
    selected = _select_anchor_evidence_profiles(
        anchor_entries,
        limit=GRAPH_ANCHOR_LIMIT,
    )
    ordered_paths = [str(entry["path"]) for entry in ordered_entries]
    ordered_paths.extend(
        str(entry["path"])
        for entry in entries
        if str(entry["path"]) not in ordered_paths
    )
    order_by_path = {
        path: rank for rank, path in enumerate(ordered_paths, start=1)
    }

    graph_candidates: dict[str, ContextGraphAnchorCandidate] = {}
    for entry in entries:
        provenance = (
            ContextGraphAnchorProvenance.PROVIDER_SYMBOL
            if allow_provider_symbol and entry["provider_symbol"]
            else ContextGraphAnchorProvenance.LEXICAL_FILE
        )
        graph_candidates[str(entry["path"])] = _context_graph_anchor_candidate(
            entry["candidates"],
            target=target,
            provenance=provenance,
            lexical_rank=order_by_path[str(entry["path"])],
            graph_support=entry["graph_support"],
            primary_candidate=entry["provider_evidence"],
            component_ids=tuple(entry["component_ids"]),
        )
    all_candidates = tuple(graph_candidates[path] for path in ordered_paths)
    anchors = tuple(graph_candidates[str(entry["path"])] for entry in selected)
    coverage = _anchor_selection_coverage(
        candidates=all_candidates,
        anchors=anchors,
        eligible_paths={
            str(entry["path"])
            for entry in anchor_entries
            if entry.get("selection_available", True)
        },
        bounded_reason=(
            "weak_single_term_fallback"
            if _coverage_profiles_use_weak_single_term_fallback(anchor_entries)
            else ""
        ),
        component_ids_by_path=component_ids_by_path,
    )
    return anchors, all_candidates, coverage


def _merge_query_term_matches(
    values: Iterable[dict[str, tuple[str, ...]] | dict[str, list[str]]],
) -> dict[str, tuple[str, ...]]:
    merged: dict[str, set[str]] = {}
    for value in values:
        for field_name, terms in value.items():
            merged.setdefault(str(field_name), set()).update(str(term) for term in terms if str(term))
    return {
        field_name: tuple(sorted(terms))
        for field_name, terms in sorted(merged.items())
        if terms
    }


def _coverage_profile_pairs(profile: dict[str, Any]) -> set[tuple[str, str]]:
    matches = profile.get("query_term_matches")
    if not isinstance(matches, dict):
        return set()
    return {
        (str(field_name), str(term))
        for field_name, terms in matches.items()
        if str(field_name) in {"path", "section", "body"} and isinstance(terms, (list, tuple))
        for term in terms
        if str(term)
    }


def _coverage_profile_structural_pairs(
    profile: dict[str, Any],
) -> set[tuple[str, str]]:
    return {
        pair
        for pair in _coverage_profile_pairs(profile)
        if pair[0] in {"path", "section"}
    }


def _coverage_profile_term_count(profile: dict[str, Any]) -> int:
    return len(_coverage_profile_terms(profile))


def _coverage_profile_terms(profile: dict[str, Any]) -> set[str]:
    return {term for _field_name, term in _coverage_profile_pairs(profile)}


def _coverage_term_breadth_tier(terms: set[str]) -> int:
    # Saturating breadth prevents vocabulary-heavy consumers from crowding out distinct evidence.
    return min(3, len(terms).bit_length())


def _coverage_profile_anchor_priority(profile: dict[str, Any]) -> int:
    primary = profile.get("primary")
    if isinstance(primary, ContextCandidate):
        return CONTEXT_ANCHOR_STRENGTH_PRIORITY[primary.anchor_strength]
    try:
        strength = ContextAnchorStrength(str(profile.get("anchor_strength") or ContextAnchorStrength.NONE.value))
    except ValueError:
        strength = ContextAnchorStrength.NONE
    return CONTEXT_ANCHOR_STRENGTH_PRIORITY[strength]


def _coverage_profile_support_count(profile: dict[str, Any]) -> int:
    support = profile.get("graph_support") if isinstance(profile.get("graph_support"), dict) else {}
    return int(support.get("direct_relation_count") or 0)


def _coverage_profile_preselection_support(profile: dict[str, Any]) -> dict[str, Any]:
    support = profile.get("preselection_graph_support")
    if isinstance(support, dict):
        return support
    if not isinstance(profile.get("item"), dict):
        graph_support = profile.get("graph_support")
        if isinstance(graph_support, dict):
            return graph_support
    return {}


def _coverage_profile_preselection_connections(
    profile: dict[str, Any],
) -> list[dict[str, Any]]:
    values = _coverage_profile_preselection_support(profile).get("candidate_connections")
    if not isinstance(values, list):
        return []
    return [
        connection
        for connection in values
        if isinstance(connection, dict)
        and connection.get("assertion") == "resolved"
        and _coverage_graph_repo_path(str(connection.get("from_path") or ""))
        and _coverage_graph_repo_path(str(connection.get("to_path") or ""))
    ]


def _coverage_profile_direct_test_count(profile: dict[str, Any]) -> int:
    support = profile.get("graph_support") if isinstance(profile.get("graph_support"), dict) else {}
    projected_count = int(support.get("direct_test_count") or 0)
    preselection_count = sum(
        1
        for connection in _coverage_profile_preselection_connections(profile)
        if connection.get("edge") == "TESTS_FILE"
    )
    return max(projected_count, preselection_count)


def _coverage_profile_candidate_neighbor_count(profile: dict[str, Any]) -> int:
    return len(_coverage_profile_neighbor_paths(profile))


def _coverage_profile_roles(profile: dict[str, Any]) -> set[str]:
    raw_roles = profile.get("coverage_roles")
    if isinstance(raw_roles, (set, list, tuple)):
        return {str(role) for role in raw_roles if str(role)}
    lane = str(profile.get("lane_key") or "")
    return {lane} if lane else set()


def _coverage_profile_graph_supported(profile: dict[str, Any]) -> bool:
    return bool(
        _coverage_profile_support_count(profile) > 0
        or profile.get("graph_supported")
        or _coverage_profile_preselection_connections(profile)
    )


def _coverage_profile_is_graph_seed(profile: dict[str, Any]) -> bool:
    item = profile.get("item") if isinstance(profile.get("item"), dict) else {}
    return ContextEvidenceKind.GRAPH_SEED.value in {
        str(value)
        for value in item.get("evidence_kinds", [])
    }


def _coverage_profile_is_seed_or_anchor_candidate(profile: dict[str, Any]) -> bool:
    if isinstance(profile.get("item"), dict):
        return _coverage_profile_is_graph_seed(profile)
    return isinstance(profile.get("primary"), ContextCandidate)


def _coverage_profile_has_unsupported_provider_echo(profile: dict[str, Any]) -> bool:
    if not profile.get("provider_symbol") or _coverage_profile_provider_evidence_supported(profile):
        return False
    pairs = _coverage_profile_pairs(profile)
    section_terms = {
        term
        for field_name, term in pairs
        if field_name == "section"
    }
    body_terms = {
        term
        for field_name, term in pairs
        if field_name == "body"
    }
    return bool(section_terms and body_terms and body_terms <= section_terms)


def _coverage_profile_has_explicit_identity(profile: dict[str, Any]) -> bool:
    return bool(
        profile.get("strong_symbol")
        or _coverage_profile_anchor_priority(profile)
        >= CONTEXT_ANCHOR_STRENGTH_PRIORITY[ContextAnchorStrength.EXACT]
    )


def _coverage_profile_has_scoped_path_evidence(profile: dict[str, Any]) -> bool:
    return bool(
        float(profile.get("path_area_coverage") or 0.0) > 0.0
        or float(profile.get("path_scope_coverage") or 0.0) > 0.0
    )


def _coverage_profile_neighbor_paths(profile: dict[str, Any]) -> set[str]:
    support = profile.get("graph_support") if isinstance(profile.get("graph_support"), dict) else {}
    values = support.get("candidate_neighbor_paths")
    projected_paths = (
        {str(path) for path in values if str(path)}
        if isinstance(values, (list, tuple, set))
        else set()
    )
    path_identity = _coverage_profile_path_identity(profile)
    preselection_paths = {
        to_path if from_path == path_identity else from_path
        for connection in _coverage_profile_preselection_connections(profile)
        if (from_path := _coverage_graph_repo_path(str(connection.get("from_path") or "")))
        and (to_path := _coverage_graph_repo_path(str(connection.get("to_path") or "")))
        and path_identity in {from_path, to_path}
    }
    return projected_paths | preselection_paths


def _coverage_profile_direct_call_neighbor_paths(profile: dict[str, Any]) -> set[str]:
    support = profile.get("graph_support") if isinstance(profile.get("graph_support"), dict) else {}
    values = support.get("direct_call_neighbor_paths")
    projected_paths = (
        {str(path) for path in values if str(path)}
        if isinstance(values, (list, tuple, set))
        else set()
    )
    path_identity = _coverage_profile_path_identity(profile)
    preselection_paths = {
        to_path if from_path == path_identity else from_path
        for connection in _coverage_profile_preselection_connections(profile)
        if connection.get("edge") == "CALLS"
        and (from_path := _coverage_graph_repo_path(str(connection.get("from_path") or "")))
        and (to_path := _coverage_graph_repo_path(str(connection.get("to_path") or "")))
        and path_identity in {from_path, to_path}
    }
    return projected_paths | preselection_paths


def _coverage_profile_direct_call_symbol_ids(profile: dict[str, Any]) -> set[str]:
    support = profile.get("graph_support") if isinstance(profile.get("graph_support"), dict) else {}
    values = support.get("direct_call_symbol_ids")
    projected_ids = (
        {str(value) for value in values if str(value)}
        if isinstance(values, (list, tuple, set))
        else set()
    )
    preselection_ids = {
        str(connection.get(key) or "")
        for connection in _coverage_profile_preselection_connections(profile)
        if connection.get("edge") == "CALLS"
        for key in ("from_id", "to_id")
        if str(connection.get(key) or "")
    }
    return projected_ids | preselection_ids


def _coverage_profile_exact_reference_symbol_ids(profile: dict[str, Any]) -> set[str]:
    values = profile.get("exact_reference_symbol_ids")
    if not isinstance(values, (list, tuple, set)):
        return set()
    return {str(value) for value in values if str(value)}


def _coverage_profile_path_identity(profile: dict[str, Any]) -> str:
    return str(profile.get("path_identity") or profile.get("path") or "")


def _coverage_profile_test_target_paths(profile: dict[str, Any]) -> set[str]:
    item = profile.get("item") if isinstance(profile.get("item"), dict) else {}
    graph_path = item.get("graph_path") if isinstance(item.get("graph_path"), list) else []
    path_identity = _coverage_profile_path_identity(profile)
    projected_paths = {
        _coverage_graph_repo_path(str(relation.get("to_path") or ""))
        for relation in graph_path
        if isinstance(relation, dict)
        and relation.get("edge") == "TESTS_FILE"
        and _coverage_graph_repo_path(str(relation.get("from_path") or ""))
        == path_identity
        and _coverage_graph_repo_path(str(relation.get("to_path") or ""))
    }
    preselection_paths = {
        _coverage_graph_repo_path(str(connection.get("to_path") or ""))
        for connection in _coverage_profile_preselection_connections(profile)
        if connection.get("edge") == "TESTS_FILE"
        and _coverage_graph_repo_path(str(connection.get("from_path") or ""))
        == path_identity
        and _coverage_graph_repo_path(str(connection.get("to_path") or ""))
    }
    return projected_paths | preselection_paths


def _coverage_profile_has_preselection_evidence_connection(
    profile: dict[str, Any],
) -> bool:
    return bool(_coverage_profile_preselection_evidence_neighbor_paths(profile))


def _coverage_profile_preselection_evidence_neighbor_paths(
    profile: dict[str, Any],
) -> set[str]:
    path_identity = _coverage_profile_path_identity(profile)
    neighbor_paths: set[str] = set()
    for connection in _coverage_profile_preselection_connections(profile):
        edge = str(connection.get("edge") or "")
        from_path = _coverage_graph_repo_path(str(connection.get("from_path") or ""))
        to_path = _coverage_graph_repo_path(str(connection.get("to_path") or ""))
        if path_identity not in {from_path, to_path}:
            continue
        if edge in {"CALLS", STRUCTURED_EDGE_KIND}:
            neighbor_paths.add(to_path if from_path == path_identity else from_path)
        if edge == "TESTS_FILE" and to_path == path_identity:
            neighbor_paths.add(from_path)
    return {path for path in neighbor_paths if path}


def _coverage_profile_evidence_neighbor_paths(profile: dict[str, Any]) -> set[str]:
    path_identity = _coverage_profile_path_identity(profile)
    item = profile.get("item") if isinstance(profile.get("item"), dict) else {}
    graph_path = item.get("graph_path") if isinstance(item.get("graph_path"), list) else []
    projected_paths: set[str] = set()
    for relation in graph_path:
        if not isinstance(relation, dict):
            continue
        edge = str(relation.get("edge") or "")
        from_path = _coverage_graph_repo_path(str(relation.get("from_path") or ""))
        to_path = _coverage_graph_repo_path(str(relation.get("to_path") or ""))
        if path_identity not in {from_path, to_path}:
            continue
        if edge in {"CALLS", STRUCTURED_EDGE_KIND}:
            projected_paths.add(to_path if from_path == path_identity else from_path)
        elif edge == "TESTS_FILE" and to_path == path_identity:
            projected_paths.add(from_path)
    return {
        path
        for path in (
            projected_paths
            | _coverage_profile_preselection_evidence_neighbor_paths(profile)
        )
        if path and path != path_identity
    }


def _coverage_profile_query_evidence_supported(profile: dict[str, Any]) -> bool:
    if _coverage_profile_has_explicit_identity(profile):
        return True
    if profile.get("strong_symbol") or _coverage_profile_provider_evidence_supported(profile):
        return True
    return bool(
        not _coverage_profile_has_unsupported_provider_echo(profile)
        and (
            _coverage_profile_structural_pairs(profile)
            or _coverage_term_breadth_tier(_coverage_profile_terms(profile)) >= 2
        )
    )


def _coverage_profile_connected_to_selected(
    profile: dict[str, Any],
    *,
    selected: list[dict[str, Any]],
) -> bool:
    profile_path = _coverage_profile_path_identity(profile)
    if str(profile.get("lane_key") or "") == ContextRetrievalLane.PRODUCT_TEST.value:
        test_targets = _coverage_profile_test_target_paths(profile)
        source_references = {
            _coverage_profile_path_identity(item)
            for item in selected
            if str(item.get("lane_key") or "")
            in {
                ContextRetrievalLane.PRODUCT_SOURCE.value,
                "product_config",
                ContextRetrievalLane.PRODUCT_DATA.value,
            }
        }
        if source_references:
            return bool(test_targets & source_references)
    neighbor_paths = _coverage_profile_neighbor_paths(profile)
    return bool(
        any(
            _coverage_profile_path_identity(item) in neighbor_paths
            or profile_path in _coverage_profile_neighbor_paths(item)
            for item in selected
        )
    )


def _coverage_profile_direct_call_connected_to_selected(
    profile: dict[str, Any],
    *,
    selected: list[dict[str, Any]],
) -> bool:
    profile_path = _coverage_profile_path_identity(profile)
    neighbor_paths = _coverage_profile_direct_call_neighbor_paths(profile)
    return bool(
        any(
            _coverage_profile_path_identity(item) in neighbor_paths
            or profile_path in _coverage_profile_direct_call_neighbor_paths(item)
            for item in selected
        )
    )


def _coverage_profile_evidence_connected_to_selected(
    profile: dict[str, Any],
    *,
    selected: list[dict[str, Any]],
) -> bool:
    selected_paths = {
        _coverage_profile_path_identity(item)
        for item in selected
        if _coverage_profile_path_identity(item)
    }
    return bool(
        _coverage_profile_evidence_neighbor_paths(profile) & selected_paths
    )


def _coverage_profile_exact_call_connected_to_selected(
    profile: dict[str, Any],
    *,
    selected: list[dict[str, Any]],
) -> bool:
    call_symbol_ids = _coverage_profile_direct_call_symbol_ids(profile)
    reference_symbol_ids = {
        symbol_id_value
        for item in selected
        for symbol_id_value in _coverage_profile_exact_reference_symbol_ids(item)
    }
    selected_call_symbol_ids = {
        symbol_id_value
        for item in selected
        for symbol_id_value in _coverage_profile_direct_call_symbol_ids(item)
    }
    return bool(
        call_symbol_ids & reference_symbol_ids
        or selected_call_symbol_ids & _coverage_profile_exact_reference_symbol_ids(profile)
    )


def _coverage_profiles_have_internal_evidence_connection(
    profiles: list[dict[str, Any]],
) -> bool:
    selected_paths = {
        _coverage_profile_path_identity(profile)
        for profile in profiles
        if _coverage_profile_path_identity(profile)
    }
    return any(
        _coverage_profile_evidence_neighbor_paths(profile)
        & (selected_paths - {_coverage_profile_path_identity(profile)})
        for profile in profiles
    )


def _coverage_profile_contribution(
    profile: dict[str, Any],
    *,
    selected: list[dict[str, Any]],
) -> _CoverageContribution:
    covered_pairs = {pair for item in selected for pair in _coverage_profile_pairs(item)}
    selected_lanes = {
        str(item.get("lane_key") or "")
        for item in selected
        if str(item.get("lane_key") or "")
    }
    lane = str(profile.get("lane_key") or "")
    selected_roles = {
        role
        for item in selected
        if str(item.get("lane_key") or "") == lane
        for role in _coverage_profile_roles(item)
    }
    selected_components = _coverage_selected_component_ids(selected)
    pairs = _coverage_profile_pairs(profile)
    terms = _coverage_profile_terms(profile)
    covered_terms = {
        term
        for item in selected
        for term in _coverage_profile_terms(item)
    }
    new_terms = terms - covered_terms
    components = _coverage_profile_component_ids(profile)
    new_pairs = pairs - covered_pairs
    new_lane = lane if lane and lane not in selected_lanes else ""
    new_roles = _coverage_profile_roles(profile) - selected_roles
    new_components = components - selected_components
    explicit_identity = _coverage_profile_has_explicit_identity(profile)
    coverage_identity_required = bool(profile.get("coverage_identity_required"))
    connected_to_selected = _coverage_profile_connected_to_selected(
        profile,
        selected=selected,
    )
    scope_coherent = bool(
        not any(_coverage_profile_has_scoped_path_evidence(item) for item in selected)
        or _coverage_profile_has_scoped_path_evidence(profile)
        or connected_to_selected
        or explicit_identity
        or profile.get("structured_relationship_source")
        or _coverage_term_breadth_tier(new_terms) >= 2
    )
    body_query_evidence = any(field_name == "body" for field_name, _term in pairs)
    provider_identity = _coverage_profile_provider_evidence_supported(profile)
    unsupported_provider_echo = _coverage_profile_has_unsupported_provider_echo(profile)
    effective_new_terms = (
        new_terms
        if scope_coherent and not unsupported_provider_echo
        else set()
    )
    if profile.get("compact_projection_candidate"):
        lexical = bool(
            new_pairs
            and (
                explicit_identity
                or (scope_coherent and provider_identity)
                or (
                    body_query_evidence
                    and bool(effective_new_terms)
                )
            )
        )
        component_contribution = bool(
            new_components
            and (
                connected_to_selected
                or (scope_coherent and provider_identity)
                or (
                    scope_coherent
                    and _coverage_profile_is_graph_seed(profile)
                )
                or profile.get("structured_relationship_source")
                or (
                    body_query_evidence
                    and bool(effective_new_terms)
                )
            )
        )
    else:
        lexical = bool(
            new_pairs
            and (
                explicit_identity
                or bool(effective_new_terms)
                or (
                    scope_coherent
                    and new_components
                    and len(terms) > 1
                )
                or (
                    scope_coherent
                    and _coverage_profile_graph_supported(profile)
                    and len(terms) > 1
                )
            )
        )
        component_contribution = bool(
            new_components
            and (
                bool(effective_new_terms)
                or (
                    profile.get("direct_query", True)
                    and connected_to_selected
                )
                or (scope_coherent and len(terms) > 1)
            )
        )
    return _CoverageContribution(
        new_pairs=frozenset(new_pairs),
        new_lane=new_lane,
        new_roles=frozenset(new_roles),
        new_components=new_components,
        identity=coverage_identity_required,
        lexical=lexical,
        component=component_contribution,
    )


def _coverage_graph_repo_path(path: str) -> str:
    resolution = resolve_repo_selector_path(
        path,
        repository_path="",
    )
    return resolution.path if resolution.status == RepoSelectorStatus.RESOLVED else ""


def _coverage_source_ref_repo_path(path: str, *, repository_path: str) -> str:
    workspace_path = _coverage_graph_repo_path(path)
    workspace_repository_path = _coverage_graph_repo_path(repository_path)
    if not workspace_path:
        return ""
    if not workspace_repository_path:
        return workspace_path
    prefix = f"{workspace_repository_path}/"
    return workspace_path.removeprefix(prefix) if workspace_path.startswith(prefix) else ""


def _coverage_pair_frequencies(profiles: list[dict[str, Any]]) -> dict[tuple[str, str], int]:
    frequencies: dict[tuple[str, str], int] = {}
    for profile in profiles:
        for pair in _coverage_profile_pairs(profile):
            frequencies[pair] = frequencies.get(pair, 0) + 1
    return frequencies


def _coverage_pair_weight(
    pairs: set[tuple[str, str]],
    frequencies: dict[tuple[str, str], int],
) -> float:
    return sum(1.0 / max(1, frequencies.get(pair, 1)) for pair in pairs)


def _coverage_profile_initial_connection_priority(profile: dict[str, Any]) -> int:
    raw_priority = profile.get("connection_priority")
    if isinstance(raw_priority, int):
        return raw_priority
    if _coverage_profile_direct_test_count(profile) > 0:
        return 0
    if _coverage_profile_candidate_neighbor_count(profile) > 0:
        return 2
    return 3


def _coverage_profile_initial_key(
    profile: dict[str, Any],
    *,
    frequencies: dict[tuple[str, str], int],
) -> tuple[Any, ...]:
    pairs = _coverage_profile_pairs(profile)
    structural_pairs = _coverage_profile_structural_pairs(profile)
    structural_terms = {term for _field_name, term in structural_pairs}
    lane = str(profile.get("lane_key") or "")
    connection_priority = _coverage_profile_initial_connection_priority(profile)
    explicit_test_connection = (
        lane == ContextRetrievalLane.PRODUCT_TEST.value
        and isinstance(profile.get("connection_priority"), int)
    )
    return (
        -_coverage_profile_anchor_priority(profile),
        0 if profile.get("strong_symbol") else 1,
        0 if profile.get("structured_relationship_source") else 1,
        0 if profile.get("direct_query", True) else 1,
        0 if lane in {ContextRetrievalLane.PRODUCT_SOURCE.value, "product_config"} else 1,
        connection_priority if explicit_test_connection else 3,
        -_coverage_term_breadth_tier(_coverage_profile_terms(profile)),
        0 if structural_pairs else 1,
        -_coverage_term_breadth_tier(structural_terms),
        -_coverage_pair_weight(structural_pairs, frequencies),
        connection_priority if not explicit_test_connection else 3,
        int(profile.get("role_priority") or 0),
        -_coverage_pair_weight(pairs, frequencies),
        -int(profile.get("field_count") or 0),
        -float(profile.get("query_coverage") or 0.0),
        -float(profile.get("path_name_coverage") or 0.0),
        -float(profile.get("path_area_coverage") or 0.0),
        -float(profile.get("path_scope_coverage") or 0.0),
        0 if _coverage_profile_direct_test_count(profile) > 0 else 1,
        -_coverage_profile_candidate_neighbor_count(profile),
        0 if _coverage_profile_graph_supported(profile) else 1,
        -float(profile.get("score") or getattr(profile.get("primary"), "score", 0.0)),
        -_coverage_profile_direct_test_count(profile),
        -_coverage_profile_support_count(profile),
        str(profile.get("path") or ""),
    )


def _coverage_profile_related_key(
    profile: dict[str, Any],
    *,
    selected: list[dict[str, Any]],
    frequencies: dict[tuple[str, str], int],
) -> tuple[Any, ...]:
    covered_pairs = {pair for item in selected for pair in _coverage_profile_pairs(item)}
    selected_lanes = {str(item.get("lane_key") or "") for item in selected}
    selected_components = _coverage_selected_component_ids(selected)
    selected_roles = {role for item in selected for role in _coverage_profile_roles(item)}
    pairs = _coverage_profile_pairs(profile)
    new_pairs = pairs - covered_pairs
    new_structural_pairs = {
        pair for pair in new_pairs if pair[0] in {"path", "section"}
    }
    ranked_structural_pairs = new_structural_pairs
    ranked_structural_terms = {
        term for _field_name, term in ranked_structural_pairs
    }
    covered_terms = {term for _field_name, term in covered_pairs}
    new_terms = _coverage_profile_terms(profile) - covered_terms
    lane = str(profile.get("lane_key") or "")
    new_components = _coverage_profile_component_ids(profile) - selected_components
    new_roles = _coverage_profile_roles(profile) - selected_roles
    affinity = max(
        (
            _shared_parent_prefix_depth(
                _coverage_profile_path_identity(profile),
                _coverage_profile_path_identity(item),
            )
            for item in selected
        ),
        default=0,
    )
    connected_to_selected = _coverage_profile_connected_to_selected(
        profile,
        selected=selected,
    )
    connection_priority = (
        _coverage_profile_initial_connection_priority(profile)
        if connected_to_selected
        else 3
    )
    explicit_test_connection = (
        lane == ContextRetrievalLane.PRODUCT_TEST.value
        and isinstance(profile.get("connection_priority"), int)
    )
    structural_provenance = (
        0
        if connected_to_selected
        else 1
        if _coverage_profile_is_graph_seed(profile)
        else 2
        if _coverage_profile_provider_evidence_supported(profile)
        else 3
        if any(field_name in {"path", "section"} for field_name, _term in pairs)
        else 4
    )
    compact_structural_order = (
        (structural_provenance,)
        if profile.get("compact_projection_candidate")
        else ()
    )
    return (
        -_coverage_profile_anchor_priority(profile),
        0 if profile.get("strong_symbol") else 1,
        *compact_structural_order,
        0 if profile.get("direct_query", True) else 1,
        0 if _coverage_profile_history_corroborated(profile) else 1,
        0 if lane and lane not in selected_lanes else 1,
        connection_priority if explicit_test_connection else 3,
        -_coverage_term_breadth_tier(new_terms),
        0 if ranked_structural_pairs else 1,
        -_coverage_term_breadth_tier(ranked_structural_terms),
        -_coverage_pair_weight(ranked_structural_pairs, frequencies),
        0 if new_components else 1,
        -_coverage_term_breadth_tier(_coverage_profile_terms(profile)),
        connection_priority if not explicit_test_connection else 3,
        -_coverage_pair_weight(new_pairs, frequencies),
        -_coverage_pair_weight(pairs, frequencies),
        0 if new_roles else 1,
        int(profile.get("role_priority") or 0),
        -int(profile.get("field_count") or 0),
        -float(profile.get("query_coverage") or 0.0),
        -float(profile.get("path_name_coverage") or 0.0),
        -float(profile.get("path_area_coverage") or 0.0),
        -float(profile.get("path_scope_coverage") or 0.0),
        0 if _coverage_profile_direct_test_count(profile) > 0 else 1,
        -_coverage_profile_candidate_neighbor_count(profile),
        0 if _coverage_profile_graph_supported(profile) else 1,
        -float(profile.get("score") or getattr(profile.get("primary"), "score", 0.0)),
        -_coverage_profile_direct_test_count(profile),
        -_coverage_profile_support_count(profile),
        -affinity,
        str(profile.get("path") or ""),
    )


def _eligible_coverage_profiles(profiles: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return list(profiles)


def _coverage_profiles_use_weak_single_term_fallback(profiles: list[dict[str, Any]]) -> bool:
    selectable = [profile for profile in profiles if profile.get("selection_available", True)]
    terms = {term for profile in selectable for term in _coverage_profile_terms(profile)}
    return bool(selectable) and len(terms) <= 1 and all(
        not profile.get("strong_symbol")
        and not _coverage_profile_graph_supported(profile)
        and _coverage_profile_term_count(profile) <= 1
        and _coverage_profile_anchor_priority(profile)
        < CONTEXT_ANCHOR_STRENGTH_PRIORITY[ContextAnchorStrength.EXACT]
        for profile in selectable
    )


def _coverage_profile_contributes(
    profile: dict[str, Any],
    *,
    selected: list[dict[str, Any]],
) -> bool:
    if not selected:
        return True
    return _coverage_profile_contribution(profile, selected=selected).contributes


def _coverage_profile_direct_rank_key(
    profile: dict[str, Any],
) -> tuple[Any, ...]:
    """Rank source evidence from typed identity and coverage, never as authority."""
    structural_terms = {
        term
        for field_name, term in _coverage_profile_structural_pairs(profile)
        if field_name in {"path", "section"}
    }
    query_plausibility_priority = (
        0
        if len(structural_terms) >= 2
        else 1
        if _coverage_term_breadth_tier(_coverage_profile_terms(profile)) >= 2
        else 2
    )
    history_priority = 0 if _coverage_profile_history_corroborated(profile) else 1
    item = profile.get("item") if isinstance(profile.get("item"), dict) else {}
    breakdown = item.get("score_breakdown") if isinstance(item.get("score_breakdown"), dict) else {}
    return (
        _coverage_profile_identity_tier(profile),
        query_plausibility_priority,
        history_priority,
        -_coverage_profile_anchor_priority(profile),
        -float(breakdown.get("exact") or 0.0),
        -float(profile.get("path_name_coverage") or 0.0),
        -int(profile.get("field_count") or 0),
        -float(profile.get("score") or getattr(profile.get("primary"), "score", 0.0)),
        -float(breakdown.get("fts") or 0.0),
        str(profile.get("path") or ""),
    )


def _coverage_profile_prior_outcome_rank_key(
    profile: dict[str, Any],
    *,
    prior_outcome_paths: set[str],
) -> tuple[Any, ...]:
    """Break otherwise identical current-evidence ranks with positive history."""
    direct_key = _coverage_profile_direct_rank_key(profile)
    return (
        *direct_key[:-1],
        0
        if (
            _coverage_profile_path_identity(profile) in prior_outcome_paths
            and not _coverage_profile_has_explicit_identity(profile)
            and not _coverage_profile_graph_supported(profile)
        )
        else 1,
        direct_key[-1],
    )


def _coverage_profile_history_corroborated(profile: dict[str, Any]) -> bool:
    return bool(profile.get("history_corroborated"))


def _coverage_profile_related_record_ids(profile: dict[str, Any]) -> set[str]:
    record_ids = profile.get("related_record_ids")
    if not isinstance(record_ids, (set, list, tuple)):
        return set()
    return {str(record_id) for record_id in record_ids if str(record_id)}


def _coverage_profile_shares_history(
    profile: dict[str, Any],
    *,
    reference_profiles: list[dict[str, Any]],
) -> bool:
    if not _coverage_profile_history_corroborated(profile):
        return False
    record_ids = _coverage_profile_related_record_ids(profile)
    return bool(
        record_ids
        and record_ids
        & {
            record_id
            for reference in reference_profiles
            if _coverage_profile_history_corroborated(reference)
            for record_id in _coverage_profile_related_record_ids(reference)
        }
    )


def _coverage_profile_provider_evidence_supported(profile: dict[str, Any]) -> bool:
    evidence = profile.get("provider_evidence")
    if isinstance(evidence, ContextCandidate):
        return provider_symbol_evidence_supported(evidence)
    if not profile.get("provider_symbol"):
        return False
    try:
        strength = ContextAnchorStrength(
            str(profile.get("anchor_strength") or ContextAnchorStrength.NONE.value)
        )
    except ValueError:
        strength = ContextAnchorStrength.NONE
    matches = profile.get("query_term_matches")
    return bool(
        isinstance(matches, dict)
        and provider_symbol_query_supported(
            anchor_strength=strength,
            query_term_matches={
                str(field_name): tuple(str(term) for term in terms if str(term))
                for field_name, terms in matches.items()
                if isinstance(terms, (list, tuple, set))
            },
        )
    )


def _coverage_profile_evidence_kinds(profile: dict[str, Any]) -> set[str]:
    item = profile.get("item") if isinstance(profile.get("item"), dict) else {}
    values = {
        str(value)
        for value in item.get("evidence_kinds", [])
        if str(value)
    }
    primary = profile.get("primary")
    if isinstance(primary, ContextCandidate):
        values.update(kind.value for kind in primary.evidence_kinds)
    candidates = profile.get("candidates")
    if isinstance(candidates, (list, tuple)):
        values.update(
            kind.value
            for candidate in candidates
            if isinstance(candidate, ContextCandidate)
            for kind in candidate.evidence_kinds
        )
    return values


def _coverage_profile_identity_tier(profile: dict[str, Any]) -> int:
    """Return a closed lexicographic identity tier for all evidence lanes."""

    if _coverage_profile_has_explicit_identity(profile):
        return 0
    evidence_kinds = _coverage_profile_evidence_kinds(profile)
    named_test_identity = bool(
        str(profile.get("lane_key") or "")
        == ContextRetrievalLane.PRODUCT_TEST.value
        and evidence_kinds
        & {kind.value for kind in _NAMED_GRAPH_ANCHOR_KINDS}
    )
    if (
        evidence_kinds
        & {kind.value for kind in _EXPLICIT_NAMED_GRAPH_ANCHOR_KINDS}
        or named_test_identity
    ):
        return 1
    return 2


def _coverage_profile_has_query_identity(profile: dict[str, Any]) -> bool:
    return _coverage_profile_identity_tier(profile) < 2


def _coverage_profile_evidence_supported(profile: dict[str, Any]) -> bool:
    if (
        profile.get("provider_symbol")
        and _coverage_profile_anchor_priority(profile)
        >= CONTEXT_ANCHOR_STRENGTH_PRIORITY[ContextAnchorStrength.STRONG]
    ):
        return True
    if not (
        _coverage_profile_graph_supported(profile)
        and _coverage_profile_candidate_neighbor_count(profile) > 0
    ):
        return False
    return bool(
        _coverage_profile_provider_evidence_supported(profile)
        or _coverage_term_breadth_tier(_coverage_profile_terms(profile)) >= 3
        or (
            _coverage_profile_structural_pairs(profile)
            and _coverage_term_breadth_tier(_coverage_profile_terms(profile)) >= 2
        )
    )


def _coverage_profile_is_direct_evidence(profile: dict[str, Any]) -> bool:
    if not profile.get("direct_query", True):
        return False
    if str(profile.get("lane_key") or "") not in {
        ContextRetrievalLane.PRODUCT_SOURCE.value,
        "product_config",
    }:
        return False

    item = profile.get("item")
    if isinstance(item, dict):
        return bool(
            _coverage_profile_has_explicit_identity(profile)
            or (
                _coverage_profile_has_preselection_evidence_connection(profile)
                and _coverage_profile_query_evidence_supported(profile)
            )
            or (
                _coverage_profile_anchor_priority(profile)
                >= CONTEXT_ANCHOR_STRENGTH_PRIORITY[ContextAnchorStrength.STRONG]
                and _coverage_profile_provider_evidence_supported(profile)
            )
        )

    return _coverage_profile_evidence_supported(profile)


def _coverage_profile_is_direct_source(profile: dict[str, Any]) -> bool:
    return bool(
        profile.get("direct_query", True)
        and str(profile.get("lane_key") or "")
        in {ContextRetrievalLane.PRODUCT_SOURCE.value, "product_config"}
    )


def _coverage_profile_reference_key(
    profile: dict[str, Any],
    *,
    reference_profiles: list[dict[str, Any]],
    frequencies: dict[tuple[str, str], int],
) -> tuple[Any, ...]:
    reference_pairs = {
        pair for reference in reference_profiles for pair in _coverage_profile_pairs(reference)
    }
    reference_terms = {term for _field_name, term in reference_pairs}
    shared_pairs = _coverage_profile_pairs(profile) & reference_pairs
    shared_terms = _coverage_profile_terms(profile) & reference_terms
    connected = _coverage_profile_connected_to_selected(
        profile,
        selected=reference_profiles,
    )
    connection_priority = (
        _coverage_profile_initial_connection_priority(profile) if connected else 3
    )
    direct_call_connected = _coverage_profile_direct_call_connected_to_selected(
        profile,
        selected=reference_profiles,
    )
    exact_call_connected = _coverage_profile_exact_call_connected_to_selected(
        profile,
        selected=reference_profiles,
    )
    return (
        _coverage_profile_identity_tier(profile),
        0 if exact_call_connected else 1,
        0 if direct_call_connected else 1,
        -_coverage_profile_anchor_priority(profile),
        0 if profile.get("strong_symbol") else 1,
        connection_priority,
        0 if profile.get("direct_query", True) else 1,
        0
        if _coverage_profile_shares_history(
            profile,
            reference_profiles=reference_profiles,
        )
        else 1,
        -_coverage_term_breadth_tier(shared_terms),
        -_coverage_pair_weight(shared_pairs, frequencies),
        _coverage_profile_initial_key(profile, frequencies=frequencies),
    )


def _coverage_profile_structural_gap_key(
    profile: dict[str, Any],
    *,
    selected: list[dict[str, Any]],
    frequencies: dict[tuple[str, str], int],
) -> tuple[Any, ...]:
    covered = {
        pair
        for item in selected
        for pair in _coverage_profile_structural_pairs(item)
    }
    new_pairs = _coverage_profile_structural_pairs(profile) - covered
    return (
        -_coverage_term_breadth_tier(
            {term for _field_name, term in new_pairs}
        ),
        -_coverage_pair_weight(new_pairs, frequencies),
        _coverage_profile_related_key(
            profile,
            selected=selected,
            frequencies=frequencies,
        ),
    )


def _select_coverage_profiles(
    profiles: list[dict[str, Any]],
    *,
    limit: int,
    reference_profiles: list[dict[str, Any]] | None = None,
) -> list[dict[str, Any]]:
    if not profiles or limit <= 0:
        return []
    selectable = [profile for profile in profiles if profile.get("selection_available", True)]
    if not selectable:
        return []
    if _coverage_profiles_use_weak_single_term_fallback(profiles):
        limit = min(limit, 1)
    frequencies = _coverage_pair_frequencies(profiles)
    references = list(reference_profiles or [])
    compact_working_set = any(
        isinstance(profile.get("item"), dict)
        for profile in selectable
    )
    ranked_direct_evidence = sorted(
        (profile for profile in selectable if _coverage_profile_is_direct_evidence(profile)),
        key=_coverage_profile_direct_rank_key,
    )
    compact_direct_matches = [
        profile for profile in selectable if _coverage_profile_is_direct_source(profile)
    ]
    if compact_working_set and not references and compact_direct_matches:
        selected = [
            min(
                compact_direct_matches,
                key=_coverage_profile_direct_rank_key,
            )
        ]
    else:
        reserved_count = min(len(ranked_direct_evidence), 1)
        selected = ranked_direct_evidence[:reserved_count]
    remaining = [profile for profile in selectable if profile not in selected]
    if not selected and remaining:
        if references:
            primary_references = references[:1]
            primary_connected = [
                profile
                for profile in remaining
                if _coverage_profile_connected_to_selected(
                    profile,
                    selected=primary_references,
                )
            ]
            connected = [
                profile
                for profile in remaining
                if _coverage_profile_connected_to_selected(
                    profile,
                    selected=references,
                )
            ]
            first_candidates = primary_connected or connected or remaining
            first_references = (
                primary_references
                if primary_connected
                else references
            )
            first = min(
                first_candidates,
                key=lambda profile: _coverage_profile_reference_key(
                    profile,
                    reference_profiles=first_references,
                    frequencies=frequencies,
                ),
            )
        else:
            first = min(
                remaining,
                key=lambda profile: _coverage_profile_initial_key(
                    profile,
                    frequencies=frequencies,
                ),
            )
        selected.append(first)
        remaining.remove(first)
    while remaining and len(selected) < limit:
        selection_context = [*references, *selected]
        primary_pairs = (
            _coverage_profile_pairs(selected[0])
            if selected
            else set()
        )
        primary_components = (
            _coverage_profile_component_ids(selected[0])
            if selected
            else frozenset()
        )
        co_primary_candidates = [
            profile
            for profile in remaining
            if primary_pairs
            and _coverage_profile_pairs(profile) == primary_pairs
            and _coverage_profile_is_seed_or_anchor_candidate(profile)
            and _coverage_profile_is_seed_or_anchor_candidate(selected[0])
            and profile.get("parallel_anchor_coverage", True)
            and selected[0].get("parallel_anchor_coverage", True)
            and primary_components
            and _coverage_profile_component_ids(profile) == primary_components
        ]
        contributors = [
            profile
            for profile in remaining
            if _coverage_profile_contributes(profile, selected=selection_context)
        ]
        contributors.extend(
            profile
            for profile in co_primary_candidates
            if profile not in contributors
        )
        if not contributors:
            break
        contributing_direct_evidence = [
            profile
            for profile in contributors
            if _coverage_profile_is_direct_evidence(profile)
        ]
        explicit_identity_evidence = [
            profile
            for profile in contributing_direct_evidence
            if _coverage_profile_has_explicit_identity(profile)
        ]
        connected_contributors = [
            profile
            for profile in (contributing_direct_evidence or contributors)
            if _coverage_profile_connected_to_selected(
                profile,
                selected=selection_context,
            )
        ]
        evidence_connected_contributors = [
            profile
            for profile in (contributing_direct_evidence or contributors)
            if _coverage_profile_evidence_connected_to_selected(
                profile,
                selected=selection_context,
            )
        ]
        direct_source_contributors = [
            profile
            for profile in contributors
            if _coverage_profile_is_direct_source(profile)
        ]
        direct_source_count = sum(
            1 for profile in selected if _coverage_profile_is_direct_source(profile)
        )
        co_primary_direct_sources = [
            profile
            for profile in co_primary_candidates
            if profile in direct_source_contributors
        ]
        internal_evidence_closure = bool(
            not references
            and _coverage_profiles_have_internal_evidence_connection(
                selection_context
            )
        )
        connected_structural_contributors = [
            profile
            for profile in connected_contributors
            if _coverage_profile_structural_pairs(profile)
            - {
                pair
                for item in selection_context
                for pair in _coverage_profile_structural_pairs(item)
            }
        ]
        covered_terms = {
            term
            for item in selection_context
            for term in _coverage_profile_terms(item)
        }
        independent_novel_sources = [
            profile
            for profile in direct_source_contributors
            if _coverage_profile_terms(profile) - covered_terms
            and not _coverage_profile_connected_to_selected(
                profile,
                selected=selection_context,
            )
        ]
        isolated_novel_sources = [
            profile
            for profile in independent_novel_sources
            if not _coverage_profile_neighbor_paths(profile)
        ]
        use_structural_gap_key = False
        if explicit_identity_evidence:
            candidate_pool = explicit_identity_evidence
        elif internal_evidence_closure:
            if connected_structural_contributors:
                candidate_pool = connected_structural_contributors
                use_structural_gap_key = True
            elif compact_working_set and isolated_novel_sources:
                candidate_pool = isolated_novel_sources
            elif not compact_working_set and independent_novel_sources:
                candidate_pool = independent_novel_sources
            else:
                break
        elif co_primary_direct_sources:
            candidate_pool = co_primary_direct_sources
        elif evidence_connected_contributors:
            candidate_pool = evidence_connected_contributors
        elif (
            compact_working_set
            and direct_source_count < min(2, limit)
            and direct_source_contributors
        ):
            candidate_pool = direct_source_contributors
        elif connected_contributors:
            candidate_pool = connected_contributors
        else:
            candidate_pool = contributing_direct_evidence or contributors
        next_profile = min(
            candidate_pool,
            key=(
                lambda profile: _coverage_profile_structural_gap_key(
                    profile,
                    selected=selection_context,
                    frequencies=frequencies,
                )
                if use_structural_gap_key
                else _coverage_profile_related_key(
                    profile,
                    selected=selection_context,
                    frequencies=frequencies,
                )
            ),
        )
        selected.append(next_profile)
        remaining.remove(next_profile)
    return selected


def _coverage_omission_diagnostics(
    profiles: list[dict[str, Any]],
    *,
    selected_paths: set[str],
) -> dict[str, Any]:
    selected = [profile for profile in profiles if str(profile.get("path") or "") in selected_paths]
    omitted = [profile for profile in profiles if str(profile.get("path") or "") not in selected_paths]
    coverage_omissions: list[dict[str, Any]] = []
    unrepresented_pairs: set[tuple[str, str]] = set()
    unrepresented_lanes: set[str] = set()
    unrepresented_roles: set[str] = set()
    unrepresented_components: set[str] = set()
    for profile in omitted:
        if not profile.get("selection_available", True):
            continue
        contribution = _coverage_profile_contribution(profile, selected=selected)
        if not contribution.contributes:
            continue
        coverage_omissions.append(profile)
        if contribution.lexical:
            unrepresented_pairs.update(contribution.new_pairs)
        if contribution.new_lane:
            unrepresented_lanes.add(contribution.new_lane)
        unrepresented_roles.update(contribution.new_roles)
        if contribution.component:
            unrepresented_components.update(contribution.new_components)
    field_term_evidence: dict[str, list[str]] = {}
    for field_name, term in sorted(unrepresented_pairs):
        field_term_evidence.setdefault(field_name, []).append(term)
    return {
        "omitted": omitted,
        "coverage_omissions": coverage_omissions,
        "unrepresented_field_term_evidence": field_term_evidence,
        "unrepresented_lanes": sorted(unrepresented_lanes),
        "unrepresented_roles": sorted(unrepresented_roles),
        "unrepresented_components": sorted(unrepresented_components),
    }


def _anchor_selection_coverage(
    *,
    candidates: tuple[ContextGraphAnchorCandidate, ...],
    anchors: tuple[ContextGraphAnchorCandidate, ...],
    eligible_paths: set[str],
    bounded_reason: str = "",
    component_ids_by_path: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    candidate_by_path = {candidate.anchor.path: candidate for candidate in candidates}
    eligible = [candidate_by_path[path] for path in sorted(eligible_paths) if path in candidate_by_path]
    selected_paths = {anchor.anchor.path for anchor in anchors}
    profiles = [
        {
            "path": candidate.anchor.path,
            "path_identity": candidate.anchor.path,
            "component_ids": tuple(
                (component_ids_by_path or {}).get(candidate.anchor.path, ())
            ),
            "lane_key": candidate.retrieval_lane,
            "coverage_roles": {candidate.retrieval_lane} if candidate.retrieval_lane else set(),
            "query_term_matches": candidate.query_term_matches,
            "strong_symbol": candidate.anchor_provenance
            is ContextGraphAnchorProvenance.PROVIDER_SYMBOL,
            "coverage_identity_required": candidate.anchor_provenance
            in {
                ContextGraphAnchorProvenance.EXACT_IDENTITY,
                ContextGraphAnchorProvenance.REVIEWED_KNOWLEDGE,
            },
            "anchor_strength": candidate.anchor_strength.value,
            "graph_support": candidate.graph_support,
            "structured_relationship_source": candidate.source_ref.section_kind
            is ContextSectionKind.PROVIDER_RELATIONSHIP,
            "selection_available": candidate.anchor.path in eligible_paths,
        }
        for candidate in candidates
    ]
    diagnostics = _coverage_omission_diagnostics(profiles, selected_paths=selected_paths)
    omitted = diagnostics["omitted"]
    coverage_omissions = diagnostics["coverage_omissions"]
    partial = bool(coverage_omissions)
    return {
        "status": "partial" if partial else "complete",
        "reason": (
            bounded_reason
            if partial and bounded_reason
            else "anchor_budget_exhausted"
            if partial and len(anchors) >= GRAPH_ANCHOR_LIMIT
            else "anchor_unavailable"
            if partial
            else ""
        ),
        "candidate_count": len(candidates),
        "eligible_count": len(eligible),
        "selected_count": len(anchors),
        "omitted_count": len(omitted),
        "coverage_omitted_count": len(coverage_omissions),
        "eligible_paths": [candidate.anchor.path for candidate in eligible],
        "selected_paths": [anchor.anchor.path for anchor in anchors],
        "omitted_paths": [str(profile.get("path") or "") for profile in omitted],
        "coverage_omitted_paths": [
            str(profile.get("path") or "") for profile in coverage_omissions
        ],
        "unrepresented_field_term_evidence": diagnostics["unrepresented_field_term_evidence"],
        "unrepresented_lanes": diagnostics["unrepresented_lanes"],
        "unrepresented_roles": diagnostics["unrepresented_roles"],
        "unrepresented_components": diagnostics["unrepresented_components"],
    }


def _anchor_selection_coverage_after_filter(
    resolution: ContextAnchorResolution,
    anchors: tuple[ContextGraphAnchorCandidate, ...],
    *,
    eligible_paths: set[str] | None = None,
    component_ids_by_path: dict[str, tuple[str, ...]] | None = None,
) -> dict[str, Any]:
    if eligible_paths is None:
        eligible_paths = {
            str(path)
            for path in resolution.selection_coverage.get("eligible_paths", [])
            if str(path)
        }
    return _anchor_selection_coverage(
        candidates=resolution.candidates,
        anchors=anchors,
        eligible_paths=eligible_paths,
        bounded_reason=(
            str(resolution.selection_coverage.get("reason") or "")
            if anchors
            and resolution.selection_coverage.get("reason") == "weak_single_term_fallback"
            else ""
        ),
        component_ids_by_path=component_ids_by_path,
    )


def _shared_parent_prefix_depth(left: str, right: str) -> int:
    left_parts = left.replace("\\", "/").split("/")[:-1]
    right_parts = right.replace("\\", "/").split("/")[:-1]
    depth = 0
    for left_part, right_part in zip(left_parts, right_parts, strict=False):
        if left_part != right_part:
            break
        depth += 1
    return depth


def _named_file_identity(path: str) -> tuple[str, ...]:
    filename = path.replace("\\", "/").rsplit("/", 1)[-1]
    stem = filename.rsplit(".", 1)[0] if "." in filename else filename
    return canonical_identifier_sequence(stem)


def _is_direct_lexical_anchor_candidate(candidate: ContextCandidate) -> bool:
    return bool(set(candidate.evidence_kinds) & _LEXICAL_QUERY_EVIDENCE_KINDS) and (
        candidate.score - float(candidate.score_breakdown.get("graph") or 0.0)
    ) > 0


def _is_provider_symbol_candidate(candidate: ContextCandidate) -> bool:
    return bool(
        candidate.source_ref.section_kind == ContextSectionKind.PROVIDER_SYMBOL
        and candidate.source_ref.provider
        and candidate.source_ref.provider_symbol_id
    )


def _is_strong_provider_symbol_candidate(candidate: ContextCandidate) -> bool:
    return (
        _is_provider_symbol_candidate(candidate)
        and
        candidate.anchor_strength == ContextAnchorStrength.STRONG
        and ContextEvidenceKind.SECTION_TERMS in set(candidate.evidence_kinds)
    )


def _lexical_anchor_field_count(candidates: list[ContextCandidate]) -> int:
    evidence_kinds = {kind for candidate in candidates for kind in candidate.evidence_kinds}
    return sum(
        (
            ContextEvidenceKind.PATH_TERMS in evidence_kinds,
            ContextEvidenceKind.SECTION_TERMS in evidence_kinds,
            bool(evidence_kinds & {ContextEvidenceKind.BODY_TERMS, ContextEvidenceKind.FTS}),
        )
    )


def _lexical_anchor_lane_key(lane: ContextRetrievalLane, *, source_kind: str) -> str:
    if lane == ContextRetrievalLane.PRODUCT_TEST:
        return ContextRetrievalLane.PRODUCT_TEST.value
    if source_kind == "config":
        return "product_config"
    return lane.value


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
        "message": "Graph anchor identity is ambiguous; no relation expansion was performed",
    }


def _graph_source_chunk(chunks: list[Any], *, retrieved: ContextCandidate | None) -> Any | None:
    if not chunks:
        return None
    if retrieved is not None:
        for chunk in chunks:
            if chunk.source_ref.key() == retrieved.source_ref.key():
                return chunk
    module_chunks = [chunk for chunk in chunks if chunk.source_ref.section.endswith(" module")]
    return sorted(module_chunks or chunks, key=lambda item: (item.source_ref.line_start, item.source_ref.line_end, item.source_ref.section))[0]


def _section_coherent_graph_relations(
    relations: list[dict[str, Any]],
    *,
    candidate: ContextCandidate | None,
    target: RepoTarget,
    mode: str,
) -> list[dict[str, Any]]:
    if candidate is None or mode == ContextResultMode.FILE_IMPACT.value:
        return relations
    ref = candidate.source_ref
    if ref.section_kind == ContextSectionKind.PROVIDER_SYMBOL:
        if not ref.provider or not ref.provider_symbol_id:
            return []
        provider_symbol_node = symbol_id(
            target.id,
            ref.provider,
            ref.provider_symbol_id,
        )
        return [
            relation
            for relation in relations
            if relation.get("edge") == "CALLS"
            and provider_symbol_node
            in {
                str(relation.get("from_id") or ""),
                str(relation.get("to_id") or ""),
            }
        ]
    if ref.section_kind == ContextSectionKind.PROVIDER_RELATIONSHIP:
        if not ref.source_fact_id:
            return []
        return [
            relation
            for relation in relations
            if any(
                isinstance(fact, dict)
                and str(fact.get("source_fact_id") or "") == ref.source_fact_id
                for fact in (
                    relation.get("facts", {}).get("relations", [])
                    if isinstance(relation.get("facts"), dict)
                    and isinstance(relation.get("facts", {}).get("relations"), list)
                    else []
                )
            )
        ]
    return relations


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
    origins = relation.get("origin_paths") if isinstance(relation.get("origin_paths"), list) else []
    propagated = max(
        (
            anchor_scores.get(str(origin or ""), 0.0)
            / _relation_origin_distance(relation, str(origin or ""))
            for origin in origins
        ),
        default=0.0,
    )
    endpoint_score = sum(
        retrieval_by_path[path].score
        for path in {
            str(relation.get("from_path") or ""),
            str(relation.get("to_path") or ""),
        }
        if path in retrieval_by_path
    )
    return propagated + endpoint_score


def _relation_origin_distance(relation: dict[str, Any], origin_path: str) -> int:
    origin_distances = relation.get("origin_distances")
    if isinstance(origin_distances, dict):
        try:
            return max(1, int(origin_distances.get(origin_path) or relation.get("distance") or 1))
        except (TypeError, ValueError):
            pass
    return max(1, int(relation.get("distance") or 1))


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
    best: dict[tuple[str, str, str, str, int, int, str], ContextCandidate] = {}
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
    return bool(set(candidate.evidence_kinds) - GRAPH_DERIVED_CONTEXT_EVIDENCE_KINDS)


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
    if ref.kind in {"completion_receipt", "task_artifact", "prior_task_outcome"}:
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
        "query_term_matches": {
            field_name: list(terms)
            for field_name, terms in sorted(candidate.query_term_matches.items())
            if terms
        },
        "evidence_kinds": sorted(kind.value for kind in set(candidate.evidence_kinds)),
        "excerpt": candidate.text,
        "graph_path": candidate.graph_path,
        "continuations": _candidate_continuations(candidate, target=target),
        "evidence_role": roles[0],
        "evidence_roles": roles,
    }
    if candidate.related_record_ids:
        item["related_record_ids"] = sorted(set(candidate.related_record_ids))
    if candidate.document_role != DocumentRole.UNSPECIFIED:
        item["document_role"] = candidate.document_role.value
    graph_provenance = _graph_relation_provenance(candidate.graph_path)
    if graph_provenance:
        item["provenance"] = graph_provenance
    return item


def _candidate_section(candidate: ContextCandidate) -> dict[str, Any]:
    section = {"kind": candidate.source_ref.kind}
    for key, value in (
        ("section", candidate.source_ref.section),
        ("section_kind", candidate.source_ref.section_kind.value),
        ("line_start", candidate.source_ref.line_start),
        ("line_end", candidate.source_ref.line_end),
    ):
        if value not in {"", 0, ContextSectionKind.UNSPECIFIED.value}:
            section[key] = value
    return section


def _graph_relation_provenance(relations: list[dict[str, Any]]) -> dict[str, Any]:
    typed_relations = [relation for relation in relations if isinstance(relation, dict)]
    if not typed_relations:
        return {}
    origin_paths = sorted(
        {
            str(origin or "")
            for relation in typed_relations
            for origin in (
                relation.get("origin_paths")
                if isinstance(relation.get("origin_paths"), list)
                else []
            )
            if str(origin or "")
        }
    )
    provenance: dict[str, Any] = {
        "edge_kinds": sorted({str(relation.get("edge") or "") for relation in typed_relations if relation.get("edge")}),
        "providers": sorted({str(relation.get("provider") or "") for relation in typed_relations if relation.get("provider")}),
        "min_distance": min(max(1, int(relation.get("distance") or 1)) for relation in typed_relations),
    }
    if origin_paths:
        provenance["origin_paths"] = origin_paths
    return provenance


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
        for path_candidates in by_path.values()
    ]
    if group in {"likely_change_surface", "tests_and_verification"}:
        return [item for item, _direct_query_score_value in items]
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
    sections: dict[tuple[str, str, str, int, int], dict[str, Any]] = {}
    reasons: set[str] = set()
    roles: set[str] = set()
    continuations: list[dict[str, Any]] = []
    graph_paths: dict[str, dict[str, Any]] = {}
    breakdown: dict[str, float] = {}
    query_term_matches: dict[str, set[str]] = {}
    evidence_kinds: set[ContextEvidenceKind] = set()
    related_record_ids: set[str] = set()
    anchor_strength = ContextAnchorStrength.NONE
    for candidate in ranked:
        section = _candidate_section(candidate)
        section_key = (
            str(section.get("kind") or ""),
            str(section.get("section") or ""),
            str(section.get("section_kind") or ""),
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
        for field_name, terms in candidate.query_term_matches.items():
            query_term_matches.setdefault(field_name, set()).update(terms)
        evidence_kinds.update(candidate.evidence_kinds)
        related_record_ids.update(candidate.related_record_ids)
        if CONTEXT_ANCHOR_STRENGTH_PRIORITY[candidate.anchor_strength] > CONTEXT_ANCHOR_STRENGTH_PRIORITY[anchor_strength]:
            anchor_strength = candidate.anchor_strength
    ordered_roles = sorted(roles, key=lambda role: (_evidence_role_priority(role), role))
    merged_graph_paths = [graph_paths[key] for key in sorted(graph_paths)]
    item.update(
        {
            "sections": [sections[key] for key in sorted(sections)],
            "selection_reason": "; ".join(sorted(reasons)[:4]),
            "selection_reasons": sorted(reasons),
            "score": max(candidate.score for candidate in ranked),
            "score_breakdown": breakdown,
            "query_term_matches": {
                field_name: sorted(terms)
                for field_name, terms in sorted(query_term_matches.items())
                if terms
            },
            "anchor_strength": anchor_strength.value,
            "evidence_kinds": sorted(kind.value for kind in evidence_kinds),
            "graph_path": merged_graph_paths,
            "continuations": _dedupe_continuations(continuations),
            "evidence_role": ordered_roles[0],
            "evidence_roles": ordered_roles,
        }
    )
    if related_record_ids:
        item["related_record_ids"] = sorted(related_record_ids)
    graph_provenance = _graph_relation_provenance(merged_graph_paths)
    if graph_provenance:
        item["provenance"] = graph_provenance
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
        elif ref.kind == ContextSourceKind.STRUCTURED_DATA.value:
            roles.add("structured_data")
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
                origin_paths = (
                    relation.get("origin_paths")
                    if isinstance(relation.get("origin_paths"), list)
                    else []
                )
                if any(
                    str(origin or "") != repo_path
                    and not is_test_path(str(origin or ""))
                    for origin in origin_paths
                    if str(origin or "")
                ):
                    roles.add("anchor_connected_test")
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
        "knowledge_linked_source": 0,
        "knowledge_linked_test": 0,
        "anchor_connected_test": 1,
        "directly_connected_test": 1,
        "imported_dependency": 1,
        "called_dependency": 1,
        "structured_dependency": 1,
        "product_manifest": 1,
        "configuration": 1,
        "structured_data": 1,
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
    role_priority = _evidence_role_priority(role)
    try:
        anchor_strength = ContextAnchorStrength(str(item.get("anchor_strength") or ContextAnchorStrength.NONE.value))
    except ValueError:
        anchor_strength = ContextAnchorStrength.NONE
    anchor_priority = CONTEXT_ANCHOR_STRENGTH_PRIORITY[anchor_strength]
    if group == "callers_and_dependents":
        role_priority = 0 if role == "code_relation" else role_priority
    return (
        0,
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
    source_refs = record.get("resolved_source_refs") if isinstance(record.get("resolved_source_refs"), list) else []
    for ref in source_refs:
        if (
            isinstance(ref, dict)
            and str(ref.get("resolution_status") or "") in {"current", "relocated"}
            and str(ref.get("path") or "")
        ):
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

from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .context_model import ContextBundle, ContextCandidate, ContextSourceRef
from .context_pack import pack_candidates
from .context_retrieval import excerpt_for_query, retrieve_context
from .context_sources import collect_context_sources
from .graph import build_graph, query_graph
from .graph_model import digest_data
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


def build_context_bundle(
    root: Path,
    *,
    target: RepoTarget,
    query: str,
    budget_tokens: int = 1200,
    explain: bool = False,
    mode: str = "",
    graph_result: tuple[Any, list[Problem], dict[str, Any]] | None = None,
) -> tuple[ContextBundle | None, list[Problem], dict[str, Any]]:
    query_mode = normalize_context_mode(mode)
    snapshot, graph_problems, graph_meta = graph_result if graph_result is not None else build_graph(root, target=target)
    include_history = query_mode in {"past_decision", "failure_mode"}
    chunks, source_snapshots, completeness, source_problems = collect_context_sources(
        root,
        target=target,
        snapshot=snapshot,
        graph_problems=graph_problems,
        graph_meta=graph_meta,
        include_history=include_history,
    )
    problems = [*source_problems]
    retrieval_chunks = _retrieval_chunks(chunks, mode=query_mode, target=target)
    retrieved_candidates = retrieve_context(query, retrieval_chunks, limit=24)
    if query_mode == "auto":
        graph_candidates, graph_warnings = _graph_context_candidates_from_sources(
            snapshot,
            chunks=chunks,
            target=target,
            source_candidates=retrieved_candidates,
            query=query,
        )
    else:
        graph_candidates, graph_warnings = _graph_context_candidates(snapshot, chunks=chunks, target=target, query=query, mode=query_mode)
    startup_candidates = _startup_query_candidates(chunks, target=target, mode=query_mode)
    candidates = _dedupe_candidates([*startup_candidates, *graph_candidates, *retrieved_candidates])
    packed, budget = pack_candidates(candidates, budget_tokens=budget_tokens)
    knowledge_data: dict[str, Any] = {}
    if query_mode in {"authority_or_contract", "invariant", "past_decision", "failure_mode"}:
        knowledge_data, knowledge_problems, knowledge_warnings = query_knowledge_records(root, repo_id=target.id, query=query, include_stale=False, limit=10, explain=explain)
        problems.extend(knowledge_problems)
        problems.extend(knowledge_warnings)
    groups = _context_groups(
        packed,
        knowledge_results=knowledge_data.get("results", []) if isinstance(knowledge_data.get("results"), list) else [],
        repo_id=target.id,
        completeness=completeness,
        graph_warnings=graph_warnings,
    )
    bundle = ContextBundle(
        repository=target.to_dict(),
        query={"text": query, "type": "natural_language", "mode": query_mode, "explain": explain},
        source_snapshots=source_snapshots,
        completeness={
            **completeness,
            "source_count": len(chunks),
            "group_names": list(CONTEXT_GROUPS),
            "knowledge_available_record_count": int(knowledge_data.get("available_record_count") or 0),
            "knowledge_result_count": int(knowledge_data.get("result_count") or 0),
            "knowledge_lifecycle": knowledge_data.get("lifecycle", {}) if isinstance(knowledge_data.get("lifecycle"), dict) else {},
        },
        candidates=candidates,
        packed_context=packed,
        budget=budget,
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


def _retrieval_chunks(chunks: list[Any], *, mode: str, target: RepoTarget) -> list[Any]:
    if mode == "auto":
        return [chunk for chunk in chunks if chunk.source_ref.kind not in {"completion_receipt", "task_artifact"}]
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
        f"# Context Bundle",
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
            section = f" ({ref.get('section')})" if ref.get("section") else ""
            reason = item.get("selection_reason") or item.get("status") or ""
            lines.append(f"- `{label}`{section}: {reason}")
            excerpt = str(item.get("excerpt") or "").strip()
            if excerpt:
                compact = " ".join(excerpt.split())
                lines.append(f"  {compact[:240]}")
        lines.append("")
    return "\n".join(lines).rstrip() + "\n"


def compact_context_bundle(bundle: ContextBundle, *, max_group_items: int = 8, excerpt_chars: int = 240) -> dict[str, Any]:
    """Return the default agent-facing view without raw candidate/debug payloads."""
    group_limits = {
        "must_read": 5,
        "likely_change_surface": 5,
        "callers_and_dependents": 3,
        "tests_and_verification": 3,
        "reviewed_knowledge": 2,
        "supporting_evidence": 3,
        "warnings_and_completeness": 5,
    }
    groups = {
        group: [
            _compact_group_item(item, excerpt_chars=excerpt_chars)
            for item in items[: min(max_group_items, group_limits.get(group, max_group_items))]
        ]
        for group, items in sorted(bundle.groups.items())
    }
    group_counts = {group: len(items) for group, items in sorted(bundle.groups.items())}
    selected_refs = _selected_source_refs(bundle.packed_context)
    omitted = {
        "candidate_count": len(bundle.candidates),
        "packed_context_count": len(bundle.packed_context),
        "group_counts": group_counts,
    }
    return {
        "schema": bundle.schema,
        "schema_version": bundle.schema_version,
        "view": "compact",
        "authoritative": bundle.authoritative,
        "repository": bundle.repository,
        "query": bundle.query,
        "source_snapshots": dict(sorted(bundle.source_snapshots.items())),
        "completeness": bundle.completeness,
        "groups": groups,
        "selected_source_refs": selected_refs,
        "budget": {**bundle.budget, **omitted},
        "knowledge_result_count": len(bundle.knowledge_results),
        "bundle_digest": bundle.bundle_digest,
    }


def _selected_source_refs(candidates: list[ContextCandidate]) -> list[dict[str, Any]]:
    refs: list[dict[str, Any]] = []
    seen: set[tuple[str, str, str, int, int]] = set()
    for candidate in sorted(candidates, key=lambda item: (-item.score, item.source_ref.path, item.source_ref.section, item.source_ref.line_start)):
        ref = candidate.source_ref
        key = ref.key()
        if key in seen:
            continue
        seen.add(key)
        refs.append({"source_ref": ref.to_dict()})
    return refs


def _compact_group_item(item: dict[str, Any], *, excerpt_chars: int) -> dict[str, Any]:
    compact: dict[str, Any] = {}
    for key in ("repo_id", "status", "record_id", "code", "selection_reason"):
        if item.get(key):
            compact[key] = item[key]
    ref = item.get("source_ref")
    if isinstance(ref, dict):
        compact["source_ref"] = ref
    score_breakdown = item.get("score_breakdown")
    if isinstance(score_breakdown, dict) and score_breakdown:
        compact["score_breakdown"] = score_breakdown
    excerpt = item.get("excerpt")
    if excerpt:
        compact["excerpt"] = _truncate(str(excerpt), excerpt_chars)
    graph_path = item.get("graph_path")
    if isinstance(graph_path, list) and graph_path:
        compact["graph_path_count"] = len(graph_path)
    return compact


def _truncate(value: str, limit: int) -> str:
    compact = " ".join(value.strip().split())
    if len(compact) <= limit:
        return compact
    return compact[: max(0, limit - 3)].rstrip() + "..."


def _query_tokens(query: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_./:-]*|[./][A-Za-z0-9_./:-]+", query)


def _startup_query_candidates(chunks: list[Any], *, target: RepoTarget, mode: str) -> list[ContextCandidate]:
    if mode != "startup_reading":
        return []
    wanted = _startup_source_priority(target)
    selected: list[ContextCandidate] = []
    seen_paths: set[str] = set()
    for chunk in chunks:
        path = chunk.source_ref.path
        score = wanted.get(path)
        if score is None or path in seen_paths:
            continue
        seen_paths.add(path)
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
    query: str,
    mode: str,
) -> tuple[list[ContextCandidate], list[dict[str, str]]]:
    if snapshot is None:
        return [], [{"code": "context_graph_unavailable", "message": "Graph snapshot was not available for context query"}]
    warnings: list[dict[str, str]] = []
    candidates: list[ContextCandidate] = []
    seen_queries: set[tuple[str, str]] = set()
    product_prefix = f"{target.display_path.rstrip('/')}/"
    source_chunks = {
        chunk.source_ref.path.removeprefix(product_prefix): chunk
        for chunk in chunks
        if chunk.source_ref.kind == "current_source" and chunk.source_ref.path.startswith(product_prefix)
    }
    for token in _query_tokens(query):
        token = token.strip("`'\".,()[]{}")
        if len(token) < 2:
            continue
        graph_results: list[dict[str, Any]] = []
        if "/" in token or token.endswith((".py", ".ts", ".tsx", ".js", ".jsx", ".dart", ".cs")):
            if mode in {"file_impact", "call_impact"}:
                key = ("impact_file", token)
                if key not in seen_queries:
                    seen_queries.add(key)
                    result, problems = query_graph(snapshot, impact_file=token, depth=2)
                    graph_results.extend(_usable_graph_results(result, problems, warnings))
            key = ("file", token)
            if key not in seen_queries:
                seen_queries.add(key)
                result, problems = query_graph(snapshot, file=token)
                graph_results.extend(_usable_graph_results(result, problems, warnings))
        elif mode in {"code_location", "call_impact", "file_impact"}:
            if mode == "code_location":
                key = ("symbol", token)
                if key not in seen_queries:
                    seen_queries.add(key)
                    result, problems = query_graph(snapshot, symbol=token)
                    graph_results.extend(_usable_graph_results(result, problems, warnings))
            if mode in {"call_impact", "file_impact"}:
                for selector in ("callers_of", "impact_symbol"):
                    key = (selector, token)
                    if key in seen_queries:
                        continue
                    seen_queries.add(key)
                    kwargs = {"callers_of": token} if selector == "callers_of" else {"impact_symbol": token, "depth": 2}
                    result, problems = query_graph(snapshot, **kwargs)
                    graph_results.extend(_usable_graph_results(result, problems, warnings))
        for result in graph_results:
            candidates.extend(_graph_result_source_candidates(result, source_chunks=source_chunks, query=query))
            graph_candidate = _graph_candidate(result, repo_id=str(snapshot.repository.get("id") or ""), mode=mode)
            if graph_candidate.graph_path:
                candidates.append(graph_candidate)
    return _dedupe_candidates(candidates), warnings


def _graph_context_candidates_from_sources(
    snapshot: Any,
    *,
    chunks: list[Any],
    target: RepoTarget,
    source_candidates: list[ContextCandidate],
    query: str,
) -> tuple[list[ContextCandidate], list[dict[str, str]]]:
    if snapshot is None:
        return [], [{"code": "context_graph_unavailable", "message": "Graph snapshot was not available for context query"}]
    if not source_candidates:
        return [], []
    product_prefix = f"{target.display_path.rstrip('/')}/"
    source_chunks = {
        chunk.source_ref.path.removeprefix(product_prefix): chunk
        for chunk in chunks
        if chunk.source_ref.kind == "current_source" and chunk.source_ref.path.startswith(product_prefix)
    }
    seed_paths: list[str] = []
    for candidate in source_candidates[:8]:
        path = candidate.source_ref.path
        if candidate.source_ref.kind != "current_source" or not path.startswith(product_prefix):
            continue
        repo_path = path.removeprefix(product_prefix)
        if repo_path not in seed_paths:
            seed_paths.append(repo_path)
        if len(seed_paths) == 3:
            break
    if not seed_paths:
        return [], []

    candidates: list[ContextCandidate] = []
    warnings: list[dict[str, str]] = []
    for path in seed_paths:
        result, result_problems = query_graph(snapshot, impact_file=path, depth=2)
        usable = _usable_graph_results(result, result_problems, warnings)
        for graph_result in usable:
            candidates.extend(_graph_result_source_candidates(graph_result, source_chunks=source_chunks, query=query))
            graph_candidate = _graph_candidate(graph_result, repo_id=str(snapshot.repository.get("id") or ""), mode="auto")
            if graph_candidate.graph_path:
                candidates.append(graph_candidate)
    return _dedupe_candidates(candidates), warnings


def _usable_graph_results(result: dict[str, Any] | None, problems: list[Problem], warnings: list[dict[str, str]]) -> list[dict[str, Any]]:
    for problem in problems:
        if problem.code == "graph_query_ambiguous_symbol" and result is not None:
            warnings.append({"code": problem.code, "message": problem.message})
            return [result]
        if problem.severity == "error":
            continue
        warnings.append({"code": problem.code, "message": problem.message})
    if result is None or str(result.get("query_status") or "") != "found":
        return []
    if not result.get("matches") and not result.get("paths"):
        return []
    return [result]


def _graph_result_source_candidates(result: dict[str, Any], *, source_chunks: dict[str, Any], query: str) -> list[ContextCandidate]:
    matched_paths = {
        str(match.get("path") or "")
        for match in result.get("matches", [])
        if isinstance(match, dict) and str(match.get("path") or "")
    }
    related_paths: set[str] = set()
    for node in result.get("nodes", []):
        if not isinstance(node, dict) or str(node.get("kind") or "") != "file":
            continue
        identity = node.get("identity") if isinstance(node.get("identity"), dict) else {}
        path = str(identity.get("path") or "")
        if path:
            related_paths.add(path)

    candidates: list[ContextCandidate] = []
    for path in sorted(matched_paths | related_paths):
        chunk = source_chunks.get(path)
        if chunk is None:
            continue
        matched = path in matched_paths
        candidates.append(
            ContextCandidate(
                source_ref=chunk.source_ref,
                text=excerpt_for_query(chunk.text, query, limit=700),
                score=6.0 if matched else 5.0,
                score_breakdown={"exact": 0.0, "fts": 0.0, "authority": 0.0, "graph": 1.0},
                selection_reasons=["Graph matched source file" if matched else "Graph related source file"],
            )
        )
    return candidates


def _graph_candidate(result: dict[str, Any], *, repo_id: str, mode: str) -> ContextCandidate:
    query = result.get("query") if isinstance(result.get("query"), dict) else {}
    paths = result.get("paths") if isinstance(result.get("paths"), list) else []
    matches = result.get("matches") if isinstance(result.get("matches"), list) else []
    lines: list[str] = []
    for match in matches[:5]:
        label = match.get("qualified_name") or match.get("path") or match.get("raw_import") or match.get("id")
        location = match.get("path") or ""
        lines.append(f"match {label} {location}".rstrip())
    relationship_paths = [path for path in paths if str(path.get("edge") or "") not in {"DEFINES", "ANCHORS"}]
    display_paths = relationship_paths or paths
    for path in display_paths[:10]:
        from_node = path.get("from", {}) if isinstance(path.get("from"), dict) else {}
        to_node = path.get("to", {}) if isinstance(path.get("to"), dict) else {}
        from_label = from_node.get("qualified_name") or from_node.get("path") or from_node.get("id")
        to_label = to_node.get("qualified_name") or to_node.get("path") or to_node.get("id")
        lines.append(f"{from_label} --{path.get('edge')}--> {to_label}: {path.get('reason')}")
    text = "\n".join(lines) if lines else f"Graph query matched {len(matches)} node(s)."
    digest = digest_data(result)
    query_type = str(query.get("type") or "graph")
    return ContextCandidate(
        source_ref=ContextSourceRef(kind="graph_query", path=f"<graph-query:{query_type}:{digest[7:19]}>", section=query_type, content_sha256=digest),
        text=text,
        score=4.0 if paths else 3.0,
        score_breakdown={"exact": 0.0, "fts": 0.0, "authority": 0.0, "graph": 1.0},
        selection_reasons=[f"Graph {query_type} evidence for {mode}"],
        graph_path=paths,
    )


def _dedupe_candidates(candidates: list[ContextCandidate]) -> list[ContextCandidate]:
    best: dict[tuple[str, str, str, int, int], ContextCandidate] = {}
    for candidate in candidates:
        key = candidate.source_ref.key()
        previous = best.get(key)
        if previous is None or candidate.score > previous.score:
            best[key] = candidate
    return sorted(best.values(), key=lambda item: (-item.score, item.source_ref.path, item.source_ref.line_start))


def _context_groups(
    packed: list[ContextCandidate],
    *,
    knowledge_results: list[dict[str, Any]],
    repo_id: str,
    completeness: dict[str, Any],
    graph_warnings: list[dict[str, str]],
) -> dict[str, list[dict[str, Any]]]:
    groups: dict[str, list[dict[str, Any]]] = {group: [] for group in CONTEXT_GROUPS}
    for candidate in packed:
        group = _candidate_group(candidate)
        groups[group].append(_candidate_group_item(candidate, repo_id=repo_id, status="current"))
    for result in knowledge_results:
        record = result.get("record") if isinstance(result.get("record"), dict) else {}
        groups["reviewed_knowledge"].append(
            {
                "repo_id": repo_id,
                "record_id": record.get("id", ""),
                "status": result.get("status") or record.get("status") or "reviewed",
                "selection_reason": "reviewed knowledge match",
                "score_breakdown": result.get("score_breakdown", {}),
                "excerpt": record.get("claim") or record.get("summary") or "",
                "source_ref": {"kind": "knowledge_record", "path": f"docs/knowledge/records/{record.get('id', '')}.json", "content_sha256": record.get("record_sha256", "")},
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
    precise_provider = graph_meta.get("precise_provider") if isinstance(graph_meta.get("precise_provider"), dict) else {}
    provider = str(precise_provider.get("provider") or "")
    if provider:
        languages = precise_provider.get("languages") if isinstance(precise_provider.get("languages"), list) else []
        groups["warnings_and_completeness"].append(
            {
                "repo_id": repo_id,
                "status": "warning",
                "code": "context_graph_capability",
                "selection_reason": f"Graph precise provider is {provider} for languages={languages}; other languages are inventory/index evidence unless a resolver reports otherwise",
            }
        )
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
    text = candidate.text.lower()
    if candidate.score_breakdown.get("startup_reading"):
        return "must_read"
    if ref.kind == "current_source":
        return "tests_and_verification" if _looks_like_test_ref(path) else "likely_change_surface"
    if ref.kind == "graph_query":
        return "callers_and_dependents" if candidate.graph_path else "supporting_evidence"
    if _is_low_value_generated_or_unsupported(candidate):
        return "supporting_evidence"
    if path.startswith("docs/adr/") or path.startswith("docs/contracts/") or path in {"agents.md", "docs/prd.md"} or section in {"decision", "authority rules", "future layer rules"}:
        return "must_read"
    if ref.kind in {"completion_receipt", "verification_hint"} or "verification" in text or "test" in path or "test" in text:
        return "tests_and_verification"
    return "supporting_evidence"


def _candidate_group_item(candidate: ContextCandidate, *, repo_id: str, status: str) -> dict[str, Any]:
    return {
        "repo_id": repo_id,
        "status": status,
        "source_ref": candidate.source_ref.to_dict(),
        "content_sha256": candidate.source_ref.content_sha256,
        "selection_reason": "; ".join(candidate.selection_reasons) or "retrieval match",
        "score_breakdown": candidate.score_breakdown,
        "excerpt": candidate.text,
        "graph_path": candidate.graph_path,
    }


def _is_low_value_generated_or_unsupported(candidate: ContextCandidate) -> bool:
    path = candidate.source_ref.path.lower()
    text = candidate.text.lower()
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
        "/egg-info/",
        "/library/",
        "/logs/",
        "/node_modules/",
        "/obj/",
        "/target/",
        "/temp/",
        "/usersettings/",
    )
    noisy_suffixes = (".pkl", ".pyc", ".tsbuildinfo", ".lock", ".log", "package-lock.json", "pnpm-lock.yaml", "yarn.lock", "bun.lockb")
    return (
        any(part in path for part in noisy_parts)
        or path.endswith(noisy_suffixes)
        or "parse_status" in text
        and any(marker in text for marker in ('"skipped"', "unsupported", "non-utf8"))
    )


def _looks_like_test_ref(path: str) -> bool:
    name = path.rsplit("/", 1)[-1]
    return (
        "/tests/" in path
        or "/test/" in path
        or path.startswith("tests/")
        or path.startswith("test/")
        or name.startswith("test_")
        or name.endswith(("_test.py", ".test.js", ".test.ts", "_test.dart"))
    )

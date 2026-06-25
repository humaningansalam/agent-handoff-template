from __future__ import annotations

from pathlib import Path
import re
from typing import Any

from .context_model import ContextBundle, ContextCandidate, ContextSourceRef
from .context_pack import pack_candidates
from .context_retrieval import retrieve_context
from .context_sources import collect_context_sources
from .graph import build_graph, query_graph
from .graph_model import digest_data
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


def build_context_bundle(
    root: Path,
    *,
    target: RepoTarget,
    query: str,
    budget_tokens: int = 3000,
    explain: bool = False,
    mode: str = "",
) -> tuple[ContextBundle | None, list[Problem], dict[str, Any]]:
    snapshot, graph_problems, graph_meta = build_graph(root, target=target)
    chunks, source_snapshots, completeness, source_problems = collect_context_sources(root, target=target, snapshot=snapshot, graph_problems=graph_problems, graph_meta=graph_meta)
    problems = [*source_problems]
    query_mode = classify_context_mode(query, explicit_mode=mode)
    graph_candidates, graph_warnings = _graph_context_candidates(snapshot, query=query, mode=query_mode)
    candidates = retrieve_context(query, chunks, snapshot=snapshot, limit=40)
    candidates = _dedupe_candidates([*graph_candidates, *candidates])
    packed, budget = pack_candidates(candidates, budget_tokens=budget_tokens)
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


def classify_context_mode(query: str, *, explicit_mode: str = "") -> str:
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
    if normalized:
        return normalized
    lowered = query.lower()
    if any(term in lowered for term in ("failure", "failed", "장애", "실패")):
        return "failure_mode"
    if any(term in lowered for term in ("invariant", "must", "contract", "깨뜨", "계약")):
        return "invariant"
    if any(term in lowered for term in ("decision", "decided", "why", "왜", "adr")):
        if any(term in lowered for term in ("authority", "authoritative", "source", "권위", "generated wiki", "llmwiki")):
            return "authority_or_contract"
        return "past_decision"
    if any(term in lowered for term in ("call", "caller", "callee", "호출")):
        return "call_impact"
    if any(term in lowered for term in ("impact", "impacted", "change", "변경", "영향")):
        return "file_impact" if any("/" in token or "." in token for token in _query_tokens(query)) else "call_impact"
    if any(term in lowered for term in ("where", "defined", "symbol", "어디")):
        return "code_location"
    return "authority_or_contract"


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


def _query_tokens(query: str) -> list[str]:
    return re.findall(r"[A-Za-z_][A-Za-z0-9_./:-]*|[./][A-Za-z0-9_./:-]+", query)


def _graph_context_candidates(snapshot: Any, *, query: str, mode: str) -> tuple[list[ContextCandidate], list[dict[str, str]]]:
    if snapshot is None:
        return [], [{"code": "context_graph_unavailable", "message": "Graph snapshot was not available for context query"}]
    warnings: list[dict[str, str]] = []
    candidates: list[ContextCandidate] = []
    seen_queries: set[tuple[str, str]] = set()
    for token in _query_tokens(query):
        token = token.strip("`'\".,()[]{}")
        if len(token) < 2:
            continue
        graph_results: list[dict[str, Any]] = []
        if "/" in token or token.endswith((".py", ".ts", ".tsx", ".js", ".jsx")):
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
        else:
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
            candidates.append(_graph_candidate(result, repo_id=str(snapshot.repository.get("id") or ""), mode=mode))
    return _dedupe_candidates(candidates), warnings


def _usable_graph_results(result: dict[str, Any] | None, problems: list[Problem], warnings: list[dict[str, str]]) -> list[dict[str, Any]]:
    for problem in problems:
        if problem.code == "graph_query_ambiguous_symbol" and result is not None:
            warnings.append({"code": problem.code, "message": problem.message})
            return [result]
        if problem.severity == "error":
            continue
        warnings.append({"code": problem.code, "message": problem.message})
    return [result] if result is not None else []


def _graph_candidate(result: dict[str, Any], *, repo_id: str, mode: str) -> ContextCandidate:
    query = result.get("query") if isinstance(result.get("query"), dict) else {}
    paths = result.get("paths") if isinstance(result.get("paths"), list) else []
    matches = result.get("matches") if isinstance(result.get("matches"), list) else []
    lines: list[str] = []
    for match in matches[:5]:
        label = match.get("qualified_name") or match.get("path") or match.get("raw_import") or match.get("id")
        location = match.get("path") or ""
        lines.append(f"match {label} {location}".rstrip())
    for path in paths[:10]:
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
    assigned: set[tuple[str, str, str, int, int]] = set()
    for candidate in packed:
        group = _candidate_group(candidate)
        groups[group].append(_candidate_group_item(candidate, repo_id=repo_id, status="current"))
        assigned.add(candidate.source_ref.key())
    for candidate in packed:
        if candidate.source_ref.key() not in assigned:
            groups["supporting_evidence"].append(_candidate_group_item(candidate, repo_id=repo_id, status="current"))
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
    return groups


def _candidate_group(candidate: ContextCandidate) -> str:
    ref = candidate.source_ref
    path = ref.path.lower()
    section = ref.section.lower()
    text = candidate.text.lower()
    if ref.kind == "graph_query" and candidate.graph_path:
        return "callers_and_dependents"
    if ref.kind == "graph_node" or ref.kind == "graph_query":
        return "likely_change_surface"
    if path.startswith("docs/adr/") or path.startswith("docs/contracts/") or path == "agents.md" or section in {"decision", "authority rules", "future layer rules"}:
        return "must_read"
    if ref.kind == "completion_receipt" or "verification" in text or "test" in path or "test" in text:
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

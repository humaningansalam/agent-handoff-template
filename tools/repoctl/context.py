from __future__ import annotations

from pathlib import Path
from typing import Any

from .context_model import ContextBundle
from .context_pack import pack_candidates
from .context_retrieval import retrieve_context
from .context_sources import collect_context_sources
from .graph import build_graph
from .knowledge_candidates import query_knowledge_records
from .repositories import RepoTarget
from .tasks import Problem


def build_context_bundle(root: Path, *, target: RepoTarget, query: str, budget_tokens: int = 3000, explain: bool = False) -> tuple[ContextBundle | None, list[Problem], dict[str, Any]]:
    snapshot, graph_problems, graph_meta = build_graph(root, target=target)
    chunks, source_snapshots, completeness, source_problems = collect_context_sources(root, target=target, snapshot=snapshot, graph_problems=graph_problems, graph_meta=graph_meta)
    problems = [*source_problems]
    candidates = retrieve_context(query, chunks, snapshot=snapshot, limit=40)
    packed, budget = pack_candidates(candidates, budget_tokens=budget_tokens)
    knowledge_data, knowledge_problems, knowledge_warnings = query_knowledge_records(root, repo_id=target.id, query=query, include_stale=False, limit=10, explain=explain)
    problems.extend(knowledge_problems)
    problems.extend(knowledge_warnings)
    bundle = ContextBundle(
        repository=target.to_dict(),
        query={"text": query, "type": "natural_language", "explain": explain},
        source_snapshots=source_snapshots,
        completeness={
            **completeness,
            "source_count": len(chunks),
            "knowledge_available_record_count": int(knowledge_data.get("available_record_count") or 0),
            "knowledge_result_count": int(knowledge_data.get("result_count") or 0),
        },
        candidates=candidates,
        packed_context=packed,
        budget=budget,
        knowledge_results=knowledge_data.get("results", []) if isinstance(knowledge_data.get("results"), list) else [],
    ).with_digest()
    meta = {"repository": target.to_dict(), "graph": graph_meta}
    return bundle, problems, meta

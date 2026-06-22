from __future__ import annotations

from pathlib import Path
from typing import Any

from .context_model import ContextBundle
from .context_pack import pack_candidates
from .context_retrieval import retrieve_context
from .context_sources import collect_context_sources
from .graph import build_graph
from .repositories import RepoTarget
from .tasks import Problem


def build_context_bundle(root: Path, *, target: RepoTarget, query: str, budget_tokens: int = 3000) -> tuple[ContextBundle | None, list[Problem], dict[str, Any]]:
    snapshot, graph_problems, graph_meta = build_graph(root, target=target)
    chunks, source_snapshots, completeness, source_problems = collect_context_sources(root, target=target, snapshot=snapshot, graph_problems=graph_problems, graph_meta=graph_meta)
    problems = [*source_problems]
    candidates = retrieve_context(query, chunks, snapshot=snapshot, limit=40)
    packed, budget = pack_candidates(candidates, budget_tokens=budget_tokens)
    bundle = ContextBundle(
        repository=target.to_dict(),
        query={"text": query, "type": "natural_language"},
        source_snapshots=source_snapshots,
        completeness={**completeness, "source_count": len(chunks)},
        candidates=candidates,
        packed_context=packed,
        budget=budget,
    ).with_digest()
    meta = {"repository": target.to_dict(), "graph": graph_meta}
    return bundle, problems, meta

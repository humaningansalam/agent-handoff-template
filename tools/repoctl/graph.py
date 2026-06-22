from __future__ import annotations

from pathlib import Path
from typing import Any

from .code_index import CodeIndexEntry, build_code_index
from .git import normalize_repo_path
from .graph_code_provider import build_precise_symbols
from .graph_import_resolver import resolve_python_imports
from .graph_model import GraphEdge, GraphNode, GraphSnapshot, anchor_id, artifact_id, change_event_id, digest_data, file_id, import_ref_id, repository_id, symbol_id, task_id as graph_task_id, topic_id
from .meta import RepoMetadataFacts, read_metadata_facts
from .repositories import RepoTarget
from .tasks import Problem, load_completion_receipts


def _has_errors(problems: list[Problem]) -> bool:
    return any(problem.severity == "error" for problem in problems)


def _annotation_topics(annotation: dict[str, Any] | None) -> list[str]:
    if not annotation:
        return []
    topics = annotation.get("topics")
    if not isinstance(topics, list):
        return []
    return sorted({str(topic) for topic in topics if str(topic).strip()})


def _annotation_payload(annotation: dict[str, Any] | None) -> dict[str, Any]:
    if not annotation:
        return {}
    payload: dict[str, Any] = {}
    for key in ("role", "purpose", "topics", "declared_effects", "caution"):
        if key in annotation:
            value = annotation[key]
            if isinstance(value, list):
                payload[key] = sorted({str(item) for item in value if str(item).strip()})
            else:
                payload[key] = value
    return payload


def _index_facts(entry: CodeIndexEntry) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "language": entry.language,
        "classification": entry.classification,
        "symbol_names": sorted(entry.symbols),
        "call_names": sorted(entry.calls),
        "dependencies": sorted(entry.deps),
        "observed_effects": sorted(entry.observed_effects),
        "parse_status": entry.parse_status,
    }
    if entry.parse_error:
        facts["parse_error"] = entry.parse_error
    return facts


def _file_node(repo_id: str, entry: CodeIndexEntry, metadata: RepoMetadataFacts | None) -> GraphNode:
    facts: dict[str, Any] = {"index": _index_facts(entry)}
    if metadata is not None:
        if metadata.annotation is not None:
            facts["annotation"] = _annotation_payload(metadata.annotation)
        if metadata.areas or metadata.policy_topics:
            facts["policy"] = {
                "areas": list(metadata.areas),
                "topics": list(metadata.policy_topics),
            }
    return GraphNode(
        id=file_id(repo_id, entry.path),
        kind="file",
        identity={
            "repo_id": repo_id,
            "path": entry.path,
            "workspace_path": entry.workspace_path,
        },
        facts=facts,
    )


def build_graph(root: Path, *, target: RepoTarget) -> tuple[GraphSnapshot | None, list[Problem], dict[str, Any]]:
    entries, index_problems, index_meta = build_code_index(root, changed=False, limit=-1, target=target)
    if _has_errors(index_problems):
        return None, index_problems, {"repository": target.to_dict()}

    summary = index_meta.get("summary", {})
    if summary.get("truncated"):
        return (
            None,
            [
                Problem(
                    "error",
                    "graph_index_truncated",
                    "graph build requires a complete code index; code index output was truncated",
                    target.display_path,
                )
            ],
            {"repository": target.to_dict(), "index": summary},
        )

    metadata_facts, metadata_problems, metadata_meta = read_metadata_facts(root, target=target)
    problems = [*index_problems, *metadata_problems]
    if _has_errors(metadata_problems):
        return None, problems, {"repository": target.to_dict()}

    repo_id = target.id
    metadata_by_path = {fact.path: fact for fact in metadata_facts}
    nodes: dict[str, GraphNode] = {
        repository_id(repo_id): GraphNode(
            id=repository_id(repo_id),
            kind="repository",
            identity=target.to_dict(),
        )
    }
    edges: dict[tuple[str, str, str, str, str], GraphEdge] = {}

    def add_edge(edge: GraphEdge) -> None:
        edges[(edge.kind, edge.from_id, edge.to_id, edge.assertion, edge.source)] = edge

    def ensure_receipt_file_node(path: str) -> str:
        node_id = file_id(repo_id, path)
        nodes.setdefault(
            node_id,
            GraphNode(
                id=node_id,
                kind="file",
                identity={"repo_id": repo_id, "path": path, "workspace_path": f"{target.display_path}/{path}"},
                facts={"receipt": {"present_in_current_inventory": False}},
            ),
        )
        return node_id

    for entry in entries:
        file_node = _file_node(repo_id, entry, metadata_by_path.get(entry.path))
        nodes[file_node.id] = file_node
        add_edge(GraphEdge("CONTAINS", repository_id(repo_id), file_node.id, "observed", "code_index"))

        for raw_import in sorted(entry.imports):
            import_node_id = import_ref_id(repo_id, entry.language, raw_import)
            nodes.setdefault(
                import_node_id,
                GraphNode(
                    id=import_node_id,
                    kind="import_ref",
                    identity={
                        "repo_id": repo_id,
                        "language": entry.language,
                        "raw_import": raw_import,
                    },
                ),
            )
            add_edge(GraphEdge("DECLARES_IMPORT", file_node.id, import_node_id, "observed", "code_index"))

        metadata = metadata_by_path.get(entry.path)
        if metadata is None:
            continue
        for topic in metadata.policy_topics:
            topic_node_id = topic_id(repo_id, topic)
            nodes.setdefault(topic_node_id, GraphNode(id=topic_node_id, kind="topic", identity={"repo_id": repo_id, "topic": topic}))
            add_edge(GraphEdge("HAS_TOPIC", file_node.id, topic_node_id, "default", "repometa_policy"))
        for topic in _annotation_topics(metadata.annotation):
            topic_node_id = topic_id(repo_id, topic)
            nodes.setdefault(topic_node_id, GraphNode(id=topic_node_id, kind="topic", identity={"repo_id": repo_id, "topic": topic}))
            add_edge(GraphEdge("HAS_TOPIC", file_node.id, topic_node_id, "declared", "repometa_annotation"))

    task_receipts = load_completion_receipts(root, repo_id=repo_id)
    for receipt in task_receipts:
        receipt_task_id = str(receipt.get("task_id") or "")
        if not receipt_task_id:
            continue
        task_node_id = graph_task_id(receipt_task_id)
        nodes[task_node_id] = GraphNode(
            id=task_node_id,
            kind="task",
            identity={"task_id": receipt_task_id, "repo_id": repo_id},
            facts={
                "receipt": {
                    "status": str(receipt.get("status") or ""),
                    "task_path": str(receipt.get("task_path") or ""),
                    "archive_path": str(receipt.get("archive_path") or ""),
                    "completed_at": str(receipt.get("completed_at") or ""),
                    "content_sha256": str(receipt.get("content_sha256") or ""),
                }
            },
        )
        verification = receipt.get("verification") if isinstance(receipt.get("verification"), dict) else {}
        artifact_path = str(verification.get("archive_path") or verification.get("task_path") or receipt.get("task_path") or "")
        if artifact_path:
            artifact_node_id = artifact_id(receipt_task_id, artifact_path)
            nodes[artifact_node_id] = GraphNode(
                id=artifact_node_id,
                kind="artifact",
                identity={"task_id": receipt_task_id, "path": artifact_path},
                facts={"receipt": {"content_sha256": str(verification.get("content_sha256") or receipt.get("content_sha256") or "")}},
            )
            add_edge(GraphEdge("TASK_VERIFIED_BY", task_node_id, artifact_node_id, "recorded", "task_completion"))
        raw_changes = receipt.get("changed_entries") if isinstance(receipt.get("changed_entries"), list) else []
        for index, raw_change in enumerate(raw_changes):
            if not isinstance(raw_change, dict):
                continue
            change = str(raw_change.get("change") or "")
            path = str(raw_change.get("path") or "")
            old_path = str(raw_change.get("old_path") or "")
            if not change or not path:
                continue
            change_node_id = change_event_id(receipt_task_id, index)
            nodes[change_node_id] = GraphNode(
                id=change_node_id,
                kind="change_event",
                identity={"task_id": receipt_task_id, "index": index},
                facts={"receipt": {"change": change, "path": path, "old_path": old_path}},
            )
            add_edge(GraphEdge("TASK_RECORDED_CHANGE", task_node_id, change_node_id, "recorded", "task_completion"))
            affected_file_id = ensure_receipt_file_node(path)
            add_edge(GraphEdge("CHANGE_AFFECTED_FILE", change_node_id, affected_file_id, "recorded", "task_completion", {"role": "path"}))
            if old_path:
                old_file_id = ensure_receipt_file_node(old_path)
                add_edge(GraphEdge("CHANGE_AFFECTED_FILE", change_node_id, old_file_id, "recorded", "task_completion", {"role": "old_path"}))

    precise_symbols, precise_meta = build_precise_symbols(root, target=target, paths=[entry.path for entry in entries])
    for precise_symbol in precise_symbols:
        file_node_id = file_id(repo_id, precise_symbol.path)
        if file_node_id not in nodes:
            continue
        symbol_node_id = symbol_id(repo_id, precise_symbol.provider, precise_symbol.provider_symbol_id)
        anchor = precise_symbol.anchor
        anchor_node_id = anchor_id(repo_id, precise_symbol.provider, anchor.path, anchor.start_line, anchor.start_col, anchor.end_line, anchor.end_col)
        nodes[symbol_node_id] = GraphNode(
            id=symbol_node_id,
            kind="symbol",
            identity={
                "repo_id": repo_id,
                "provider": precise_symbol.provider,
                "provider_symbol_id": precise_symbol.provider_symbol_id,
            },
            facts={
                "provider": {
                    "language": precise_symbol.language,
                    "kind": precise_symbol.kind,
                    "name": precise_symbol.name,
                    "qualified_name": precise_symbol.qualified_name,
                }
            },
        )
        nodes[anchor_node_id] = GraphNode(
            id=anchor_node_id,
            kind="anchor",
            identity={
                "repo_id": repo_id,
                "provider": precise_symbol.provider,
                **anchor.to_dict(),
            },
        )
        add_edge(GraphEdge("DEFINES", file_node_id, symbol_node_id, "resolved", precise_symbol.provider))
        add_edge(GraphEdge("ANCHORS", symbol_node_id, anchor_node_id, "resolved", precise_symbol.provider))

    import_resolutions = resolve_python_imports(entries)
    for resolution in import_resolutions:
        importer_node_id = file_id(repo_id, resolution.importer_path)
        target_node_id = file_id(repo_id, resolution.target_path)
        import_node_id = import_ref_id(repo_id, resolution.language, resolution.raw_import)
        if importer_node_id not in nodes or target_node_id not in nodes or import_node_id not in nodes:
            continue
        add_edge(
            GraphEdge(
                "RESOLVES_TO",
                import_node_id,
                target_node_id,
                "resolved",
                resolution.provider,
                {"importer_path": resolution.importer_path},
            )
        )
        add_edge(
            GraphEdge(
                "IMPORTS_FILE",
                importer_node_id,
                target_node_id,
                "resolved",
                resolution.provider,
                {"raw_import": resolution.raw_import},
            )
        )

    parse_error_count = int(summary.get("parse_error") or 0)
    source_payloads = {
        "code_index": [entry.to_dict() for entry in entries],
        "repometa_annotation": [
            {"path": fact.path, "annotation": fact.annotation}
            for fact in metadata_facts
            if fact.annotation is not None
        ],
        "repometa_policy": [
            {"path": fact.path, "areas": list(fact.areas), "policy_topics": list(fact.policy_topics)}
            for fact in metadata_facts
        ],
        "task_completion": task_receipts,
        "python_ast": [symbol.to_dict() for symbol in precise_symbols],
        "python_import_resolver": [resolution.to_dict() for resolution in import_resolutions],
    }
    snapshot = GraphSnapshot(
        repository=target.to_dict(),
        sources=[
            {"kind": "code_index", "assertion": "observed", "digest": digest_data(source_payloads["code_index"])},
            {"kind": "repometa_annotation", "assertion": "declared", "digest": digest_data(source_payloads["repometa_annotation"])},
            {"kind": "repometa_policy", "assertion": "default", "digest": digest_data(source_payloads["repometa_policy"])},
            {"kind": "task_completion", "assertion": "recorded", "digest": digest_data(source_payloads["task_completion"])},
            {"kind": "python_ast", "assertion": "resolved", "digest": digest_data(source_payloads["python_ast"])},
            {"kind": "python_import_resolver", "assertion": "resolved", "digest": digest_data(source_payloads["python_import_resolver"])},
        ],
        completeness={
            "inventory_complete": True,
            "identity_collisions": 0,
            "metadata_store_valid": True,
            "receipt_set_complete": True,
            "index_truncated": False,
            "code_facts_complete": parse_error_count == 0,
            "parse_error_count": parse_error_count,
            "provider_failures": [],
        },
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        capabilities=["repository", "file", "import_ref", "topic", "task", "change_event", "artifact", "symbol", "anchor", "import_resolution"],
    ).with_digest()
    return snapshot, problems, {"repository": target.to_dict(), "index": summary, "metadata": metadata_meta.get("summary", {}), "precise_provider": precise_meta}


def _node_by_id(snapshot: GraphSnapshot) -> dict[str, GraphNode]:
    return {node.id: node for node in snapshot.nodes}


def _query_payload(snapshot: GraphSnapshot, *, query: dict[str, str], node_ids: set[str], edges: list[GraphEdge]) -> dict[str, Any]:
    nodes = _node_by_id(snapshot)
    for edge in edges:
        node_ids.add(edge.from_id)
        node_ids.add(edge.to_id)
    return {
        "repository": snapshot.repository,
        "snapshot_digest": snapshot.snapshot_digest,
        "query": query,
        "nodes": [nodes[node_id].to_dict() for node_id in sorted(node_ids) if node_id in nodes],
        "edges": [edge.to_dict() for edge in sorted(edges, key=lambda item: (item.kind, item.from_id, item.to_id, item.assertion, item.source))],
    }


def query_graph(snapshot: GraphSnapshot, *, file: str = "", topic: str = "", import_ref: str = "") -> tuple[dict[str, Any] | None, list[Problem]]:
    selectors = [(name, value) for name, value in (("file", file), ("topic", topic), ("import", import_ref)) if value]
    if not selectors:
        return None, [Problem("error", "graph_query_selector_required", "pass exactly one graph query selector: --file, --topic, or --import")]
    if len(selectors) > 1:
        return None, [Problem("error", "graph_query_selector_ambiguous", "pass only one graph query selector")]

    repo_id = str(snapshot.repository.get("id") or "")
    selector, value = selectors[0]
    nodes = _node_by_id(snapshot)
    if selector == "file":
        normalized = normalize_repo_path(value)
        if not normalized:
            return None, [Problem("error", "graph_query_invalid_path", "graph file query must be a normalized repo-relative path")]
        wanted = file_id(repo_id, normalized)
        if wanted not in nodes:
            return None, [Problem("error", "graph_query_not_found", f"graph file query matched no node: {normalized}", normalized)]
        matched_edges = [edge for edge in snapshot.edges if edge.from_id == wanted or edge.to_id == wanted]
        return _query_payload(snapshot, query={"type": "file", "path": normalized}, node_ids={wanted}, edges=matched_edges), []

    if selector == "topic":
        wanted = topic_id(repo_id, value)
        if wanted not in nodes:
            return None, [Problem("error", "graph_query_not_found", f"graph topic query matched no node: {value}")]
        matched_edges = [edge for edge in snapshot.edges if edge.kind == "HAS_TOPIC" and edge.to_id == wanted]
        return _query_payload(snapshot, query={"type": "topic", "topic": value}, node_ids={wanted}, edges=matched_edges), []

    matched_import_nodes = [
        node
        for node in snapshot.nodes
        if node.kind == "import_ref" and str(node.identity.get("raw_import") or "") == value
    ]
    matched_ids = {node.id for node in matched_import_nodes}
    if not matched_ids:
        return None, [Problem("error", "graph_query_not_found", f"graph import query matched no node: {value}")]
    matched_edges = [edge for edge in snapshot.edges if edge.kind == "DECLARES_IMPORT" and edge.to_id in matched_ids]
    return _query_payload(snapshot, query={"type": "import", "raw_import": value}, node_ids=set(matched_ids), edges=matched_edges), []

from __future__ import annotations

from pathlib import Path
from typing import Any

from .code_index import CodeIndexEntry, build_code_index
from .git import normalize_repo_path
from .graph_code_provider import build_precise_calls, build_precise_symbols
from .graph_import_resolver import resolve_code_imports
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
    precise_symbol_node_ids: dict[str, str] = {}
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
        precise_symbol_node_ids[precise_symbol.provider_symbol_id] = symbol_node_id

    import_resolutions = resolve_code_imports(entries)

    precise_calls, precise_call_meta = build_precise_calls(
        root,
        target=target,
        paths=[entry.path for entry in entries],
        symbols=precise_symbols,
        import_resolutions=import_resolutions,
    )
    for precise_call in precise_calls:
        caller_node_id = precise_symbol_node_ids.get(precise_call.caller_provider_symbol_id)
        callee_node_id = precise_symbol_node_ids.get(precise_call.callee_provider_symbol_id)
        if not caller_node_id or not callee_node_id:
            continue
        add_edge(
            GraphEdge(
                "CALLS",
                caller_node_id,
                callee_node_id,
                "resolved",
                precise_call.provider,
                {"scope": precise_call.scope, "anchor": precise_call.anchor.to_dict()},
            )
        )

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
        "python_ast_calls": [call.to_dict() for call in precise_calls],
        "python_import_resolver": [resolution.to_dict() for resolution in import_resolutions if resolution.provider == "python_import_resolver"],
        "js_ts_relative_import_resolver": [resolution.to_dict() for resolution in import_resolutions if resolution.provider == "js_ts_relative_import_resolver"],
    }
    snapshot = GraphSnapshot(
        repository=target.to_dict(),
        sources=[
            {"kind": "code_index", "assertion": "observed", "digest": digest_data(source_payloads["code_index"])},
            {"kind": "repometa_annotation", "assertion": "declared", "digest": digest_data(source_payloads["repometa_annotation"])},
            {"kind": "repometa_policy", "assertion": "default", "digest": digest_data(source_payloads["repometa_policy"])},
            {"kind": "task_completion", "assertion": "recorded", "digest": digest_data(source_payloads["task_completion"])},
            {"kind": "python_ast", "assertion": "resolved", "digest": digest_data(source_payloads["python_ast"])},
            {"kind": "python_ast_calls", "assertion": "resolved", "digest": digest_data(source_payloads["python_ast_calls"])},
            {"kind": "python_import_resolver", "assertion": "resolved", "digest": digest_data(source_payloads["python_import_resolver"])},
            {"kind": "js_ts_relative_import_resolver", "assertion": "resolved", "digest": digest_data(source_payloads["js_ts_relative_import_resolver"])},
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
        capabilities=["repository", "file", "import_ref", "topic", "task", "change_event", "artifact", "symbol", "anchor", "import_resolution", "same_file_calls", "cross_file_import_calls"],
    ).with_digest()
    return snapshot, problems, {"repository": target.to_dict(), "index": summary, "metadata": metadata_meta.get("summary", {}), "precise_provider": precise_meta, "precise_calls": precise_call_meta}


def _node_by_id(snapshot: GraphSnapshot) -> dict[str, GraphNode]:
    return {node.id: node for node in snapshot.nodes}


def _edge_key(edge: GraphEdge) -> tuple[str, str, str, str, str]:
    return (edge.kind, edge.from_id, edge.to_id, edge.assertion, edge.source)


def _node_summary(node: GraphNode | None) -> dict[str, Any]:
    if node is None:
        return {}
    summary: dict[str, Any] = {"id": node.id, "kind": node.kind}
    if node.kind == "file":
        summary["path"] = node.identity.get("path")
    elif node.kind == "symbol":
        provider = node.facts.get("provider") if isinstance(node.facts.get("provider"), dict) else {}
        summary["name"] = provider.get("name")
        summary["qualified_name"] = provider.get("qualified_name")
        summary["symbol_kind"] = provider.get("kind")
    elif node.kind == "import_ref":
        summary["raw_import"] = node.identity.get("raw_import")
        summary["language"] = node.identity.get("language")
    elif node.kind == "topic":
        summary["topic"] = node.identity.get("topic")
    return summary


def _symbol_file_id(snapshot: GraphSnapshot, symbol_node_id: str) -> str:
    for edge in snapshot.edges:
        if edge.kind == "DEFINES" and edge.to_id == symbol_node_id:
            return edge.from_id
    return ""


def _symbol_anchor_node(snapshot: GraphSnapshot, symbol_node_id: str) -> GraphNode | None:
    nodes = _node_by_id(snapshot)
    for edge in snapshot.edges:
        if edge.kind == "ANCHORS" and edge.from_id == symbol_node_id:
            return nodes.get(edge.to_id)
    return None


def _symbol_match_dict(snapshot: GraphSnapshot, node: GraphNode) -> dict[str, Any]:
    provider = node.facts.get("provider") if isinstance(node.facts.get("provider"), dict) else {}
    file_node = _node_by_id(snapshot).get(_symbol_file_id(snapshot, node.id))
    anchor_node = _symbol_anchor_node(snapshot, node.id)
    match = {
        "id": node.id,
        "kind": node.kind,
        "name": provider.get("name"),
        "qualified_name": provider.get("qualified_name"),
        "symbol_kind": provider.get("kind"),
        "provider": node.identity.get("provider"),
        "provider_symbol_id": node.identity.get("provider_symbol_id"),
        "path": file_node.identity.get("path") if file_node else None,
    }
    if anchor_node is not None:
        match["range"] = {
            "start_line": anchor_node.identity.get("start_line"),
            "start_col": anchor_node.identity.get("start_col"),
            "end_line": anchor_node.identity.get("end_line"),
            "end_col": anchor_node.identity.get("end_col"),
        }
    return match


def _match_symbols(snapshot: GraphSnapshot, selector: str, *, in_file: str = "") -> list[GraphNode]:
    nodes = _node_by_id(snapshot)
    normalized_file = normalize_repo_path(in_file) if in_file else ""
    matches: list[GraphNode] = []
    for node in snapshot.nodes:
        if node.kind != "symbol":
            continue
        provider = node.facts.get("provider") if isinstance(node.facts.get("provider"), dict) else {}
        if selector not in {str(provider.get("name") or ""), str(provider.get("qualified_name") or "")}:
            continue
        if normalized_file:
            file_node = nodes.get(_symbol_file_id(snapshot, node.id))
            if file_node is None or file_node.identity.get("path") != normalized_file:
                continue
        matches.append(node)
    return sorted(matches, key=lambda item: (str(_symbol_match_dict(snapshot, item).get("path") or ""), str(_symbol_match_dict(snapshot, item).get("qualified_name") or ""), item.id))


def _query_warnings(snapshot: GraphSnapshot) -> list[dict[str, str]]:
    warnings: list[dict[str, str]] = []
    if not snapshot.completeness.get("code_facts_complete", True):
        warnings.append(
            {
                "code": "graph_code_facts_incomplete",
                "message": f"code index has {snapshot.completeness.get('parse_error_count', 0)} parse error(s); query results may be incomplete",
            }
        )
    for failure in snapshot.completeness.get("provider_failures", []):
        warnings.append({"code": "graph_provider_failure", "message": str(failure)})
    return warnings


def _path_from_edge(nodes: dict[str, GraphNode], edge: GraphEdge, *, reason: str) -> dict[str, Any]:
    source: dict[str, Any] = {"provider": edge.source, "assertion": edge.assertion}
    if edge.facts:
        source["facts"] = edge.facts
    return {
        "from": _node_summary(nodes.get(edge.from_id)),
        "edge": edge.kind,
        "to": _node_summary(nodes.get(edge.to_id)),
        "reason": reason,
        "source": source,
    }


def _definition_edges(snapshot: GraphSnapshot, node_ids: set[str]) -> list[GraphEdge]:
    wanted = set(node_ids)
    edges: list[GraphEdge] = []
    for edge in snapshot.edges:
        if edge.kind == "DEFINES" and (edge.from_id in wanted or edge.to_id in wanted):
            wanted.add(edge.from_id)
            wanted.add(edge.to_id)
            edges.append(edge)
        elif edge.kind == "ANCHORS" and edge.from_id in wanted:
            wanted.add(edge.to_id)
            edges.append(edge)
    node_ids.update(wanted)
    return edges


def _query_payload(
    snapshot: GraphSnapshot,
    *,
    query: dict[str, Any],
    node_ids: set[str],
    edges: list[GraphEdge],
    matches: list[dict[str, Any]] | None = None,
    paths: list[dict[str, Any]] | None = None,
    warnings: list[dict[str, str]] | None = None,
) -> dict[str, Any]:
    nodes = _node_by_id(snapshot)
    for edge in edges:
        node_ids.add(edge.from_id)
        node_ids.add(edge.to_id)
    sorted_edges = sorted({_edge_key(edge): edge for edge in edges}.values(), key=_edge_key)
    return {
        "repository": snapshot.repository,
        "snapshot_digest": snapshot.snapshot_digest,
        "query": query,
        "matches": matches or [],
        "nodes": [nodes[node_id].to_dict() for node_id in sorted(node_ids) if node_id in nodes],
        "edges": [edge.to_dict() for edge in sorted_edges],
        "paths": paths or [],
        "completeness": snapshot.completeness,
        "warnings": warnings or _query_warnings(snapshot),
    }


def query_graph(
    snapshot: GraphSnapshot,
    *,
    file: str = "",
    topic: str = "",
    import_ref: str = "",
    symbol: str = "",
    callers_of: str = "",
    callees_of: str = "",
    impact_file: str = "",
    impact_symbol: str = "",
    in_file: str = "",
    depth: int = 1,
) -> tuple[dict[str, Any] | None, list[Problem]]:
    selectors = [
        (name, value)
        for name, value in (
            ("file", file),
            ("topic", topic),
            ("import", import_ref),
            ("symbol", symbol),
            ("callers_of", callers_of),
            ("callees_of", callees_of),
            ("impact_file", impact_file),
            ("impact_symbol", impact_symbol),
        )
        if value
    ]
    if not selectors:
        return None, [
            Problem(
                "error",
                "graph_query_selector_required",
                "pass exactly one graph query selector: --file, --topic, --import, --symbol, --callers-of, --callees-of, --impact-file, or --impact-symbol",
            )
        ]
    if len(selectors) > 1:
        return None, [Problem("error", "graph_query_selector_ambiguous", "pass only one graph query selector")]
    if depth < 1:
        return None, [Problem("error", "graph_query_invalid_depth", "graph query depth must be at least 1")]

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
        return _query_payload(snapshot, query={"type": "file", "path": normalized}, node_ids={wanted}, edges=matched_edges, matches=[_node_summary(nodes[wanted])]), []

    if selector == "topic":
        wanted = topic_id(repo_id, value)
        if wanted not in nodes:
            return None, [Problem("error", "graph_query_not_found", f"graph topic query matched no node: {value}")]
        matched_edges = [edge for edge in snapshot.edges if edge.kind == "HAS_TOPIC" and edge.to_id == wanted]
        return _query_payload(snapshot, query={"type": "topic", "topic": value}, node_ids={wanted}, edges=matched_edges, matches=[_node_summary(nodes[wanted])]), []

    if selector == "import":
        matched_import_nodes = [
            node
            for node in snapshot.nodes
            if node.kind == "import_ref" and str(node.identity.get("raw_import") or "") == value
        ]
        matched_ids = {node.id for node in matched_import_nodes}
        if not matched_ids:
            return None, [Problem("error", "graph_query_not_found", f"graph import query matched no node: {value}")]
        matched_edges = [edge for edge in snapshot.edges if edge.kind in {"DECLARES_IMPORT", "RESOLVES_TO"} and (edge.to_id in matched_ids or edge.from_id in matched_ids)]
        return _query_payload(snapshot, query={"type": "import", "raw_import": value}, node_ids=set(matched_ids), edges=matched_edges, matches=[_node_summary(node) for node in matched_import_nodes]), []

    def resolve_one_symbol(query_type: str, raw_symbol: str) -> tuple[GraphNode | None, dict[str, Any] | None, list[Problem]]:
        matches = _match_symbols(snapshot, raw_symbol, in_file=in_file)
        query = {"type": query_type, "symbol": raw_symbol}
        if in_file:
            query["in_file"] = normalize_repo_path(in_file)
        if not matches:
            return None, None, [Problem("error", "graph_query_not_found", f"graph symbol query matched no node: {raw_symbol}", normalize_repo_path(in_file) if in_file else None)]
        match_payloads = [_symbol_match_dict(snapshot, node) for node in matches]
        if len(matches) > 1:
            result = _query_payload(snapshot, query=query, node_ids={node.id for node in matches}, edges=_definition_edges(snapshot, {node.id for node in matches}), matches=match_payloads)
            return None, result, [Problem("error", "graph_query_ambiguous_symbol", f"graph symbol query matched {len(matches)} symbols; pass --in-file or a qualified name")]
        return matches[0], _query_payload(snapshot, query=query, node_ids={matches[0].id}, edges=_definition_edges(snapshot, {matches[0].id}), matches=match_payloads), []

    if selector == "symbol":
        _symbol_node, result, problems = resolve_one_symbol("symbol", value)
        return result, problems

    if selector in {"callers_of", "callees_of"}:
        symbol_node, base_result, problems = resolve_one_symbol(selector, value)
        if symbol_node is None:
            return base_result, problems
        if selector == "callers_of":
            call_edges = [edge for edge in snapshot.edges if edge.kind == "CALLS" and edge.to_id == symbol_node.id]
            reason = "caller invokes selected symbol"
        else:
            call_edges = [edge for edge in snapshot.edges if edge.kind == "CALLS" and edge.from_id == symbol_node.id]
            reason = "selected symbol invokes callee"
        node_ids = {symbol_node.id}
        edges = [*call_edges, *_definition_edges(snapshot, {symbol_node.id, *(edge.from_id for edge in call_edges), *(edge.to_id for edge in call_edges)})]
        paths = [_path_from_edge(nodes, edge, reason=reason) for edge in sorted(call_edges, key=_edge_key)]
        return _query_payload(
            snapshot,
            query=base_result["query"] if base_result else {"type": selector, "symbol": value},
            node_ids=node_ids,
            edges=edges,
            matches=base_result["matches"] if base_result else [],
            paths=paths,
        ), []

    def impact_walk(start_ids: set[str], *, max_depth: int) -> tuple[set[str], list[GraphEdge], list[dict[str, Any]]]:
        visited = set(start_ids)
        frontier = set(start_ids)
        collected_edges: list[GraphEdge] = []
        paths: list[dict[str, Any]] = []
        for distance in range(1, max_depth + 1):
            next_frontier: set[str] = set()
            for edge in sorted(snapshot.edges, key=_edge_key):
                reason = ""
                if edge.kind == "CALLS" and edge.to_id in frontier:
                    reason = f"depth {distance}: caller depends on changed symbol"
                    next_id = edge.from_id
                elif edge.kind == "IMPORTS_FILE" and edge.to_id in frontier:
                    reason = f"depth {distance}: file imports changed file"
                    next_id = edge.from_id
                elif edge.kind == "DEFINES" and edge.from_id in frontier:
                    reason = f"depth {distance}: file defines potentially changed symbol"
                    next_id = edge.to_id
                elif edge.kind == "DEFINES" and edge.to_id in frontier:
                    reason = f"depth {distance}: symbol belongs to changed file"
                    next_id = edge.from_id
                else:
                    continue
                collected_edges.append(edge)
                paths.append(_path_from_edge(nodes, edge, reason=reason))
                if next_id not in visited:
                    visited.add(next_id)
                    next_frontier.add(next_id)
            frontier = next_frontier
            if not frontier:
                break
        return visited, collected_edges, paths

    if selector == "impact_file":
        normalized = normalize_repo_path(value)
        if not normalized:
            return None, [Problem("error", "graph_query_invalid_path", "graph impact-file query must be a normalized repo-relative path")]
        wanted = file_id(repo_id, normalized)
        if wanted not in nodes:
            return None, [Problem("error", "graph_query_not_found", f"graph impact-file query matched no node: {normalized}", normalized)]
        node_ids, edges, paths = impact_walk({wanted}, max_depth=depth)
        return _query_payload(
            snapshot,
            query={"type": "impact_file", "path": normalized, "depth": depth},
            node_ids=node_ids,
            edges=edges,
            matches=[_node_summary(nodes[wanted])],
            paths=paths,
        ), []

    symbol_node, base_result, problems = resolve_one_symbol("impact_symbol", value)
    if symbol_node is None:
        return base_result, problems
    node_ids, edges, paths = impact_walk({symbol_node.id}, max_depth=depth)
    return _query_payload(
        snapshot,
        query=base_result["query"] | {"depth": depth} if base_result else {"type": "impact_symbol", "symbol": value, "depth": depth},
        node_ids=node_ids,
        edges=edges,
        matches=base_result["matches"] if base_result else [],
        paths=paths,
    ), []

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .code_index import CodeIndexEntry, build_code_index, semantic_provider_entries
from .git import normalize_repo_path
from .graph_import_resolver import IMPORT_RESOLVER_LANGUAGES, resolve_code_imports
from .graph_model import GraphContextAnchor, GraphContextAnchorKind, GraphEdge, GraphNode, GraphSnapshot, ProviderCoverage, anchor_id, artifact_id, change_event_id, digest_data, document_id, file_id, import_ref_id, knowledge_id, repository_id, symbol_id, task_id as graph_task_id, topic_id
from .graph_semantic_model import SemanticProviderResult
from .graph_semantic_provider import build_semantic_providers
from .graph_structured_relations import RpcResolutionOutcome, STRUCTURED_EDGE_KIND, build_structured_file_relations
from .language_profiles import graph_language_capabilities, is_semantic_source_language, language_for_path
from .knowledge_candidates import KnowledgeExplicitPathKind, KnowledgeExplicitPathRole, knowledge_records_for_graph
from .meta import RepoMetadataFacts, read_metadata_facts
from .path_roles import is_test_path
from .repositories import RepoSelectorResolution, RepoSelectorStatus, RepoTarget, resolve_repo_selector_path
from .tasks import Problem, collect_completion_receipts, completion_receipt_artifact_path, completion_receipt_task_path, normalize_task_id


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


def _import_provider_coverage(
    capability: str,
    entries: list[CodeIndexEntry],
    *,
    provider_languages: set[str] | frozenset[str],
    analyzed_paths: list[str],
    evidence_level: str,
) -> ProviderCoverage:
    eligible_entries = [
        entry
        for entry in entries
        if entry.classification != "excluded" and is_semantic_source_language(entry.language)
    ]
    analyzed = set(analyzed_paths)
    supported_entries = [entry for entry in eligible_entries if entry.language in provider_languages]
    return ProviderCoverage(
        capability=capability,
        eligible_paths=tuple(sorted(entry.path for entry in eligible_entries)),
        analyzed_paths=tuple(sorted(entry.path for entry in supported_entries if entry.path in analyzed)),
        unsupported_paths=tuple(sorted(entry.path for entry in eligible_entries if entry.language not in provider_languages)),
        failed_paths=tuple(sorted(entry.path for entry in supported_entries if entry.path not in analyzed)),
        evidence_level=evidence_level,
    )


def _semantic_provider_coverage(
    capability: str,
    entries: list[CodeIndexEntry],
    results: list[SemanticProviderResult],
    *,
    languages: set[str] | None = None,
) -> ProviderCoverage:
    eligible_entries = [
        entry
        for entry in entries
        if entry.classification != "excluded"
        and is_semantic_source_language(entry.language)
        and (languages is None or entry.language in languages)
    ]
    provider_languages = {
        language
        for result in results
        for language in result.languages
    }
    if capability == "symbols":
        analyzed = {
            path
            for result in results
            for path in result.symbol_analyzed_paths
        }
        failed = {
            path
            for result in results
            for path in result.symbol_failed_paths
        }
        capability_evidence = [
            result.symbol_coverage
            for result in results
            if set(result.languages) & {entry.language for entry in eligible_entries}
            and (result.symbol_analyzed_paths or result.symbol_failed_paths)
        ]
    elif capability == "calls":
        analyzed = {
            path
            for result in results
            for path in result.call_analyzed_paths
        }
        failed = {
            path
            for result in results
            for path in result.call_failed_paths
        }
        capability_evidence = [
            result.call_coverage
            for result in results
            if set(result.languages) & {entry.language for entry in eligible_entries}
            and (result.call_analyzed_paths or result.call_failed_paths)
        ]
    else:
        analyzed = {
            path
            for result in results
            for path in result.rpc_analyzed_paths
        }
        failed = {
            path
            for result in results
            for path in result.rpc_failed_paths
        }
        capability_evidence = [
            result.rpc_coverage
            for result in results
            if set(result.languages) & {entry.language for entry in eligible_entries}
            and (result.rpc_analyzed_paths or result.rpc_failed_paths)
        ]
    evidence_level = "conservative" if any(item.evidence_level == "conservative" for item in capability_evidence) else "precise"
    coverage_gaps = tuple(sorted({gap for item in capability_evidence for gap in item.coverage_gaps}))
    supported_entries = [entry for entry in eligible_entries if entry.language in provider_languages]
    failed.update(entry.path for entry in supported_entries if entry.path not in analyzed)
    return ProviderCoverage(
        capability=capability,
        eligible_paths=tuple(sorted(entry.path for entry in eligible_entries)),
        analyzed_paths=tuple(sorted(entry.path for entry in supported_entries if entry.path in analyzed)),
        unsupported_paths=tuple(sorted(entry.path for entry in eligible_entries if entry.language not in provider_languages)),
        failed_paths=tuple(sorted(entry.path for entry in supported_entries if entry.path in failed and entry.path not in analyzed)),
        evidence_level=evidence_level,
        coverage_gaps=coverage_gaps,
    )


def _language_capabilities(
    entries: list[CodeIndexEntry],
    results: list[SemanticProviderResult],
) -> dict[str, Any]:
    capabilities = graph_language_capabilities({entry.language for entry in entries})
    for language, payload in capabilities.items():
        if not payload.get("semantic_source"):
            payload.update(
                {
                    "provider_defined": False,
                    "providers": [],
                    "symbols_status": "unsupported",
                    "calls_status": "unsupported",
                    "precise_semantics": False,
                }
            )
            continue
        language_results = [result for result in results if language in result.languages]
        symbol_coverage = _semantic_provider_coverage("symbols", entries, results, languages={language})
        call_coverage = _semantic_provider_coverage("calls", entries, results, languages={language})
        payload.update(
            {
                "provider_defined": bool(language_results),
                "providers": sorted(result.provider for result in language_results),
                "symbols_status": symbol_coverage.status,
                "calls_status": call_coverage.status,
                "precise_semantics": bool(symbol_coverage.analyzed_paths),
            }
        )
    return capabilities


def _index_facts(entry: CodeIndexEntry) -> dict[str, Any]:
    facts: dict[str, Any] = {
        "language": entry.language,
        "classification": entry.classification,
        "symbol_names": sorted(entry.symbols),
        "imports": sorted(entry.imports),
        "call_names": sorted(entry.calls),
        "dependencies": sorted(entry.deps),
        "observed_effects": sorted(entry.observed_effects),
        "parse_status": entry.parse_status,
        "import_occurrences": [occurrence.to_dict() for occurrence in entry.import_occurrences],
        "module_bindings": list(entry.module_bindings),
        "module_certain_bindings": list(entry.module_certain_bindings),
        "module_wildcard_import": entry.module_wildcard_import,
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


def _import_declarations(entry: CodeIndexEntry) -> list[dict[str, Any]]:
    if entry.language == "python":
        return [
            {
                "raw_import": occurrence.raw_import,
                "form": occurrence.form,
                "module": occurrence.module,
                "imported_name": occurrence.imported_name,
                "level": occurrence.level,
            }
            for occurrence in entry.import_occurrences
        ]
    return [
        {
            "raw_import": raw_import,
            "form": "raw",
            "module": raw_import,
            "imported_name": "",
            "level": 0,
        }
        for raw_import in entry.imports
    ]


def build_graph(
    root: Path,
    *,
    target: RepoTarget,
    code_index_result: tuple[list[CodeIndexEntry], list[Problem], dict[str, Any]] | None = None,
    cached_semantic_results: list[SemanticProviderResult] | None = None,
    provider_results_out: list[SemanticProviderResult] | None = None,
) -> tuple[GraphSnapshot | None, list[Problem], dict[str, Any]]:
    entries, index_problems, index_meta = code_index_result or build_code_index(root, changed=False, limit=-1, target=target)
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

        for declaration in _import_declarations(entry):
            raw_import = str(declaration["raw_import"])
            import_node_id = import_ref_id(
                repo_id,
                entry.path,
                entry.language,
                raw_import,
                form=str(declaration["form"]),
                level=int(declaration["level"]),
                module=str(declaration["module"]),
                imported_name=str(declaration["imported_name"]),
            )
            nodes.setdefault(
                import_node_id,
                GraphNode(
                    id=import_node_id,
                    kind="import_ref",
                    identity={
                        "repo_id": repo_id,
                        "importer_path": entry.path,
                        "language": entry.language,
                        "raw_import": raw_import,
                        "form": declaration["form"],
                        "module": declaration["module"],
                        "imported_name": declaration["imported_name"],
                        "level": declaration["level"],
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

    task_receipts, receipt_problems = collect_completion_receipts(root, repo_id=repo_id)
    problems.extend(Problem("warning", problem.code, problem.message, problem.path) for problem in receipt_problems)
    for receipt in task_receipts:
        receipt_task_id = str(receipt.get("task_id") or "")
        if not receipt_task_id:
            continue
        receipt_task_path = completion_receipt_task_path(receipt)
        task_node_id = graph_task_id(receipt_task_id)
        nodes[task_node_id] = GraphNode(
            id=task_node_id,
            kind="task",
            identity={"task_id": receipt_task_id, "repo_id": repo_id},
            facts={
                "receipt": {
                    "status": str(receipt.get("status") or ""),
                    "task_path_at_completion": receipt_task_path,
                    "completed_at": str(receipt.get("completed_at") or ""),
                    "content_sha256": str(receipt.get("content_sha256") or ""),
                    "repo_evidence": receipt.get("repo_evidence") if isinstance(receipt.get("repo_evidence"), dict) else {},
                }
            },
        )
        verification = receipt.get("verification") if isinstance(receipt.get("verification"), dict) else {}
        artifact_path = completion_receipt_artifact_path(root, receipt)
        if artifact_path:
            artifact_node_id = artifact_id(receipt_task_id, artifact_path)
            nodes[artifact_node_id] = GraphNode(
                id=artifact_node_id,
                kind="artifact",
                identity={"task_id": receipt_task_id, "path": artifact_path},
                facts={"receipt": {"content_sha256": str(receipt.get("content_sha256") or ""), "verification": verification}},
            )
            add_edge(GraphEdge("TASK_VERIFIED_BY", task_node_id, artifact_node_id, "recorded", "task_completion"))
        repo_evidence = receipt.get("repo_evidence") if isinstance(receipt.get("repo_evidence"), dict) else {}
        attribution = str(repo_evidence.get("attribution") or "none")
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
                facts={"receipt": {"change": change, "path": path, "old_path": old_path, "attribution": attribution}},
            )
            add_edge(GraphEdge("TASK_RECORDED_CHANGE", task_node_id, change_node_id, "recorded", "task_completion", {"attribution": attribution}))
            affected_file_id = ensure_receipt_file_node(path)
            add_edge(GraphEdge("CHANGE_AFFECTED_FILE", change_node_id, affected_file_id, "recorded", "task_completion", {"role": "path"}))
            add_edge(GraphEdge("TASK_CHANGED_FILE", task_node_id, affected_file_id, "recorded", "task_completion", {"change": change, "role": "path"}))
            if old_path:
                old_file_id = ensure_receipt_file_node(old_path)
                add_edge(GraphEdge("CHANGE_AFFECTED_FILE", change_node_id, old_file_id, "recorded", "task_completion", {"role": "old_path"}))
                add_edge(GraphEdge("TASK_CHANGED_FILE", task_node_id, old_file_id, "recorded", "task_completion", {"change": change, "role": "old_path"}))

    knowledge_records, knowledge_problems = knowledge_records_for_graph(root, repo_id=repo_id)
    problems.extend(
        Problem("warning", "graph_knowledge_unavailable", problem.message, problem.path, problem.code)
        for problem in knowledge_problems
    )
    current_paths = {entry.path for entry in entries}
    for record in knowledge_records:
        record_id = str(record.get("id") or "")
        if not record_id:
            continue
        record_status = str(record.get("status") or "")
        record_node_id = knowledge_id(record_id)
        nodes[record_node_id] = GraphNode(
            id=record_node_id,
            kind="knowledge",
            identity={"record_id": record_id, "repo_id": repo_id},
            facts={"record": record},
        )
        explicit_path_refs = record.get("explicit_path_refs") if isinstance(record.get("explicit_path_refs"), list) else []
        for path_ref in explicit_path_refs:
            if record_status != "reviewed":
                continue
            if not isinstance(path_ref, dict) or path_ref.get("kind") not in {
                KnowledgeExplicitPathKind.APPLIES_TO_PATH.value,
                KnowledgeExplicitPathKind.SOURCE_REF.value,
            }:
                continue
            if path_ref.get("role") != KnowledgeExplicitPathRole.CODE_ANCHOR.value:
                continue
            raw_path = str(path_ref.get("path") or "")
            resolution = resolve_repo_selector_path(
                raw_path,
                repository_path=target.display_path,
                known_paths=current_paths,
            )
            if resolution.status == RepoSelectorStatus.RESOLVED:
                add_edge(
                    GraphEdge(
                        "KNOWLEDGE_APPLIES_TO",
                        record_node_id,
                        file_id(repo_id, resolution.path),
                        "reviewed",
                        "knowledge_record",
                        {"freshness": record.get("status", "reviewed"), "path_kind": path_ref.get("kind")},
                    )
                )
            elif resolution.status == RepoSelectorStatus.AMBIGUOUS:
                problems.append(
                    Problem(
                        "warning",
                        "graph_knowledge_path_ambiguous",
                        f"reviewed Knowledge path resolves to multiple current files: {list(resolution.candidates)}",
                        raw_path,
                    )
                )
            elif resolution.status in {RepoSelectorStatus.INVALID, RepoSelectorStatus.NOT_FOUND}:
                problems.append(
                    Problem(
                        "warning",
                        "graph_knowledge_path_unresolved",
                        "reviewed Knowledge code path does not resolve to a current file",
                        raw_path,
                    )
                )
        for ref in record.get("source_refs", []) if isinstance(record.get("source_refs"), list) else []:
            if not isinstance(ref, dict):
                continue
            path = str(ref.get("path") or "")
            if not path:
                continue
            source_node_id = document_id(path)
            nodes.setdefault(
                source_node_id,
                GraphNode(
                    id=source_node_id,
                    kind="document",
                    identity={"path": path},
                    facts={"source_ref": ref},
                ),
            )
            add_edge(
                GraphEdge(
                    "KNOWLEDGE_SOURCED_FROM",
                    record_node_id,
                    source_node_id,
                    "recorded",
                    "knowledge_record",
                    {"freshness": record.get("status", "reviewed")},
                )
            )
        provenance = record.get("provenance") if isinstance(record.get("provenance"), dict) else {}
        source_task = str(provenance.get("source_task") or "")
        if source_task:
            source_task_id = graph_task_id(source_task)
            if source_task_id in nodes:
                add_edge(
                    GraphEdge(
                        "KNOWLEDGE_DERIVED_FROM_TASK",
                        record_node_id,
                        source_task_id,
                        "recorded",
                        "knowledge_record",
                        {"freshness": record.get("status", "reviewed")},
                    )
                )

    provider_entries = semantic_provider_entries(entries)
    import_resolutions, import_meta = resolve_code_imports(provider_entries, repo=target.root_path)
    semantic_results = build_semantic_providers(
        root,
        target=target,
        entries=provider_entries,
        import_resolutions=import_resolutions,
        cached_results=cached_semantic_results,
    )
    if provider_results_out is not None:
        provider_results_out.extend(semantic_results)
    dart_semantics = next((result for result in semantic_results if result.provider == "dart_analyzer"), None)
    structured_result = build_structured_file_relations(
        target.root_path,
        entries,
        dart_rpc_invocations=dart_semantics.rpc_invocations if dart_semantics is not None else (),
        dart_rpc_analyzed_paths=dart_semantics.rpc_analyzed_paths if dart_semantics is not None else (),
        dart_rpc_failed_paths=dart_semantics.rpc_failed_paths if dart_semantics is not None else (),
    )
    problems.extend(structured_result.problems)
    for relation in structured_result.relations:
        from_node_id = file_id(repo_id, relation.from_path)
        to_node_id = file_id(repo_id, relation.to_path)
        if from_node_id not in nodes or to_node_id not in nodes:
            continue
        evidence = [item.to_dict() for item in relation.evidence]
        add_edge(
            GraphEdge(
                STRUCTURED_EDGE_KIND,
                from_node_id,
                to_node_id,
                "resolved",
                "structured_file_relations",
                {
                    "evidence_type": "structured_file_relation",
                    "confidence": "medium" if any(item.get("confidence") == "medium" for item in evidence) else "high",
                    "relations": evidence,
                },
            )
        )

    rpc_facts_by_path: dict[str, list[dict[str, object]]] = {}
    for fact in dart_semantics.rpc_invocations if dart_semantics is not None else ():
        rpc_facts_by_path.setdefault(fact.path, []).append(fact.to_dict())
    rpc_resolutions_by_path: dict[str, list[dict[str, object]]] = {}
    for resolution in structured_result.rpc_resolutions:
        rpc_resolutions_by_path.setdefault(resolution.path, []).append(resolution.to_dict())
    for path in sorted(set(rpc_facts_by_path) | set(rpc_resolutions_by_path)):
        node_id = file_id(repo_id, path)
        node = nodes.get(node_id)
        if node is None:
            continue
        resolutions = sorted(rpc_resolutions_by_path.get(path, []), key=lambda item: str(item.get("fact_id") or ""))
        outcome_counts = {
            outcome: sum(1 for resolution in resolutions if resolution.get("outcome") == outcome)
            for outcome in ("linked", "unresolved", "ambiguous", "incomplete")
        }
        nodes[node_id] = GraphNode(
            id=node.id,
            kind=node.kind,
            identity=node.identity,
            facts={
                **node.facts,
                "rpc": {
                    "source_facts": sorted(rpc_facts_by_path.get(path, []), key=lambda item: str(item.get("fact_id") or "")),
                    "resolutions": resolutions,
                    "outcome_counts": outcome_counts,
                },
            },
        )

    precise_symbols = sorted(
        (symbol for result in semantic_results for symbol in result.symbols),
        key=lambda item: (item.provider, item.provider_symbol_id),
    )
    precise_calls = sorted(
        (call for result in semantic_results for call in result.calls),
        key=lambda item: (item.provider, item.caller_provider_symbol_id, item.callee_provider_symbol_id, item.anchor.start_line, item.anchor.start_col),
    )
    precise_symbol_node_ids: dict[tuple[str, str], str] = {}
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
        precise_symbol_node_ids[(precise_symbol.provider, precise_symbol.provider_symbol_id)] = symbol_node_id

    for precise_call in precise_calls:
        caller_node_id = precise_symbol_node_ids.get((precise_call.provider, precise_call.caller_provider_symbol_id))
        callee_node_id = precise_symbol_node_ids.get((precise_call.provider, precise_call.callee_provider_symbol_id))
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
        import_node_id = import_ref_id(
            repo_id,
            resolution.importer_path,
            resolution.language,
            resolution.raw_import,
            form=resolution.form,
            level=resolution.level,
            module=resolution.module,
            imported_name=resolution.imported_name,
        )
        if importer_node_id not in nodes or target_node_id not in nodes or import_node_id not in nodes:
            continue
        add_edge(
            GraphEdge(
                "RESOLVES_TO",
                import_node_id,
                target_node_id,
                "resolved",
                resolution.provider,
                {
                    "importer_path": resolution.importer_path,
                    "match_kind": resolution.match_kind,
                },
            )
        )
        add_edge(
            GraphEdge(
                "IMPORTS_FILE",
                importer_node_id,
                target_node_id,
                "resolved",
                resolution.provider,
                {
                    "raw_import": resolution.raw_import,
                    "match_kind": resolution.match_kind,
                },
            )
        )
        if is_test_path(resolution.importer_path) and not is_test_path(resolution.target_path):
            add_edge(
                GraphEdge(
                    "TESTS_FILE",
                    importer_node_id,
                    target_node_id,
                    "resolved",
                    resolution.provider,
                    {
                        "evidence_type": "direct_test_import",
                        "match_kind": resolution.match_kind,
                        "confidence": "medium",
                        "import_confidence": "high",
                        "test_role_evidence": "path_role",
                    },
                )
            )

    parse_error_count = int(summary.get("parse_error") or 0)
    provider_coverage = {
        "imports": _import_provider_coverage(
            "imports",
            entries,
            provider_languages=IMPORT_RESOLVER_LANGUAGES,
            analyzed_paths=[str(path) for path in import_meta.get("analyzed_paths", [])],
            evidence_level="conservative",
        ),
        "symbols": _semantic_provider_coverage("symbols", entries, semantic_results),
        "calls": _semantic_provider_coverage("calls", entries, semantic_results),
        "rpc": _semantic_provider_coverage("rpc", entries, semantic_results, languages={"dart"}),
        "structured_relations": ProviderCoverage(
            capability="structured_relations",
            eligible_paths=structured_result.eligible_paths,
            analyzed_paths=structured_result.analyzed_paths,
            unsupported_paths=(),
            failed_paths=structured_result.failed_paths,
            evidence_level="syntax_resolved",
        ),
    }
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
        "knowledge_records": knowledge_records,
        "structured_file_relations": [relation.to_dict() for relation in structured_result.relations],
        "rpc_resolutions": [resolution.to_dict() for resolution in structured_result.rpc_resolutions],
        "python_import_resolver": [resolution.to_dict() for resolution in import_resolutions if resolution.provider == "python_import_resolver"],
        "js_ts_relative_import_resolver": [resolution.to_dict() for resolution in import_resolutions if resolution.provider == "js_ts_relative_import_resolver"],
        "dart_import_resolver": [resolution.to_dict() for resolution in import_resolutions if resolution.provider == "dart_import_resolver"],
    }
    semantic_source_payloads: dict[str, list[dict[str, object]]] = {}
    for result in semantic_results:
        semantic_source_payloads[result.provider] = [symbol.to_dict() for symbol in result.symbols]
        semantic_source_payloads[f"{result.provider}_calls"] = [call.to_dict() for call in result.calls]
        if result.provider == "dart_analyzer":
            semantic_source_payloads[f"{result.provider}_rpc"] = [fact.to_dict() for fact in result.rpc_invocations]
    source_payloads.update(semantic_source_payloads)
    language_capabilities = _language_capabilities(entries, semantic_results)
    rpc_resolution_status = provider_coverage["rpc"].status
    if rpc_resolution_status == "complete" and any(
        resolution.outcome is RpcResolutionOutcome.INCOMPLETE
        for resolution in structured_result.rpc_resolutions
    ):
        rpc_resolution_status = "partial"
    capability_completeness = {
        "source_inventory": "complete",
        "file_inventory": "complete",
        "imports": provider_coverage["imports"].status,
        "symbols": provider_coverage["symbols"].status,
        "calls": provider_coverage["calls"].status,
        "rpc_resolution": rpc_resolution_status,
        "structured_relations": provider_coverage["structured_relations"].status,
        "task_history": "partial" if receipt_problems else "complete",
        "knowledge": "partial" if knowledge_problems else "complete",
    }
    overall_completeness = "complete" if all(value == "complete" for value in capability_completeness.values()) else "partial"
    snapshot = GraphSnapshot(
        repository=target.to_dict(),
        sources=[
            {"kind": "code_index", "assertion": "observed", "digest": digest_data(source_payloads["code_index"])},
            {"kind": "repometa_annotation", "assertion": "declared", "digest": digest_data(source_payloads["repometa_annotation"])},
            {"kind": "repometa_policy", "assertion": "default", "digest": digest_data(source_payloads["repometa_policy"])},
            {"kind": "task_completion", "assertion": "recorded", "digest": digest_data(source_payloads["task_completion"])},
            {"kind": "knowledge_records", "assertion": "reviewed", "digest": digest_data(source_payloads["knowledge_records"])},
            {"kind": "structured_file_relations", "assertion": "resolved", "digest": digest_data(source_payloads["structured_file_relations"])},
            {"kind": "rpc_resolutions", "assertion": "resolved", "digest": digest_data(source_payloads["rpc_resolutions"])},
            *[
                {"kind": kind, "assertion": "resolved", "digest": digest_data(payload)}
                for kind, payload in sorted(semantic_source_payloads.items())
            ],
            {"kind": "python_import_resolver", "assertion": "resolved", "digest": digest_data(source_payloads["python_import_resolver"])},
            {"kind": "js_ts_relative_import_resolver", "assertion": "resolved", "digest": digest_data(source_payloads["js_ts_relative_import_resolver"])},
            {"kind": "dart_import_resolver", "assertion": "resolved", "digest": digest_data(source_payloads["dart_import_resolver"])},
        ],
        completeness={
            "status": overall_completeness,
            "capabilities": capability_completeness,
            "inventory_complete": True,
            "identity_collisions": 0,
            "metadata_store_valid": True,
            "receipt_set_complete": not receipt_problems,
            "invalid_completion_receipts": len(receipt_problems),
            "receipt_problems": [problem.to_dict() for problem in receipt_problems],
            "index_truncated": False,
            "code_facts_complete": parse_error_count == 0,
            "parse_error_count": parse_error_count,
            "provider_failures": [
                failure.to_dict()
                for result in semantic_results
                for failure in result.failures
            ],
            "provider_coverage": {name: coverage.to_dict() for name, coverage in sorted(provider_coverage.items())},
            "language_capabilities": language_capabilities,
        },
        nodes=list(nodes.values()),
        edges=list(edges.values()),
        capabilities=["repository", "file", "import_ref", "topic", "task", "change_event", "artifact", "document", "knowledge", "symbol", "anchor", "import_resolution", "same_file_calls", "cross_file_import_calls", "direct_tests", "structured_file_relations", "rpc_resolution", "language_capabilities"],
    ).with_digest()
    return snapshot, problems, {
        "repository": target.to_dict(),
        "index": summary,
        "metadata": metadata_meta.get("summary", {}),
        "semantic_providers": [result.to_meta() for result in semantic_results],
        "import_resolvers": import_meta,
        "structured_relations": structured_result.to_meta(),
        "provider_coverage": {name: coverage.to_dict() for name, coverage in sorted(provider_coverage.items())},
        "language_capabilities": language_capabilities,
    }


def _node_by_id(snapshot: GraphSnapshot) -> dict[str, GraphNode]:
    return {node.id: node for node in snapshot.nodes}


def _edge_key(edge: GraphEdge) -> tuple[str, str, str, str, str]:
    return (edge.kind, edge.from_id, edge.to_id, edge.assertion, edge.source)


def _node_summary(node: GraphNode | None, *, symbol_paths: dict[str, str] | None = None) -> dict[str, Any]:
    if node is None:
        return {}
    summary: dict[str, Any] = {"id": node.id, "kind": node.kind}
    if node.kind == "file":
        summary["path"] = node.identity.get("path")
        rpc = node.facts.get("rpc") if isinstance(node.facts.get("rpc"), dict) else {}
        if isinstance(rpc.get("outcome_counts"), dict):
            summary["rpc_outcome_counts"] = rpc["outcome_counts"]
    elif node.kind == "symbol":
        provider = node.facts.get("provider") if isinstance(node.facts.get("provider"), dict) else {}
        summary["name"] = provider.get("name")
        summary["qualified_name"] = provider.get("qualified_name")
        summary["symbol_kind"] = provider.get("kind")
        path = str((symbol_paths or {}).get(node.id) or "")
        if path:
            summary["path"] = path
    elif node.kind == "import_ref":
        summary["raw_import"] = node.identity.get("raw_import")
        summary["language"] = node.identity.get("language")
        summary["importer_path"] = node.identity.get("importer_path")
        summary["form"] = node.identity.get("form")
    elif node.kind == "topic":
        summary["topic"] = node.identity.get("topic")
    elif node.kind == "task":
        summary["task_id"] = node.identity.get("task_id")
    elif node.kind == "artifact":
        summary["task_id"] = node.identity.get("task_id")
        summary["path"] = node.identity.get("path")
    elif node.kind == "document":
        summary["path"] = node.identity.get("path")
    elif node.kind == "knowledge":
        record = node.facts.get("record") if isinstance(node.facts.get("record"), dict) else {}
        summary["record_id"] = node.identity.get("record_id")
        summary["knowledge_kind"] = record.get("kind")
        summary["status"] = record.get("status")
        summary["title"] = record.get("title")
    elif node.kind == "change_event":
        summary["task_id"] = node.identity.get("task_id")
        summary["change_index"] = node.identity.get("index")
    return summary


def relationship_candidates_for_paths(
    snapshot: GraphSnapshot,
    paths: list[str] | set[str] | tuple[str, ...],
    *,
    source_fact_ids: dict[str, set[str]] | None = None,
    excluded_paths: set[str] | None = None,
) -> list[dict[str, Any]]:
    """Project unresolved typed relationships without promoting them to Graph edges."""
    normalized_excluded_paths = {
        normalized
        for path in (excluded_paths or set())
        if (normalized := normalize_repo_path(path))
    }
    wanted_paths = {
        normalized
        for path in paths
        if (normalized := normalize_repo_path(path)) and normalized not in normalized_excluded_paths
    }
    normalized_source_fact_ids = (
        {
            normalize_repo_path(path): {str(fact_id) for fact_id in fact_ids if str(fact_id)}
            for path, fact_ids in source_fact_ids.items()
            if normalize_repo_path(path)
        }
        if source_fact_ids is not None
        else None
    )
    candidates: list[dict[str, Any]] = []
    for node in snapshot.nodes:
        path = str(node.identity.get("path") or "")
        if node.kind != "file" or path not in wanted_paths:
            continue
        rpc = node.facts.get("rpc") if isinstance(node.facts.get("rpc"), dict) else {}
        source_facts = rpc.get("source_facts") if isinstance(rpc.get("source_facts"), list) else []
        facts_by_id = {
            str(item.get("fact_id") or ""): item
            for item in source_facts
            if isinstance(item, dict) and str(item.get("fact_id") or "")
        }
        resolutions = rpc.get("resolutions") if isinstance(rpc.get("resolutions"), list) else []
        for resolution in resolutions:
            if not isinstance(resolution, dict):
                continue
            fact_id_value = str(resolution.get("fact_id") or "")
            if normalized_source_fact_ids is not None and fact_id_value not in normalized_source_fact_ids.get(path, set()):
                continue
            fact = facts_by_id.get(fact_id_value)
            raw_targets = resolution.get("candidates") if isinstance(resolution.get("candidates"), list) else []
            if (
                fact is None
                or not raw_targets
                or resolution.get("candidate_compatibility") != "compatible"
                or resolution.get("outcome") == "linked"
            ):
                continue
            routine = fact.get("routine") if isinstance(fact.get("routine"), dict) else {}
            runtime_name = str(routine.get("value") or "") if routine.get("status") == "known" else ""
            if not runtime_name:
                continue
            anchor = fact.get("anchor") if isinstance(fact.get("anchor"), dict) else {}
            targets: list[dict[str, Any]] = []
            continuations: list[dict[str, Any]] = []
            seen_paths: set[str] = set()
            for raw_target in raw_targets:
                if not isinstance(raw_target, dict):
                    continue
                target_path = normalize_repo_path(str(raw_target.get("path") or ""))
                target_routine = str(raw_target.get("routine") or "")
                target_line = int(raw_target.get("line") or 0)
                if (
                    not target_path
                    or target_path in normalized_excluded_paths
                    or not target_routine
                    or target_line < 1
                ):
                    continue
                targets.append(
                    {
                        "identity": {"kind": "sql_routine", "value": target_routine},
                        "path": target_path,
                        "location": {"line": target_line},
                        "parameter_names": [str(value) for value in raw_target.get("parameter_names", []) if isinstance(value, str)],
                        "parameter_types": [str(value) for value in raw_target.get("parameter_types", []) if isinstance(value, str)],
                        "required_parameter_names": [
                            str(value)
                            for value in raw_target.get("required_parameter_names", [])
                            if isinstance(value, str)
                        ],
                    }
                )
                if target_path not in seen_paths and len(continuations) < 4:
                    seen_paths.add(target_path)
                    continuations.append(
                        {
                            "selector": {"kind": "file", "value": target_path},
                            "query_types": ["file", "impact_file"],
                            "actions": ["workspace.open", "graph.file", "graph.impact_file"],
                        }
                    )
            if not targets:
                continue
            targets.sort(key=lambda item: (str(item["path"]), int(item["location"]["line"]), str(item["identity"]["value"])))
            params = fact.get("params") if isinstance(fact.get("params"), dict) else {}
            candidates.append(
                {
                    "candidate_id": fact_id_value,
                    "kind": "sql_rpc_dependency",
                    "status": "non_authoritative",
                    "authoritative": False,
                    "source": {
                        "path": path,
                        "location": {
                            key: anchor[key]
                            for key in ("start_line", "start_col", "end_line", "end_col")
                            if key in anchor
                        },
                        "provider": str(fact.get("provider") or ""),
                        "runtime_identity": {"kind": "rpc_routine", "value": runtime_name},
                        "parameter_names": [str(value) for value in params.get("known_names", []) if isinstance(value, str)],
                    },
                    "resolution": {
                        "outcome": str(resolution.get("outcome") or "incomplete"),
                        "reason_code": str(resolution.get("reason_code") or "relationship_evidence_incomplete"),
                    },
                    "targets": targets,
                    "target_count": len(targets),
                    "targets_truncated": False,
                    "continuations": continuations,
                }
            )
    return sorted(
        candidates,
        key=lambda item: (
            str((item.get("source") or {}).get("path") or ""),
            int(((item.get("source") or {}).get("location") or {}).get("start_line") or 0),
            str(item.get("candidate_id") or ""),
        ),
    )


def compact_relationship_candidates(
    candidates: Any,
    *,
    candidate_limit: int = 3,
    target_limit: int = 3,
) -> dict[str, Any]:
    values = [item for item in candidates if isinstance(item, dict)] if isinstance(candidates, list) else []
    items: list[dict[str, Any]] = []
    for candidate in values[:candidate_limit]:
        raw_targets = candidate.get("targets") if isinstance(candidate.get("targets"), list) else []
        targets = [target for target in raw_targets[:target_limit] if isinstance(target, dict)]
        visible_paths = {str(target.get("path") or "") for target in targets if str(target.get("path") or "")}
        raw_continuations = candidate.get("continuations") if isinstance(candidate.get("continuations"), list) else []
        continuations = [
            continuation
            for continuation in raw_continuations
            if isinstance(continuation, dict)
            and isinstance(continuation.get("selector"), dict)
            and str(continuation["selector"].get("value") or "") in visible_paths
        ][:target_limit]
        items.append(
            {
                **candidate,
                "targets": targets,
                "target_count": len(raw_targets),
                "targets_truncated": len(raw_targets) > len(targets),
                "continuations": continuations,
            }
        )
    return {
        "items": items,
        "total_count": len(values),
        "truncated": len(values) > len(items),
    }


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
    matches: list[GraphNode] = []
    for node in snapshot.nodes:
        if node.kind != "symbol":
            continue
        provider = node.facts.get("provider") if isinstance(node.facts.get("provider"), dict) else {}
        if selector not in {str(provider.get("name") or ""), str(provider.get("qualified_name") or "")}:
            continue
        if in_file:
            file_node = nodes.get(_symbol_file_id(snapshot, node.id))
            if file_node is None or file_node.identity.get("path") != in_file:
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
    for problem in snapshot.completeness.get("receipt_problems", []):
        if not isinstance(problem, dict):
            continue
        warning = {
            "code": str(problem.get("code") or "invalid_completion_receipt"),
            "message": str(problem.get("message") or "completion receipt is invalid"),
        }
        if problem.get("path"):
            warning["path"] = str(problem["path"])
        warnings.append(warning)
    for failure in snapshot.completeness.get("provider_failures", []):
        if isinstance(failure, dict):
            warnings.append(
                {
                    "code": str(failure.get("code") or "graph_provider_failure"),
                    "message": str(failure.get("message") or "semantic provider failed"),
                }
            )
        else:
            warnings.append({"code": "graph_provider_failure", "message": str(failure)})
    coverage = snapshot.completeness.get("provider_coverage")
    if isinstance(coverage, dict):
        incomplete = {
            name: value.get("status")
            for name, value in sorted(coverage.items())
            if isinstance(value, dict) and value.get("status") != "complete"
        }
        if incomplete:
            warnings.append(
                {
                    "code": "graph_provider_coverage",
                    "message": f"semantic provider coverage is incomplete: {incomplete}",
                }
            )
    return warnings


def _path_from_edge(
    nodes: dict[str, GraphNode],
    edge: GraphEdge,
    *,
    reason: str,
    symbol_paths: dict[str, str],
) -> dict[str, Any]:
    source: dict[str, Any] = {"provider": edge.source, "assertion": edge.assertion}
    if edge.facts:
        source["facts"] = edge.facts
    return {
        "from": _node_summary(nodes.get(edge.from_id), symbol_paths=symbol_paths),
        "edge": edge.kind,
        "to": _node_summary(nodes.get(edge.to_id), symbol_paths=symbol_paths),
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


def _file_neighborhood_edges(snapshot: GraphSnapshot, file_node_id: str) -> list[GraphEdge]:
    direct = [edge for edge in snapshot.edges if edge.from_id == file_node_id or edge.to_id == file_node_id]
    symbol_ids = {
        edge.to_id
        for edge in snapshot.edges
        if edge.kind == "DEFINES" and edge.from_id == file_node_id
    }
    call_edges = [
        edge
        for edge in snapshot.edges
        if edge.kind == "CALLS" and (edge.from_id in symbol_ids or edge.to_id in symbol_ids)
    ]
    related_entity_ids = {
        edge.from_id if edge.to_id == file_node_id else edge.to_id
        for edge in direct
        if edge.kind in {"TASK_CHANGED_FILE", "KNOWLEDGE_APPLIES_TO"}
    }
    second_hop = [
        edge
        for edge in snapshot.edges
        if edge.from_id in related_entity_ids or edge.to_id in related_entity_ids
    ]
    definition_ids = {file_node_id, *symbol_ids, *(edge.from_id for edge in call_edges), *(edge.to_id for edge in call_edges)}
    definitions = _definition_edges(snapshot, definition_ids)
    return list({_edge_key(edge): edge for edge in [*direct, *call_edges, *second_hop, *definitions]}.values())


def _file_query_path_edges(snapshot: GraphSnapshot, edges: list[GraphEdge], file_node_id: str) -> list[tuple[GraphEdge, str]]:
    symbol_ids = {
        edge.to_id
        for edge in snapshot.edges
        if edge.kind == "DEFINES" and edge.from_id == file_node_id
    }
    direct_kinds = {"IMPORTS_FILE", "TESTS_FILE", STRUCTURED_EDGE_KIND, "TASK_CHANGED_FILE", "KNOWLEDGE_APPLIES_TO"}
    selected: list[tuple[GraphEdge, str]] = []
    for edge in edges:
        if edge.kind in direct_kinds and file_node_id in {edge.from_id, edge.to_id}:
            selected.append((edge, "direct relation to selected file"))
        elif edge.kind == "CALLS" and (edge.from_id in symbol_ids or edge.to_id in symbol_ids):
            selected.append((edge, "call relation involving a symbol defined by selected file"))
    return selected


@dataclass(frozen=True)
class _ContextEdgePolicy:
    inbound: bool
    outbound: bool
    max_depth: int


_CONTEXT_GRAPH_MODE_POLICIES: dict[str, dict[str, _ContextEdgePolicy]] = {
    "auto": {
        "CALLS": _ContextEdgePolicy(inbound=True, outbound=True, max_depth=1),
        "IMPORTS_FILE": _ContextEdgePolicy(inbound=True, outbound=True, max_depth=1),
        "TESTS_FILE": _ContextEdgePolicy(inbound=True, outbound=True, max_depth=1),
        STRUCTURED_EDGE_KIND: _ContextEdgePolicy(inbound=True, outbound=True, max_depth=1),
    },
    "code_location": {
        "CALLS": _ContextEdgePolicy(inbound=False, outbound=True, max_depth=1),
        "IMPORTS_FILE": _ContextEdgePolicy(inbound=False, outbound=True, max_depth=1),
        "TESTS_FILE": _ContextEdgePolicy(inbound=True, outbound=True, max_depth=1),
        STRUCTURED_EDGE_KIND: _ContextEdgePolicy(inbound=False, outbound=True, max_depth=1),
    },
    "call_impact": {
        "CALLS": _ContextEdgePolicy(inbound=True, outbound=True, max_depth=2),
        "TESTS_FILE": _ContextEdgePolicy(inbound=True, outbound=True, max_depth=1),
    },
    "file_impact": {
        "CALLS": _ContextEdgePolicy(inbound=True, outbound=True, max_depth=1),
        "IMPORTS_FILE": _ContextEdgePolicy(inbound=True, outbound=True, max_depth=2),
        "TESTS_FILE": _ContextEdgePolicy(inbound=True, outbound=True, max_depth=1),
        STRUCTURED_EDGE_KIND: _ContextEdgePolicy(inbound=True, outbound=True, max_depth=2),
    },
}


@dataclass(frozen=True)
class _ContextProjectionIndex:
    nodes: dict[str, GraphNode]
    symbols_by_file: dict[str, list[str]]
    symbol_files: dict[str, str]
    anchor_nodes_by_symbol: dict[str, GraphNode]
    file_relations_by_file: dict[str, list[tuple[GraphEdge, str, str, str, str]]]
    changes_by_file: dict[str, list[str]]
    tasks_by_change: dict[str, list[tuple[str, GraphEdge]]]
    artifact_paths_by_task: dict[str, set[str]]


def _build_context_projection_index(snapshot: GraphSnapshot) -> _ContextProjectionIndex:
    nodes = _node_by_id(snapshot)
    symbols_by_file: dict[str, list[str]] = {}
    symbol_files: dict[str, str] = {}
    anchor_nodes_by_symbol: dict[str, GraphNode] = {}
    file_relations: list[tuple[GraphEdge, str, str, str, str]] = []
    call_edges: list[GraphEdge] = []
    changes_by_file: dict[str, list[str]] = {}
    tasks_by_change: dict[str, list[tuple[str, GraphEdge]]] = {}
    artifact_paths_by_task: dict[str, set[str]] = {}

    for edge in snapshot.edges:
        if edge.kind == "DEFINES":
            symbols_by_file.setdefault(edge.from_id, []).append(edge.to_id)
            symbol_files[edge.to_id] = edge.from_id
        elif edge.kind == "ANCHORS" and edge.to_id in nodes:
            anchor_nodes_by_symbol[edge.from_id] = nodes[edge.to_id]
        elif edge.kind in {"IMPORTS_FILE", "TESTS_FILE", STRUCTURED_EDGE_KIND}:
            file_relations.append((edge, edge.from_id, edge.to_id, "", ""))
        elif edge.kind == "CALLS":
            call_edges.append(edge)
        elif edge.kind == "CHANGE_AFFECTED_FILE":
            changes_by_file.setdefault(edge.to_id, []).append(edge.from_id)
        elif edge.kind == "TASK_RECORDED_CHANGE":
            tasks_by_change.setdefault(edge.to_id, []).append((edge.from_id, edge))
        elif edge.kind == "TASK_VERIFIED_BY":
            artifact_node = nodes.get(edge.to_id)
            artifact_path = str(artifact_node.identity.get("path") or "") if artifact_node is not None else ""
            if artifact_node is not None and artifact_node.kind == "artifact" and artifact_path:
                artifact_paths_by_task.setdefault(edge.from_id, set()).add(artifact_path)

    for edge in call_edges:
        from_file_id = symbol_files.get(edge.from_id, "")
        to_file_id = symbol_files.get(edge.to_id, "")
        if from_file_id and to_file_id:
            file_relations.append((edge, from_file_id, to_file_id, edge.from_id, edge.to_id))

    file_relations_by_file: dict[str, list[tuple[GraphEdge, str, str, str, str]]] = {}
    for relation in file_relations:
        _edge, from_file_id, to_file_id, _from_symbol_id, _to_symbol_id = relation
        file_relations_by_file.setdefault(from_file_id, []).append(relation)
        if to_file_id != from_file_id:
            file_relations_by_file.setdefault(to_file_id, []).append(relation)
    for relations in file_relations_by_file.values():
        relations.sort(key=lambda item: _edge_key(item[0]))

    return _ContextProjectionIndex(
        nodes=nodes,
        symbols_by_file=symbols_by_file,
        symbol_files=symbol_files,
        anchor_nodes_by_symbol=anchor_nodes_by_symbol,
        file_relations_by_file=file_relations_by_file,
        changes_by_file=changes_by_file,
        tasks_by_change=tasks_by_change,
        artifact_paths_by_task=artifact_paths_by_task,
    )


def _context_anchor_symbol_ids(
    *,
    repo_id: str,
    index: _ContextProjectionIndex,
    anchors: list[GraphContextAnchor],
) -> dict[str, set[str]]:
    resolved: dict[str, set[str]] = {}
    for anchor in anchors:
        if anchor.kind != GraphContextAnchorKind.SYMBOL:
            continue
        current_file_id = file_id(repo_id, anchor.path)
        for symbol_node_id in index.symbols_by_file.get(current_file_id, []):
            symbol_node = index.nodes.get(symbol_node_id)
            source_anchor = index.anchor_nodes_by_symbol.get(symbol_node_id)
            if symbol_node is None or source_anchor is None:
                continue
            provider = symbol_node.facts.get("provider") if isinstance(symbol_node.facts.get("provider"), dict) else {}
            label = str(provider.get("qualified_name") or provider.get("name") or "")
            if anchor.symbol and label != anchor.symbol:
                continue
            if str(source_anchor.identity.get("path") or "") != anchor.path:
                continue
            if anchor.line_start and int(source_anchor.identity.get("start_line") or 0) != anchor.line_start:
                continue
            if anchor.line_end and int(source_anchor.identity.get("end_line") or 0) != anchor.line_end:
                continue
            resolved.setdefault(current_file_id, set()).add(symbol_node_id)
    return resolved


def project_context_neighborhood(
    snapshot: GraphSnapshot,
    *,
    anchors: list[GraphContextAnchor],
    mode: str,
    max_relations: int = 128,
    max_history: int = 5,
    excluded_candidate_paths: set[str] | None = None,
) -> dict[str, Any]:
    """Project bounded, mode-specific relations around typed Context anchors."""
    repo_id = str(snapshot.repository.get("id") or "")
    index = _build_context_projection_index(snapshot)
    nodes = index.nodes
    mode_policy = _CONTEXT_GRAPH_MODE_POLICIES.get(mode)
    if mode_policy is None:
        raise ValueError(f"unsupported Context Graph projection mode: {mode}")
    normalized_anchors: list[GraphContextAnchor] = []
    unresolved_anchors: list[GraphContextAnchor] = []
    seen_anchor_keys: set[tuple[str, str, str, int, int]] = set()
    for raw_anchor in anchors:
        normalized = normalize_repo_path(raw_anchor.path)
        anchor = GraphContextAnchor(
            kind=raw_anchor.kind,
            path=normalized or raw_anchor.path,
            symbol=raw_anchor.symbol,
            line_start=raw_anchor.line_start,
            line_end=raw_anchor.line_end,
        )
        if anchor.key() in seen_anchor_keys:
            continue
        seen_anchor_keys.add(anchor.key())
        if not normalized or file_id(repo_id, normalized) not in nodes:
            unresolved_anchors.append(anchor)
            continue
        normalized_anchors.append(anchor)
    anchored_symbol_ids = _context_anchor_symbol_ids(
        repo_id=repo_id,
        index=index,
        anchors=normalized_anchors,
    )
    resolved_anchors: list[GraphContextAnchor] = []
    ambiguous_anchors: list[GraphContextAnchor] = []
    normalized_seeds: list[str] = []
    for anchor in normalized_anchors:
        current_file_id = file_id(repo_id, anchor.path)
        if anchor.kind == GraphContextAnchorKind.SYMBOL:
            symbol_count = len(anchored_symbol_ids.get(current_file_id, set()))
            if symbol_count == 0:
                unresolved_anchors.append(anchor)
                continue
            if symbol_count > 1:
                ambiguous_anchors.append(anchor)
                continue
        resolved_anchors.append(anchor)
        if anchor.path not in normalized_seeds:
            normalized_seeds.append(anchor.path)
    seed_ids = {file_id(repo_id, path) for path in normalized_seeds}

    symbol_paths = {
        symbol_id_value: str(nodes[file_node_id].identity.get("path") or "")
        for symbol_id_value, file_node_id in index.symbol_files.items()
        if file_node_id in nodes and str(nodes[file_node_id].identity.get("path") or "")
    }

    relations_by_key: dict[tuple[str, str, str, str, str], dict[str, Any]] = {}
    related_paths: set[str] = set()

    def add_relation(
        *,
        edge: GraphEdge,
        from_file_id: str,
        to_file_id: str,
        from_symbol_id: str = "",
        to_symbol_id: str = "",
        distance: int,
        origin_file_distances: dict[str, int],
    ) -> bool:
        from_node = nodes.get(from_file_id)
        to_node = nodes.get(to_file_id)
        if from_node is None or to_node is None:
            return False
        from_path = str(from_node.identity.get("path") or "")
        to_path = str(to_node.identity.get("path") or "")
        if not from_path or not to_path:
            return False
        key = _edge_key(edge)
        if from_file_id not in seed_ids:
            related_paths.add(from_path)
        if to_file_id not in seed_ids:
            related_paths.add(to_path)
        origin_distances = {
            str(nodes[origin_id].identity.get("path") or ""): max(1, int(origin_distance))
            for origin_id, origin_distance in origin_file_distances.items()
            if origin_id in nodes and str(nodes[origin_id].identity.get("path") or "")
        }
        existing = relations_by_key.get(key)
        if existing is not None:
            merged_origin_distances = {
                str(path): max(1, int(origin_distance))
                for path, origin_distance in (
                    existing.get("origin_distances")
                    if isinstance(existing.get("origin_distances"), dict)
                    else {}
                ).items()
            }
            for path, origin_distance in origin_distances.items():
                merged_origin_distances[path] = min(
                    merged_origin_distances.get(path, origin_distance),
                    origin_distance,
                )
            existing["origin_paths"] = sorted(merged_origin_distances)
            existing["origin_distances"] = {
                path: merged_origin_distances[path] for path in sorted(merged_origin_distances)
            }
            existing["distance"] = min(int(existing.get("distance") or distance), distance)
            return True
        if len(relations_by_key) >= max_relations:
            return False
        relation: dict[str, Any] = {
            "from_path": from_path,
            "edge": edge.kind,
            "to_path": to_path,
            "from_id": edge.from_id,
            "to_id": edge.to_id,
            "assertion": edge.assertion,
            "provider": edge.source,
            "distance": distance,
            "origin_paths": sorted(origin_distances),
            "origin_distances": {
                path: origin_distances[path] for path in sorted(origin_distances)
            },
        }
        if edge.facts:
            relation["facts"] = edge.facts
        if from_symbol_id:
            relation["from_symbol"] = _node_summary(nodes.get(from_symbol_id), symbol_paths=symbol_paths)
        if to_symbol_id:
            relation["to_symbol"] = _node_summary(nodes.get(to_symbol_id), symbol_paths=symbol_paths)
        relations_by_key[key] = relation
        return True

    frontier: dict[str, set[str]] = {seed_id: {seed_id} for seed_id in seed_ids}
    seen_origins_by_file: dict[str, set[str]] = {
        seed_id: {seed_id} for seed_id in seed_ids
    }
    relation_priority = {"TESTS_FILE": 0, "IMPORTS_FILE": 1, STRUCTURED_EDGE_KIND: 2, "CALLS": 3}
    max_depth = max((policy.max_depth for policy in mode_policy.values()), default=0)
    for distance in range(1, max_depth + 1):
        candidate_relations: dict[
            tuple[str, str, str, str, str],
            tuple[GraphEdge, str, str, str, str],
        ] = {}
        for frontier_file_id in frontier:
            for relation in index.file_relations_by_file.get(frontier_file_id, []):
                candidate_relations.setdefault(_edge_key(relation[0]), relation)

        eligible: list[
            tuple[GraphEdge, str, str, str, str, set[str], set[str]]
        ] = []
        for relation in candidate_relations.values():
            edge, from_file_id, to_file_id, from_symbol_id, to_symbol_id = relation
            edge_policy = mode_policy.get(edge.kind)
            if edge_policy is None or distance > edge_policy.max_depth:
                continue
            outbound_origins = (
                set(frontier.get(from_file_id, set())) if edge_policy.outbound else set()
            )
            inbound_origins = (
                set(frontier.get(to_file_id, set())) if edge_policy.inbound else set()
            )
            if distance == 1:
                if edge.kind == "CALLS":
                    if outbound_origins and anchored_symbol_ids.get(from_file_id) and from_symbol_id not in anchored_symbol_ids[from_file_id]:
                        outbound_origins.clear()
                    if inbound_origins and anchored_symbol_ids.get(to_file_id) and to_symbol_id not in anchored_symbol_ids[to_file_id]:
                        inbound_origins.clear()
                elif edge.kind in {"IMPORTS_FILE", STRUCTURED_EDGE_KIND}:
                    if outbound_origins and anchored_symbol_ids.get(from_file_id):
                        outbound_origins.clear()
                    if inbound_origins and anchored_symbol_ids.get(to_file_id):
                        inbound_origins.clear()
            if outbound_origins or inbound_origins:
                eligible.append((*relation, outbound_origins, inbound_origins))
        eligible.sort(
            key=lambda item: (
                0 if item[5] else 1,
                relation_priority.get(item[0].kind, 9),
                0 if item[1] == item[2] else 1,
                str(nodes.get(item[1]).identity.get("path") if nodes.get(item[1]) is not None else ""),
                str(nodes.get(item[2]).identity.get("path") if nodes.get(item[2]) is not None else ""),
                item[0].from_id,
                item[0].to_id,
            )
        )
        next_frontier: dict[str, set[str]] = {}
        for edge, from_file_id, to_file_id, from_symbol_id, to_symbol_id, outbound_origins, inbound_origins in eligible:
            relation_origins = outbound_origins | inbound_origins
            accepted = add_relation(
                edge=edge,
                from_file_id=from_file_id,
                to_file_id=to_file_id,
                from_symbol_id=from_symbol_id,
                to_symbol_id=to_symbol_id,
                distance=distance,
                origin_file_distances={origin_id: distance for origin_id in relation_origins},
            )
            if not accepted:
                continue
            for destination_file_id, propagating_origins in (
                (from_file_id, inbound_origins),
                (to_file_id, outbound_origins),
            ):
                seen_origins = seen_origins_by_file.setdefault(destination_file_id, set())
                new_origins = propagating_origins - seen_origins
                if not new_origins:
                    continue
                seen_origins.update(new_origins)
                next_frontier.setdefault(destination_file_id, set()).update(new_origins)
        frontier = next_frontier
        if not frontier:
            break

    relations = sorted(
        relations_by_key.values(),
        key=lambda item: (
            int(item.get("distance") or 0),
            relation_priority.get(str(item.get("edge") or ""), 9),
            0 if item.get("from_path") == item.get("to_path") else 1,
            str(item.get("from_path") or ""),
            str(item.get("to_path") or ""),
            str(item.get("from_id") or ""),
            str(item.get("to_id") or ""),
        ),
    )[:max_relations]
    visible_paths = set(normalized_seeds)
    for relation in relations:
        visible_paths.add(str(relation.get("from_path") or ""))
        visible_paths.add(str(relation.get("to_path") or ""))

    visible_file_ids = {file_id(repo_id, path) for path in visible_paths if path}
    changes_by_file = {
        current_file_id: index.changes_by_file[current_file_id]
        for current_file_id in visible_file_ids
        if current_file_id in index.changes_by_file
    }

    history_by_task: dict[str, list[dict[str, Any]]] = {}
    seen_history: set[tuple[str, str]] = set()
    for current_file_id in sorted(changes_by_file):
        file_node = nodes.get(current_file_id)
        path = str(file_node.identity.get("path") or "") if file_node is not None else ""
        for change_id in sorted(changes_by_file[current_file_id]):
            change_node = nodes.get(change_id)
            change_facts = change_node.facts.get("receipt") if change_node is not None and isinstance(change_node.facts.get("receipt"), dict) else {}
            for task_node_id, task_edge in sorted(index.tasks_by_change.get(change_id, []), key=lambda item: item[0]):
                task_node = nodes.get(task_node_id)
                if task_node is None:
                    continue
                task_id_value = str(task_node.identity.get("task_id") or "")
                key = (task_id_value, path)
                if not task_id_value or not path or key in seen_history:
                    continue
                seen_history.add(key)
                receipt = task_node.facts.get("receipt") if isinstance(task_node.facts.get("receipt"), dict) else {}
                artifact_paths = index.artifact_paths_by_task.get(task_node_id, set())
                history_by_task.setdefault(task_id_value, []).append(
                    {
                        "task_id": task_id_value,
                        "path": path,
                        "change": str(change_facts.get("change") or ""),
                        "attribution": str(task_edge.facts.get("attribution") or change_facts.get("attribution") or ""),
                        "completed_at": str(receipt.get("completed_at") or ""),
                        "task_path": next(iter(artifact_paths)) if len(artifact_paths) == 1 else "",
                    }
                )
    selected_task_ids = sorted(
        history_by_task,
        key=lambda task_id: (
            max(str(item.get("completed_at") or "") for item in history_by_task[task_id]),
            task_id,
        ),
        reverse=True,
    )[:max_history]
    history = [
        item
        for task_id in selected_task_ids
        for item in sorted(history_by_task[task_id], key=lambda value: str(value.get("path") or ""))
    ]
    return {
        "mode": mode,
        "policy": {
            edge_kind: {
                "inbound": edge_policy.inbound,
                "outbound": edge_policy.outbound,
                "max_depth": edge_policy.max_depth,
            }
            for edge_kind, edge_policy in sorted(mode_policy.items())
        },
        "seed_paths": normalized_seeds,
        "seed_anchors": [anchor.to_dict() for anchor in resolved_anchors],
        "unresolved_anchors": [anchor.to_dict() for anchor in unresolved_anchors],
        "ambiguous_anchors": [anchor.to_dict() for anchor in ambiguous_anchors],
        "related_paths": sorted(path for path in related_paths if path in visible_paths),
        "relations": relations,
        "relationship_candidates": relationship_candidates_for_paths(
            snapshot,
            normalized_seeds,
            excluded_paths=excluded_candidate_paths,
        ),
        "history": history,
    }


def _query_payload(
    snapshot: GraphSnapshot,
    *,
    query: dict[str, Any],
    node_ids: set[str],
    edges: list[GraphEdge],
    matches: list[dict[str, Any]] | None = None,
    paths: list[dict[str, Any]] | None = None,
    warnings: list[dict[str, str]] | None = None,
    candidates: list[dict[str, str]] | None = None,
    query_status: str = "found",
    excluded_candidate_paths: set[str] | None = None,
) -> dict[str, Any]:
    nodes = _node_by_id(snapshot)
    for edge in edges:
        node_ids.add(edge.from_id)
        node_ids.add(edge.to_id)
    sorted_edges = sorted({_edge_key(edge): edge for edge in edges}.values(), key=_edge_key)
    match_paths = {
        normalize_repo_path(str(match.get("path") or ""))
        for match in (matches or [])
        if isinstance(match, dict) and normalize_repo_path(str(match.get("path") or ""))
    }
    relationship_candidates = relationship_candidates_for_paths(
        snapshot,
        match_paths,
        excluded_paths=excluded_candidate_paths,
    )
    payload = {
        "repository": snapshot.repository,
        "snapshot_digest": snapshot.snapshot_digest,
        "query": query,
        "query_status": query_status,
        "matches": matches or [],
        "candidates": candidates or [],
        "relationship_candidates": relationship_candidates,
        "relationship_candidate_count": len(relationship_candidates),
        "relationship_candidates_truncated": False,
        "nodes": [nodes[node_id].to_dict() for node_id in sorted(node_ids) if node_id in nodes],
        "edges": [edge.to_dict() for edge in sorted_edges],
        "paths": paths or [],
        "continuations": _query_continuations(snapshot, node_ids),
        "completeness": snapshot.completeness,
        "warnings": warnings or _query_warnings(snapshot),
    }
    payload["result_digest"] = digest_data(
        {
            "schema": "repoctl.graph.query-result",
            "snapshot_digest": snapshot.snapshot_digest,
            "query": payload["query"],
            "query_status": payload["query_status"],
            "matches": payload["matches"],
            "candidates": payload["candidates"],
            "relationship_candidates": payload["relationship_candidates"],
            "paths": payload["paths"],
        }
    )
    return payload


def _query_continuations(snapshot: GraphSnapshot, node_ids: set[str]) -> list[dict[str, Any]]:
    nodes = _node_by_id(snapshot)
    continuations: list[dict[str, Any]] = []
    for node_id in sorted(node_ids):
        node = nodes.get(node_id)
        if node is None:
            continue
        selector: dict[str, str] = {}
        query_types: list[str] = []
        actions: list[str] = []
        label = ""
        if node.kind == "file":
            value = str(node.identity.get("path") or "")
            if not value:
                continue
            selector = {"kind": "file", "value": value}
            query_types = ["file", "impact_file"]
            actions = ["workspace.open", "graph.file", "graph.impact_file"]
            label = value
        elif node.kind == "symbol":
            match = _symbol_match_dict(snapshot, node)
            value = str(match.get("qualified_name") or match.get("name") or "")
            if not value:
                continue
            selector = {"kind": "symbol", "value": value}
            path = str(match.get("path") or "")
            if path:
                selector["in_file"] = path
            query_types = ["symbol"]
            capabilities = snapshot.completeness.get("capabilities") if isinstance(snapshot.completeness.get("capabilities"), dict) else {}
            if capabilities.get("calls") not in {"unsupported", "unavailable"}:
                query_types.extend(["callers_of", "callees_of", "impact_symbol"])
            actions = [f"graph.{query_type}" for query_type in query_types]
            label = value
        elif node.kind == "import_ref":
            value = str(node.identity.get("raw_import") or "")
            if not value:
                continue
            selector = {"kind": "import", "value": value}
            query_types = ["import"]
            actions = ["graph.import"]
            label = value
        elif node.kind == "topic":
            value = str(node.identity.get("topic") or "")
            if not value:
                continue
            selector = {"kind": "topic", "value": value}
            query_types = ["topic"]
            actions = ["graph.topic"]
            label = value
        elif node.kind == "task":
            value = str(node.identity.get("task_id") or "")
            if not value:
                continue
            selector = {"kind": "task", "value": value}
            query_types = ["task"]
            actions = ["graph.task", "task.show"]
            label = value
        elif node.kind == "artifact":
            value = str(node.identity.get("path") or "")
            if not value:
                continue
            selector = {"kind": "document", "value": value}
            query_types = ["artifact"]
            actions = ["graph.artifact", "workspace.open"]
            label = value
        elif node.kind == "document":
            value = str(node.identity.get("path") or "")
            if not value:
                continue
            selector = {"kind": "document", "value": value}
            query_types = ["document"]
            actions = ["workspace.open"]
            label = value
        elif node.kind == "knowledge":
            value = str(node.identity.get("record_id") or "")
            if not value:
                continue
            selector = {"kind": "knowledge_record", "value": value}
            query_types = ["knowledge_record"]
            actions = ["knowledge.show"]
            label = value
        elif node.kind == "change_event":
            value = str(node.identity.get("task_id") or "")
            if not value:
                continue
            selector = {"kind": "task", "value": value}
            query_types = ["task"]
            actions = ["graph.task"]
            label = value
        else:
            continue
        continuations.append(
            {
                "node_id": node.id,
                "node_kind": node.kind,
                "label": label,
                "selector": selector,
                "query_types": query_types,
                "actions": actions,
            }
        )
    unique: dict[tuple[str, str, str], dict[str, Any]] = {}
    for item in continuations:
        key = (
            item["selector"]["kind"],
            item["selector"]["value"],
            item["selector"].get("in_file", ""),
        )
        existing = unique.get(key)
        if existing is None:
            unique[key] = item
            continue
        existing["query_types"] = sorted(set(existing["query_types"]) | set(item["query_types"]))
        existing["actions"] = sorted(set(existing["actions"]) | set(item["actions"]))
    return [unique[key] for key in sorted(unique)]


def _empty_query_payload(
    snapshot: GraphSnapshot,
    *,
    query: dict[str, Any],
    query_status: str,
    warning: dict[str, str] | None = None,
    candidates: list[dict[str, str]] | None = None,
    excluded_candidate_paths: set[str] | None = None,
) -> dict[str, Any]:
    warnings = _query_warnings(snapshot)
    if warning is not None:
        warnings.append(warning)
    return _query_payload(
        snapshot,
        query=query,
        node_ids=set(),
        edges=[],
        warnings=warnings,
        candidates=candidates,
        query_status=query_status,
        excluded_candidate_paths=excluded_candidate_paths,
    )


def _selector_candidates(resolution: RepoSelectorResolution, *, repository_path: str) -> list[dict[str, str]]:
    return [
        {
            "path": path,
            "workspace_path": f"{repository_path}/{path}" if repository_path else path,
            "reason": "selector matches both repo-relative and workspace-relative identities",
        }
        for path in resolution.candidates
    ]


def _canonical_path_candidates(snapshot: GraphSnapshot, requested: str, *, limit: int = 3) -> list[dict[str, str]]:
    requested_name = requested.casefold().rsplit("/", 1)[-1]
    requested_lower = requested.casefold()
    repository_path = str(snapshot.repository.get("path") or "").rstrip("/")
    ranked: list[tuple[tuple[int, int, str], str]] = []
    for node in snapshot.nodes:
        if node.kind != "file":
            continue
        path = str(node.identity.get("path") or "")
        if not path:
            continue
        lowered = path.casefold()
        name = lowered.rsplit("/", 1)[-1]
        same_name = name == requested_name
        suffix_related = lowered.endswith("/" + requested_lower) or requested_lower.endswith("/" + lowered)
        if not same_name and not suffix_related:
            continue
        score = (
            0 if same_name else 1,
            abs(len(path) - len(requested)),
            path,
        )
        ranked.append((score, path))
    return [
        {
            "path": path,
            "workspace_path": f"{repository_path}/{path}" if repository_path else path,
            "reason": "canonical indexed path candidate",
        }
        for _score, path in sorted(ranked)[:limit]
    ]


def _resolve_graph_query_path(
    snapshot: GraphSnapshot,
    *,
    value: str,
    repository_path: str,
    known_paths: set[str],
    query_base: dict[str, Any],
    query_path_field: str,
    invalid_message: str,
    ambiguous_message: str,
) -> tuple[str, dict[str, Any] | None, list[Problem]]:
    resolution = resolve_repo_selector_path(value, repository_path=repository_path, known_paths=known_paths)
    if resolution.status == RepoSelectorStatus.INVALID:
        return "", None, [Problem("error", "graph_query_invalid_path", invalid_message)]

    query = dict(query_base)
    query[query_path_field] = resolution.path if resolution.status == RepoSelectorStatus.NOT_FOUND else value
    if resolution.status == RepoSelectorStatus.AMBIGUOUS:
        return "", _empty_query_payload(
            snapshot,
            query=query,
            query_status="ambiguous",
            candidates=_selector_candidates(resolution, repository_path=repository_path),
        ), [Problem("error", "graph_query_ambiguous_path", ambiguous_message)]
    if resolution.status == RepoSelectorStatus.NOT_FOUND:
        return "", _empty_query_payload(
            snapshot,
            query=query,
            query_status="not_found",
            candidates=_canonical_path_candidates(snapshot, resolution.path),
        ), []
    return resolution.path, None, []


def _semantic_query_status(snapshot: GraphSnapshot, *, capability_name: str, in_file: str) -> tuple[str, dict[str, str] | None]:
    provider_coverage = snapshot.completeness.get("provider_coverage")
    provider_coverage = provider_coverage if isinstance(provider_coverage, dict) else {}
    capability_coverage = provider_coverage.get(capability_name)
    capability_coverage = capability_coverage if isinstance(capability_coverage, dict) else {}
    if in_file:
        file_node = _node_by_id(snapshot).get(file_id(str(snapshot.repository.get("id") or ""), in_file))
        index = file_node.facts.get("index") if file_node is not None and isinstance(file_node.facts.get("index"), dict) else {}
        if index.get("classification") == "excluded":
            return "unsupported", {
                "code": "graph_query_unsupported",
                "message": f"semantic analysis is excluded by policy for {in_file}",
            }
        analyzed_paths = {str(path) for path in capability_coverage.get("analyzed_paths", [])}
        failed_paths = {str(path) for path in capability_coverage.get("failed_paths", [])}
        unsupported_paths = {str(path) for path in capability_coverage.get("unsupported_paths", [])}
        if in_file in analyzed_paths:
            return "found", None
        if in_file in failed_paths:
            return "unavailable", {
                "code": "graph_query_unavailable",
                "message": f"the {capability_name} provider could not analyze {in_file}",
            }
        if in_file in unsupported_paths:
            language = language_for_path(in_file)
            return "unsupported", {
                "code": "graph_query_unsupported",
                "message": f"provider-confirmed {capability_name} queries are unsupported for {language}",
            }
        return "unsupported", {
            "code": "graph_query_unsupported",
            "message": f"{in_file} is not eligible for provider-confirmed {capability_name} queries",
        }
    capability_statuses = snapshot.completeness.get("capabilities")
    capability_statuses = capability_statuses if isinstance(capability_statuses, dict) else {}
    coverage_status = str(capability_statuses.get(capability_name) or "")
    if coverage_status == "unsupported":
        return "unsupported", {
            "code": "graph_query_unsupported",
            "message": f"no provider-confirmed {capability_name} capability is available for the indexed source files",
        }
    if coverage_status == "unavailable":
        return "unavailable", {
            "code": "graph_query_unavailable",
            "message": f"the {capability_name} provider could not analyze the indexed source files",
        }
    return "found", None


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
    task: str = "",
    artifact: str = "",
    in_file: str = "",
    depth: int = 1,
    stale_paths: set[str] | None = None,
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
            ("task", task),
            ("artifact", artifact),
        )
        if value
    ]
    if not selectors:
        return None, [
            Problem(
                "error",
                "graph_query_selector_required",
                "pass exactly one graph query selector: --file, --topic, --import, --symbol, --callers-of, --callees-of, --impact-file, --impact-symbol, --task, or --artifact",
            )
        ]
    if len(selectors) > 1:
        return None, [Problem("error", "graph_query_selector_ambiguous", "pass only one graph query selector")]
    if depth < 1:
        return None, [Problem("error", "graph_query_invalid_depth", "graph query depth must be at least 1")]

    normalized_stale_paths = {
        normalized
        for path in (stale_paths or set())
        if (normalized := normalize_repo_path(path))
    }

    def query_payload(**kwargs: Any) -> dict[str, Any]:
        return _query_payload(
            snapshot,
            excluded_candidate_paths=normalized_stale_paths,
            **kwargs,
        )

    def empty_query_payload(**kwargs: Any) -> dict[str, Any]:
        return _empty_query_payload(
            snapshot,
            excluded_candidate_paths=normalized_stale_paths,
            **kwargs,
        )

    repo_id = str(snapshot.repository.get("id") or "")
    repository_path = str(snapshot.repository.get("path") or "")
    selector, value = selectors[0]
    known_file_paths = {
        str(node.identity.get("path") or "")
        for node in snapshot.nodes
        if node.kind == "file" and str(node.identity.get("path") or "")
    }
    normalized_in_file = ""
    nodes = _node_by_id(snapshot)
    symbol_paths = {
        edge.to_id: str(nodes[edge.from_id].identity.get("path") or "")
        for edge in snapshot.edges
        if edge.kind == "DEFINES"
        and edge.from_id in nodes
        and str(nodes[edge.from_id].identity.get("path") or "")
    }
    if selector == "file":
        normalized, resolution_result, resolution_problems = _resolve_graph_query_path(
            snapshot,
            value=value,
            repository_path=repository_path,
            known_paths=known_file_paths,
            query_base={"type": "file"},
            query_path_field="path",
            invalid_message="graph file query must be a normalized repo-relative path",
            ambiguous_message="graph file query matches both repo-relative and workspace-relative paths",
        )
        if resolution_result is not None or resolution_problems:
            return resolution_result, resolution_problems
        wanted = file_id(repo_id, normalized)
        matched_edges = _file_neighborhood_edges(snapshot, wanted)
        paths = [
            _path_from_edge(nodes, edge, reason=reason, symbol_paths=symbol_paths)
            for edge, reason in _file_query_path_edges(snapshot, sorted(matched_edges, key=_edge_key), wanted)
        ]
        return query_payload(query={"type": "file", "path": normalized}, node_ids={wanted}, edges=matched_edges, matches=[_node_summary(nodes[wanted], symbol_paths=symbol_paths)], paths=paths), []

    if selector == "topic":
        wanted = topic_id(repo_id, value)
        if wanted not in nodes:
            return empty_query_payload(query={"type": "topic", "topic": value}, query_status="not_found"), []
        matched_edges = [edge for edge in snapshot.edges if edge.kind == "HAS_TOPIC" and edge.to_id == wanted]
        return query_payload(query={"type": "topic", "topic": value}, node_ids={wanted}, edges=matched_edges, matches=[_node_summary(nodes[wanted], symbol_paths=symbol_paths)]), []

    if selector == "import":
        matched_import_nodes = [
            node
            for node in snapshot.nodes
            if node.kind == "import_ref" and str(node.identity.get("raw_import") or "") == value
        ]
        matched_ids = {node.id for node in matched_import_nodes}
        if not matched_ids:
            return empty_query_payload(query={"type": "import", "raw_import": value}, query_status="not_found"), []
        matched_edges = [edge for edge in snapshot.edges if edge.kind in {"DECLARES_IMPORT", "RESOLVES_TO"} and (edge.to_id in matched_ids or edge.from_id in matched_ids)]
        return query_payload(query={"type": "import", "raw_import": value}, node_ids=set(matched_ids), edges=matched_edges, matches=[_node_summary(node, symbol_paths=symbol_paths) for node in matched_import_nodes]), []

    def task_evidence_edges(task_node_ids: set[str]) -> list[GraphEdge]:
        direct = [
            edge
            for edge in snapshot.edges
            if edge.from_id in task_node_ids or edge.to_id in task_node_ids
        ]
        change_ids = {
            edge.to_id
            for edge in direct
            if edge.kind == "TASK_RECORDED_CHANGE" and edge.from_id in task_node_ids
        }
        affected = [
            edge
            for edge in snapshot.edges
            if edge.kind == "CHANGE_AFFECTED_FILE" and edge.from_id in change_ids
        ]
        return [*direct, *affected]

    if selector == "task":
        normalized_task = normalize_task_id(value)
        wanted = graph_task_id(normalized_task)
        if wanted not in nodes:
            return empty_query_payload(query={"type": "task", "task_id": normalized_task}, query_status="not_found"), []
        matched_edges = task_evidence_edges({wanted})
        paths = [_path_from_edge(nodes, edge, reason="recorded task evidence", symbol_paths=symbol_paths) for edge in sorted(matched_edges, key=_edge_key)]
        return query_payload(
            query={"type": "task", "task_id": normalized_task},
            node_ids={wanted},
            edges=matched_edges,
            matches=[_node_summary(nodes[wanted], symbol_paths=symbol_paths)],
            paths=paths,
        ), []

    if selector == "artifact":
        matched_artifacts = [
            node
            for node in snapshot.nodes
            if node.kind == "artifact" and str(node.identity.get("path") or "") == value
        ]
        artifact_ids = {node.id for node in matched_artifacts}
        if not artifact_ids:
            return empty_query_payload(query={"type": "artifact", "path": value}, query_status="not_found"), []
        task_ids = {
            edge.from_id
            for edge in snapshot.edges
            if edge.kind == "TASK_VERIFIED_BY" and edge.to_id in artifact_ids
        }
        matched_edge_map = {
            _edge_key(edge): edge
            for edge in [
                *[edge for edge in snapshot.edges if edge.kind == "TASK_VERIFIED_BY" and edge.to_id in artifact_ids],
                *task_evidence_edges(task_ids),
            ]
        }
        matched_edges = list(matched_edge_map.values())
        paths = [_path_from_edge(nodes, edge, reason="recorded artifact evidence", symbol_paths=symbol_paths) for edge in sorted(matched_edges, key=_edge_key)]
        return query_payload(
            query={"type": "artifact", "path": value},
            node_ids=set(artifact_ids),
            edges=matched_edges,
            matches=[_node_summary(node, symbol_paths=symbol_paths) for node in matched_artifacts],
            paths=paths,
        ), []

    if selector in {"symbol", "callers_of", "callees_of", "impact_symbol"} and in_file:
        query_base: dict[str, Any] = {"type": selector, "symbol": value}
        if selector == "impact_symbol":
            query_base["depth"] = depth
        normalized_in_file, resolution_result, resolution_problems = _resolve_graph_query_path(
            snapshot,
            value=in_file,
            repository_path=repository_path,
            known_paths=known_file_paths,
            query_base=query_base,
            query_path_field="in_file",
            invalid_message="graph --in-file must be a normalized repo-relative path",
            ambiguous_message="graph --in-file matches both repo-relative and workspace-relative paths",
        )
        if resolution_result is not None or resolution_problems:
            return resolution_result, resolution_problems

    def resolve_one_symbol(query_type: str, raw_symbol: str) -> tuple[GraphNode | None, dict[str, Any] | None, list[Problem]]:
        matches = _match_symbols(snapshot, raw_symbol, in_file=normalized_in_file)
        query = {"type": query_type, "symbol": raw_symbol}
        if normalized_in_file:
            query["in_file"] = normalized_in_file
        if not matches:
            return None, empty_query_payload(query=query, query_status="not_found"), []
        match_payloads = [_symbol_match_dict(snapshot, node) for node in matches]
        if len(matches) > 1:
            result = query_payload(query=query, node_ids={node.id for node in matches}, edges=_definition_edges(snapshot, {node.id for node in matches}), matches=match_payloads)
            return None, result, [Problem("error", "graph_query_ambiguous_symbol", f"graph symbol query matched {len(matches)} symbols; pass --in-file or a qualified name")]
        return matches[0], query_payload(query=query, node_ids={matches[0].id}, edges=_definition_edges(snapshot, {matches[0].id}), matches=match_payloads), []

    if selector in {"symbol", "callers_of", "callees_of", "impact_symbol"}:
        capability_name = "symbols" if selector == "symbol" else "calls"
        semantic_status, semantic_warning = _semantic_query_status(snapshot, capability_name=capability_name, in_file=normalized_in_file)
        if semantic_status in {"unsupported", "unavailable"}:
            query = {"type": selector, "symbol": value}
            if normalized_in_file:
                query["in_file"] = normalized_in_file
            return empty_query_payload(query=query, query_status=semantic_status, warning=semantic_warning), []

    if selector == "symbol":
        symbol_node, base_result, problems = resolve_one_symbol("symbol", value)
        if symbol_node is None:
            return base_result, problems
        call_edges = [edge for edge in snapshot.edges if edge.kind == "CALLS" and (edge.from_id == symbol_node.id or edge.to_id == symbol_node.id)]
        owner_file_id = _symbol_file_id(snapshot, symbol_node.id)
        file_edges = _file_neighborhood_edges(snapshot, owner_file_id) if owner_file_id else []
        edges = list({_edge_key(edge): edge for edge in [*call_edges, *file_edges]}.values())
        path_edges = [(edge, "incoming or outgoing relation for selected symbol") for edge in sorted(call_edges, key=_edge_key)]
        if owner_file_id:
            path_edges.extend(
                (edge, reason)
                for edge, reason in _file_query_path_edges(snapshot, sorted(file_edges, key=_edge_key), owner_file_id)
                if edge.kind != "CALLS"
            )
        paths = [_path_from_edge(nodes, edge, reason=reason, symbol_paths=symbol_paths) for edge, reason in path_edges]
        return query_payload(
            query=base_result["query"] if base_result else {"type": "symbol", "symbol": value},
            node_ids={symbol_node.id},
            edges=edges,
            matches=base_result["matches"] if base_result else [],
            paths=paths,
        ), problems

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
        paths = [_path_from_edge(nodes, edge, reason=reason, symbol_paths=symbol_paths) for edge in sorted(call_edges, key=_edge_key)]
        return query_payload(
            query=base_result["query"] if base_result else {"type": selector, "symbol": value},
            node_ids=node_ids,
            edges=edges,
            matches=base_result["matches"] if base_result else [],
            paths=paths,
            query_status="found",
        ), []

    def impact_walk(
        start_ids: set[str],
        *,
        max_depth: int,
        include_same_file_calls: bool,
    ) -> tuple[set[str], list[GraphEdge], list[dict[str, Any]]]:
        visited = set(start_ids)
        frontier = set(start_ids)
        collected_edges: list[GraphEdge] = []
        collected_edge_keys: set[tuple[str, str, str, str, str]] = set()
        paths: list[dict[str, Any]] = []
        symbol_files = {
            edge.to_id: edge.from_id
            for edge in snapshot.edges
            if edge.kind == "DEFINES"
        }

        def visible_path(edge: GraphEdge) -> bool:
            if edge.kind == "IMPORTS_FILE":
                return True
            if edge.kind == STRUCTURED_EDGE_KIND:
                return True
            if edge.kind != "CALLS":
                return False
            if include_same_file_calls:
                return True
            return symbol_files.get(edge.from_id) != symbol_files.get(edge.to_id)

        for distance in range(1, max_depth + 1):
            next_frontier: set[str] = set()
            for edge in sorted(snapshot.edges, key=_edge_key):
                reason = ""
                if edge.kind == "CALLS" and edge.to_id in frontier:
                    reason = f"depth {distance}: caller depends on changed symbol"
                    next_id = edge.from_id
                elif edge.kind in {"IMPORTS_FILE", "TESTS_FILE", STRUCTURED_EDGE_KIND} and edge.to_id in frontier:
                    relation = "tests" if edge.kind == "TESTS_FILE" else "uses" if edge.kind == STRUCTURED_EDGE_KIND else "imports"
                    reason = f"depth {distance}: file {relation} changed file"
                    next_id = edge.from_id
                elif edge.kind == "DEFINES" and edge.from_id in frontier:
                    reason = f"depth {distance}: file defines potentially changed symbol"
                    next_id = edge.to_id
                elif edge.kind == "DEFINES" and edge.to_id in frontier:
                    reason = f"depth {distance}: symbol belongs to changed file"
                    next_id = edge.from_id
                else:
                    continue
                edge_key = _edge_key(edge)
                if edge_key not in collected_edge_keys:
                    collected_edge_keys.add(edge_key)
                    collected_edges.append(edge)
                    if visible_path(edge):
                        paths.append(_path_from_edge(nodes, edge, reason=reason, symbol_paths=symbol_paths))
                if next_id not in visited:
                    visited.add(next_id)
                    next_frontier.add(next_id)
            frontier = next_frontier
            if not frontier:
                break
        return visited, collected_edges, paths

    if selector == "impact_file":
        normalized, resolution_result, resolution_problems = _resolve_graph_query_path(
            snapshot,
            value=value,
            repository_path=repository_path,
            known_paths=known_file_paths,
            query_base={"type": "impact_file", "depth": depth},
            query_path_field="path",
            invalid_message="graph impact-file query must be a normalized repo-relative path",
            ambiguous_message="graph impact-file query matches both repo-relative and workspace-relative paths",
        )
        if resolution_result is not None or resolution_problems:
            return resolution_result, resolution_problems
        wanted = file_id(repo_id, normalized)
        node_ids, edges, paths = impact_walk(
            {wanted},
            max_depth=depth,
            include_same_file_calls=False,
        )
        return query_payload(
            query={"type": "impact_file", "path": normalized, "depth": depth},
            node_ids=node_ids,
            edges=edges,
            matches=[_node_summary(nodes[wanted], symbol_paths=symbol_paths)],
            paths=paths,
        ), []

    symbol_node, base_result, problems = resolve_one_symbol("impact_symbol", value)
    if symbol_node is None:
        return base_result, problems
    node_ids, edges, paths = impact_walk(
        {symbol_node.id},
        max_depth=depth,
        include_same_file_calls=True,
    )
    return query_payload(
        query=base_result["query"] | {"depth": depth} if base_result else {"type": "impact_symbol", "symbol": value, "depth": depth},
        node_ids=node_ids,
        edges=edges,
        matches=base_result["matches"] if base_result else [],
        paths=paths,
    ), []

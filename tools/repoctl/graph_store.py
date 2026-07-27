from __future__ import annotations

import hashlib
import json
import os
import stat
from collections import defaultdict, deque
from dataclasses import dataclass
from fnmatch import fnmatch
from pathlib import Path, PurePosixPath
from typing import Any

from .code_index import (
    CODE_INDEX_INPUT_VERSION,
    CodeIndexEntry,
    PythonImportOccurrence,
    build_code_index_from_inventory,
    semantic_provider_entries,
)
from .context_sources import context_document_paths
from .evidence_store import evidence_index_binding_problems, load_evidence_index_metadata, materialize_evidence_index
from .graph import build_graph
from .graph_import_resolver import ImportResolution, resolve_code_imports
from .graph_model import GraphEdge, GraphNode, GraphSnapshot, digest_data
from .graph_semantic_model import (
    CapabilityEvidence,
    PreciseCall,
    PreciseSymbol,
    ProviderFailure,
    RpcInvocationContract,
    RpcInvocationFact,
    RpcParamsReasonCode,
    RpcParamsStatus,
    RpcRoutineReasonCode,
    RpcRoutineStatus,
    RpcSchemaSelection,
    SemanticProviderResult,
    SourceAnchor,
)
from .graph_semantic_provider import PROVIDER_INPUT_VERSIONS, PROVIDER_LANGUAGES, build_semantic_provider
from .graph_structured_relations import STRUCTURED_RELATION_INPUT_VERSION
from .git import repo_file_state_records
from .io import atomic_write
from .language_profiles import language_for_path
from .meta import meta_inventory
from .repositories import RepoTarget
from .tasks import Problem, collect_completion_receipts, completion_receipt_artifact_path


GRAPH_STATE_SCHEMA = "repoctl.graph.materialization"
GRAPH_STATE_SCHEMA_VERSION = 3
GRAPH_STATE_ROOT = Path(".repoctl-state/graph")
PROVIDER_RESULT_SCHEMA_VERSION = 3
PROVIDER_CONFIG_PATTERNS = {
    "python_ast": ("pyproject.toml",),
    "typescript_compiler": (
        "package.json",
        "tsconfig.json",
        "jsconfig.json",
        "*/package.json",
        "*/tsconfig.json",
        "*/jsconfig.json",
        "**/package.json",
        "**/tsconfig.json",
        "**/jsconfig.json",
    ),
    "dart_analyzer": (
        "pubspec.yaml",
        "pubspec.lock",
        "*/pubspec.yaml",
        "*/pubspec.lock",
        "**/pubspec.yaml",
        "**/pubspec.lock",
    ),
    "csharp_roslyn": ("*.csproj", "*.asmdef", "**/*.csproj", "**/*.asmdef"),
}


@dataclass(frozen=True)
class _MaterializedGraph:
    manifest: dict[str, Any]
    snapshot: GraphSnapshot
    provider_results: dict[str, SemanticProviderResult]
    evidence_metadata: dict[str, Any]


def _file_record_fingerprint(record: dict[str, Any]) -> str:
    if record.get("source") == "git_index":
        identity = {
            "source": "git_index",
            "mode": str(record.get("mode") or ""),
            "object": str(record.get("object") or ""),
        }
    else:
        probe = record.get("probe") if isinstance(record.get("probe"), dict) else {}
        stat_probe = probe.get("stat") if isinstance(probe.get("stat"), dict) else {}
        identity = {
            "source": "working_tree",
            "kind": str(stat_probe.get("kind") or ""),
            "mode": str(stat_probe.get("mode") or ""),
            "content_sha256": str(record.get("content_sha256") or ""),
            "symlink_target": str(record.get("symlink_target") or ""),
        }
    return digest_data(identity)


def _code_index_entries(snapshot: GraphSnapshot | None) -> list[CodeIndexEntry]:
    if snapshot is None:
        return []
    entries: list[CodeIndexEntry] = []
    for node in snapshot.nodes:
        if node.kind != "file":
            continue
        identity = node.identity if isinstance(node.identity, dict) else {}
        index = node.facts.get("index") if isinstance(node.facts.get("index"), dict) else {}
        path = str(identity.get("path") or "")
        if not path or not index:
            continue
        entries.append(
            CodeIndexEntry(
                path=path,
                workspace_path=str(identity.get("workspace_path") or ""),
                language=str(index.get("language") or ""),
                classification=str(index.get("classification") or ""),
                symbols=[str(value) for value in index.get("symbol_names", []) if str(value)],
                imports=[str(value) for value in index.get("imports", []) if str(value)],
                calls=[str(value) for value in index.get("call_names", []) if str(value)],
                deps=[str(value) for value in index.get("dependencies", []) if str(value)],
                observed_effects=[str(value) for value in index.get("observed_effects", []) if str(value)],
                parse_status=str(index.get("parse_status") or ""),
                parse_error=str(index.get("parse_error") or ""),
                import_occurrences=tuple(
                    PythonImportOccurrence(
                        raw_import=str(value.get("raw_import") or ""),
                        form=str(value.get("form") or ""),
                        module=str(value.get("module") or ""),
                        imported_name=str(value.get("imported_name") or ""),
                        level=int(value.get("level") or 0),
                    )
                    for value in index.get("import_occurrences", [])
                    if isinstance(value, dict)
                    and str(value.get("form") or "") in {"module", "from"}
                    and str(value.get("raw_import") or "")
                ),
                module_bindings=tuple(str(value) for value in index.get("module_bindings", []) if str(value)),
                module_certain_bindings=tuple(
                    str(value)
                    for value in index.get("module_certain_bindings", [])
                    if str(value)
                ),
                module_wildcard_import=index.get("module_wildcard_import") is True,
            )
        )
    return sorted(entries, key=lambda entry: entry.path)


def _sha256_bytes(data: bytes) -> str:
    return "sha256:" + hashlib.sha256(data).hexdigest()


def _path_record(
    path: Path,
    *,
    logical_path: str,
    language: str = "",
    classification: str = "",
    previous: dict[str, Any] | None = None,
) -> dict[str, object]:
    record: dict[str, object] = {
        "path": logical_path,
        "language": language,
        "classification": classification,
    }
    try:
        file_stat = os.lstat(path)
    except OSError:
        record.update({"kind": "missing", "mode": ""})
        return record
    mode = f"{stat.S_IMODE(file_stat.st_mode):04o}"
    if stat.S_ISLNK(file_stat.st_mode):
        probe = {"kind": "symlink", "mode": mode, "target": os.readlink(path)}
        record.update({"kind": "symlink", "mode": mode, "target": probe["target"], "probe": probe})
    elif stat.S_ISREG(file_stat.st_mode):
        probe = {
            "kind": "file",
            "mode": mode,
            "size": file_stat.st_size,
            "mtime_ns": file_stat.st_mtime_ns,
            "ctime_ns": file_stat.st_ctime_ns,
        }
        if previous is not None and previous.get("probe") == probe and previous.get("content_sha256"):
            record.update({"kind": "file", "mode": mode, "content_sha256": previous["content_sha256"], "probe": probe})
            return record
        try:
            content = path.read_bytes()
        except OSError:
            content = b"<unreadable>"
        record.update({"kind": "file", "mode": mode, "content_sha256": _sha256_bytes(content), "probe": probe})
    else:
        probe = {"kind": "other", "mode": mode}
        record.update({"kind": "other", "mode": mode, "probe": probe})
    return record


def _tree_records(root: Path, *, prefix: str, previous: dict[str, dict[str, Any]] | None = None) -> list[dict[str, object]]:
    previous = previous or {}
    if not root.exists() or root.is_symlink():
        return [_path_record(root, logical_path=prefix, previous=previous.get(prefix))] if root.exists() else []
    return [
        _path_record(
            path,
            logical_path=f"{prefix}/{path.relative_to(root).as_posix()}",
            previous=previous.get(f"{prefix}/{path.relative_to(root).as_posix()}"),
        )
        for path in sorted(candidate for candidate in root.rglob("*") if not candidate.is_dir())
    ]


def _config_records(
    target: RepoTarget,
    provider: str,
    inventory_records: list[dict[str, object]],
) -> list[dict[str, object]]:
    patterns = PROVIDER_CONFIG_PATTERNS[provider]
    records = [
        record
        for record in inventory_records
        if any(fnmatch(str(record.get("path") or ""), pattern) for pattern in patterns)
    ]
    if provider == "dart_analyzer":
        package_roots = {
            PurePosixPath(str(record.get("path") or "")).parent
            for record in records
            if PurePosixPath(str(record.get("path") or "")).name == "pubspec.yaml"
        }
        for package_root in sorted(package_roots):
            logical_path = (package_root / ".dart_tool/package_config.json").as_posix()
            package_config = target.root_path / logical_path
            if package_config.is_file():
                records.append(_path_record(package_config, logical_path=logical_path))
    return sorted(records, key=lambda item: str(item.get("path") or ""))


def _provider_config_state(
    target: RepoTarget,
    provider: str,
    inventory_records: list[dict[str, object]],
) -> tuple[dict[str, str], str]:
    records = _config_records(target, provider, inventory_records)
    files = {
        str(record.get("path") or ""): digest_data(record)
        for record in records
        if str(record.get("path") or "")
    }
    return files, digest_data(records)


def _root_record_fingerprint(record: dict[str, Any]) -> str:
    return digest_data(
        {
            "path": str(record.get("path") or ""),
            "language": str(record.get("language") or ""),
            "classification": str(record.get("classification") or ""),
            "kind": str(record.get("kind") or ""),
            "mode": str(record.get("mode") or ""),
            "content_sha256": str(record.get("content_sha256") or ""),
            "target": str(record.get("target") or ""),
        }
    )


def _root_evidence_records(
    root: Path,
    target: RepoTarget,
    *,
    previous: dict[str, dict[str, Any]] | None = None,
    discover_receipt_artifacts: bool = True,
) -> list[dict[str, object]]:
    previous = previous or {}
    records = [
        *_tree_records(target.root_path / ".repometa", prefix=f"{target.display_path}/.repometa", previous=previous),
        *_tree_records(root / "docs/tasks/.repoctl-state/completions", prefix="docs/tasks/.repoctl-state/completions", previous=previous),
        *_tree_records(root / "docs/knowledge/records", prefix="docs/knowledge/records", previous=previous),
        *_tree_records(root / "docs/knowledge/events", prefix="docs/knowledge/events", previous=previous),
    ]
    product_root = target.root_path.resolve()
    for path in context_document_paths(root, target=target):
        if path.resolve().is_relative_to(product_root):
            continue
        logical_path = path.relative_to(root).as_posix()
        records.append(_path_record(path, logical_path=logical_path, previous=previous.get(logical_path)))
    artifact_paths = {
        path
        for path in previous
        if path.startswith(("docs/tasks/", "docs/archive/tasks/"))
        and not path.startswith("docs/tasks/.repoctl-state/completions/")
    }
    if discover_receipt_artifacts:
        receipts, _problems = collect_completion_receipts(root, repo_id=target.id)
        artifact_paths.update(
            artifact
            for receipt in receipts
            if (artifact := completion_receipt_artifact_path(root, receipt))
        )
    for artifact in sorted(artifact_paths):
        path = root / artifact
        if path.is_file():
            records.append(_path_record(path, logical_path=artifact, previous=previous.get(artifact)))
    for relative in ("docs/repoctl.json", "repoctl-upgrade-manifest.json", "pyproject.toml"):
        path = root / relative
        if path.is_file():
            records.append(_path_record(path, logical_path=relative, previous=previous.get(relative)))
    by_path = {str(record.get("path") or ""): record for record in records if str(record.get("path") or "")}
    return [by_path[path] for path in sorted(by_path)]


def _provider_units(provider: str, target: RepoTarget, paths: set[str]) -> dict[str, str]:
    if provider == "typescript_compiler":
        from .graph_typescript_provider import typescript_analysis_units

        return typescript_analysis_units(target.root_path, paths)
    if provider == "csharp_roslyn":
        from .graph_csharp_provider import csharp_analysis_units

        return csharp_analysis_units(target.root_path, paths)
    return {path: "" for path in sorted(paths)}


def collect_graph_inputs(
    root: Path,
    *,
    target: RepoTarget,
    previous_manifest: dict[str, Any] | None = None,
    previous_snapshot: GraphSnapshot | None = None,
    rebuild: bool = False,
) -> tuple[
    tuple[list[CodeIndexEntry], list[Problem], dict[str, Any]],
    list[CodeIndexEntry],
    tuple[list[ImportResolution], dict[str, object]],
    dict[str, Any],
    dict[str, Any],
]:
    previous_manifest = previous_manifest or {}
    inventory, inventory_problems, inventory_meta = meta_inventory(root, changed=False, target=target)
    previous_file_records = previous_manifest.get("file_records") if isinstance(previous_manifest.get("file_records"), dict) else {}
    file_records, git_state = repo_file_state_records(
        root,
        paths=[item.path for item in inventory if item.classification not in {"orphan_annotation", "orphan_exclusion"}],
        target=target,
        previous={str(path): value for path, value in previous_file_records.items() if isinstance(value, dict)},
    )
    if not git_state.available:
        inventory_problems = [*inventory_problems, Problem("error", "graph_git_unavailable", git_state.reason, target.display_path)]
    file_fingerprints = {
        path: _file_record_fingerprint(record)
        for path, record in sorted(file_records.items())
    }
    previous_fingerprints = previous_manifest.get("file_fingerprints") if isinstance(previous_manifest.get("file_fingerprints"), dict) else {}
    previous_entries = _code_index_entries(previous_snapshot)
    full_reindex = (
        rebuild
        or previous_snapshot is None
        or int(previous_manifest.get("code_index_input_version") or 0) != CODE_INDEX_INPUT_VERSION
        or not previous_file_records
    )
    changed_paths = set(file_fingerprints) | set(str(path) for path in previous_fingerprints)
    if not full_reindex:
        changed_paths = {
            path
            for path in changed_paths
            if str(file_fingerprints.get(path) or "") != str(previous_fingerprints.get(path) or "")
        }
    previous_by_path = {entry.path: entry for entry in previous_entries}
    for item in inventory:
        previous = previous_by_path.get(item.path)
        if previous is not None and (previous.classification != item.classification or previous.language != str(language_for_path(item.path))):
            changed_paths.add(item.path)
    if full_reindex:
        changed_paths = set(file_fingerprints) | set(previous_by_path)
    index_result = build_code_index_from_inventory(
        root,
        files=inventory,
        inventory_problems=inventory_problems,
        inventory_meta=inventory_meta,
        target=target,
        previous_entries=previous_entries,
        reindex_paths=changed_paths,
        limit=-1,
    )
    entries, _problems, _meta = index_result
    provider_entries = semantic_provider_entries(entries)
    import_result = resolve_code_imports(provider_entries, repo=target.root_path)
    inventory_records = [
        {
            "path": entry.path,
            "language": entry.language,
            "classification": entry.classification,
            "fingerprint": file_fingerprints.get(entry.path, ""),
        }
        for entry in entries
    ]
    inventory_records.sort(key=lambda item: str(item.get("path") or ""))

    providers: dict[str, dict[str, Any]] = {}
    for provider, languages in PROVIDER_LANGUAGES.items():
        source_records = [
            {
                "path": entry.path,
                "language": entry.language,
                "classification": entry.classification,
                "fingerprint": file_fingerprints.get(entry.path, ""),
            }
            for entry in provider_entries
            if entry.language in languages
        ]
        source_records.sort(key=lambda item: str(item.get("path") or ""))
        paths = {str(record["path"]) for record in source_records}
        config_files, config_digest = _provider_config_state(target, provider, inventory_records)
        providers[provider] = {
            "input_version": PROVIDER_INPUT_VERSIONS[provider],
            "files": {str(record["path"]): digest_data(record) for record in source_records},
            "config_files": config_files,
            "config_digest": config_digest,
            "units": _provider_units(provider, target, paths),
        }

    previous_root_records = previous_manifest.get("root_evidence_records") if isinstance(previous_manifest.get("root_evidence_records"), dict) else {}
    root_records = _root_evidence_records(
        root,
        target,
        previous={str(path): value for path, value in previous_root_records.items() if isinstance(value, dict)},
    )
    root_evidence_records = {
        str(record.get("path") or ""): record
        for record in root_records
        if str(record.get("path") or "")
    }
    root_evidence_fingerprints = {
        path: _root_record_fingerprint(record)
        for path, record in root_evidence_records.items()
    }
    state = {
        "schema": GRAPH_STATE_SCHEMA,
        "schema_version": GRAPH_STATE_SCHEMA_VERSION,
        "repository": target.to_dict(),
        "code_index_input_version": CODE_INDEX_INPUT_VERSION,
        "structured_relation_input_version": STRUCTURED_RELATION_INPUT_VERSION,
        "file_records": file_records,
        "file_fingerprints": file_fingerprints,
        "inventory_digest": digest_data(inventory_records),
        "root_evidence_digest": digest_data(root_evidence_fingerprints),
        "root_evidence_records": root_evidence_records,
        "root_evidence_fingerprints": root_evidence_fingerprints,
        "providers": providers,
    }
    state["input_digest"] = digest_data(
        {
            key: value
            for key, value in state.items()
            if key not in {"file_records", "root_evidence_records"}
        }
    )
    return index_result, provider_entries, import_result, state, {
        "changed_paths": sorted(changed_paths),
        "full_reindex": full_reindex,
    }


def _state_dir(root: Path, target: RepoTarget, *, state_root: Path | None = None) -> Path:
    return (state_root or root / GRAPH_STATE_ROOT) / target.id


def _state_path_label(root: Path, path: Path) -> str:
    try:
        return path.relative_to(root).as_posix()
    except ValueError:
        return path.as_posix()


def _read_json(path: Path) -> dict[str, Any] | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None
    return data if isinstance(data, dict) else None


def _read_materialization_json(root: Path, path: Path) -> tuple[dict[str, Any] | None, str, Problem | None]:
    if not path.exists():
        return None, "missing", None
    try:
        text = path.read_text(encoding="utf-8")
    except OSError as exc:
        return None, "unavailable", Problem(
            "error",
            "graph_materialization_unavailable",
            f"materialized Graph state cannot be read: {exc}",
            _state_path_label(root, path),
        )
    try:
        data = json.loads(text)
    except json.JSONDecodeError as exc:
        return None, "invalid", Problem(
            "error",
            "graph_materialization_invalid",
            f"materialized Graph state is not valid JSON: {exc}",
            _state_path_label(root, path),
        )
    if not isinstance(data, dict):
        return None, "invalid", Problem(
            "error",
            "graph_materialization_invalid",
            "materialized Graph state must be a JSON object",
            _state_path_label(root, path),
        )
    return data, "valid", None


def _anchor_from_dict(data: Any) -> SourceAnchor | None:
    if not isinstance(data, dict):
        return None
    try:
        return SourceAnchor(
            path=str(data["path"]),
            start_line=int(data["start_line"]),
            start_col=int(data["start_col"]),
            end_line=int(data["end_line"]),
            end_col=int(data["end_col"]),
        )
    except (KeyError, TypeError, ValueError):
        return None


def _provider_result_to_dict(result: SemanticProviderResult) -> dict[str, object]:
    return {
        "schema": "repoctl.graph.semantic-provider-result",
        "schema_version": PROVIDER_RESULT_SCHEMA_VERSION,
        "provider": result.provider,
        "languages": list(result.languages),
        "symbols": [symbol.to_dict() for symbol in result.symbols],
        "calls": [call.to_dict() for call in result.calls],
        "rpc_invocations": [fact.to_dict() for fact in result.rpc_invocations],
        "symbol_analyzed_paths": list(result.symbol_analyzed_paths),
        "call_analyzed_paths": list(result.call_analyzed_paths),
        "rpc_analyzed_paths": list(result.rpc_analyzed_paths),
        "symbol_failed_paths": list(result.symbol_failed_paths),
        "call_failed_paths": list(result.call_failed_paths),
        "rpc_failed_paths": list(result.rpc_failed_paths),
        "failures": [failure.to_dict() for failure in result.failures],
        "symbol_coverage": result.symbol_coverage.to_dict(),
        "call_coverage": result.call_coverage.to_dict(),
        "rpc_coverage": result.rpc_coverage.to_dict(),
        "tool": result.tool,
    }


def _provider_result_from_dict(data: dict[str, Any], *, expected_provider: str) -> SemanticProviderResult | None:
    if data.get("schema") != "repoctl.graph.semantic-provider-result" or data.get("schema_version") != PROVIDER_RESULT_SCHEMA_VERSION:
        return None
    if str(data.get("provider") or "") != expected_provider:
        return None
    collection_fields = (
        "languages",
        "symbols",
        "calls",
        "symbol_analyzed_paths",
        "call_analyzed_paths",
        "symbol_failed_paths",
        "call_failed_paths",
        "failures",
    )
    if any(not isinstance(data.get(field), list) for field in collection_fields):
        return None
    if not isinstance(data.get("tool"), dict):
        return None
    symbols: list[PreciseSymbol] = []
    for raw in data["symbols"]:
        if not isinstance(raw, dict):
            return None
        anchor = _anchor_from_dict(raw.get("anchor"))
        if anchor is None:
            return None
        symbols.append(
            PreciseSymbol(
                path=str(raw.get("path") or ""),
                provider=expected_provider,
                provider_symbol_id=str(raw.get("provider_symbol_id") or ""),
                language=str(raw.get("language") or ""),
                kind=str(raw.get("kind") or ""),
                name=str(raw.get("name") or ""),
                qualified_name=str(raw.get("qualified_name") or ""),
                anchor=anchor,
            )
        )
    calls: list[PreciseCall] = []
    for raw in data["calls"]:
        if not isinstance(raw, dict):
            return None
        anchor = _anchor_from_dict(raw.get("anchor"))
        if anchor is None:
            return None
        calls.append(
            PreciseCall(
                path=str(raw.get("path") or ""),
                provider=expected_provider,
                caller_provider_symbol_id=str(raw.get("caller_provider_symbol_id") or ""),
                callee_provider_symbol_id=str(raw.get("callee_provider_symbol_id") or ""),
                language=str(raw.get("language") or ""),
                scope=str(raw.get("scope") or ""),
                anchor=anchor,
            )
        )
    raw_rpc_invocations = data.get("rpc_invocations", [])
    raw_rpc_analyzed_paths = data.get("rpc_analyzed_paths", [])
    raw_rpc_failed_paths = data.get("rpc_failed_paths", [])
    if not isinstance(raw_rpc_invocations, list) or not isinstance(raw_rpc_analyzed_paths, list) or not isinstance(raw_rpc_failed_paths, list):
        return None
    rpc_invocations: list[RpcInvocationFact] = []
    rpc_fact_ids: set[str] = set()
    for raw in raw_rpc_invocations:
        if not isinstance(raw, dict):
            return None
        anchor = _anchor_from_dict(raw.get("anchor"))
        invocation = RpcInvocationContract.from_dict(raw.get("invocation"))
        schema_selection = RpcSchemaSelection.from_dict(raw.get("schema_selection"))
        routine = raw.get("routine")
        params = raw.get("params")
        if anchor is None or invocation is None or schema_selection is None or not isinstance(routine, dict) or not isinstance(params, dict):
            return None
        try:
            routine_status = RpcRoutineStatus(str(routine.get("status") or ""))
            params_status = RpcParamsStatus(str(params.get("status") or ""))
            start_offset = int(raw["start_offset"])
            end_offset = int(raw["end_offset"])
            syntactic_argument_count = int(raw["syntactic_argument_count"])
        except (KeyError, TypeError, ValueError):
            return None
        raw_names = params.get("known_names")
        if not isinstance(raw_names, list) or any(not isinstance(value, str) for value in raw_names):
            return None
        param_names = tuple(sorted(set(raw_names)))
        fact_id = str(raw.get("fact_id") or "")
        path = str(raw.get("path") or "")
        content_sha256 = str(raw.get("content_sha256") or "")
        raw_routine_value = routine.get("value")
        raw_routine_reason = routine.get("reason_code")
        if routine_status == RpcRoutineStatus.KNOWN:
            if "value" not in routine or not isinstance(raw_routine_value, str) or raw_routine_reason not in (None, ""):
                return None
            routine_value = raw_routine_value
            routine_reason = None
        else:
            if not isinstance(raw_routine_reason, str) or not raw_routine_reason or raw_routine_value not in (None, ""):
                return None
            routine_value = ""
            try:
                routine_reason = RpcRoutineReasonCode(raw_routine_reason)
            except ValueError:
                return None
        raw_params_reason = str(params.get("reason_code") or "")
        if params_status == RpcParamsStatus.COMPLETE:
            if raw_params_reason:
                return None
            params_reason = None
        else:
            try:
                params_reason = RpcParamsReasonCode(raw_params_reason)
            except ValueError:
                return None
        if (
            not fact_id
            or fact_id in rpc_fact_ids
            or path != anchor.path
            or not str(raw.get("repository_id") or "")
            or str(raw.get("provider") or "") != expected_provider
            or str(raw.get("language") or "") != "dart"
            or not content_sha256.startswith("sha256:")
            or len(content_sha256) != 71
            or start_offset < 0
            or end_offset <= start_offset
            or syntactic_argument_count < 0
            or not str(raw.get("resolved_callee_identity") or "")
            or len(param_names) != len(raw_names)
        ):
            return None
        rpc_fact_ids.add(fact_id)
        try:
            fact = RpcInvocationFact(
                fact_id=fact_id,
                repository_id=str(raw.get("repository_id") or ""),
                path=path,
                provider=expected_provider,
                language="dart",
                content_sha256=content_sha256,
                start_offset=start_offset,
                end_offset=end_offset,
                resolved_callee_identity=str(raw.get("resolved_callee_identity") or ""),
                receiver_type=str(raw.get("receiver_type") or ""),
                invocation=invocation,
                schema_selection=schema_selection,
                routine_status=routine_status,
                routine=routine_value,
                routine_reason_code=routine_reason,
                params_status=params_status,
                param_names=param_names,
                params_reason_code=params_reason,
                syntactic_argument_count=syntactic_argument_count,
                anchor=anchor,
            )
        except ValueError:
            return None
        rpc_invocations.append(fact)
    failures: list[ProviderFailure] = []
    for raw in data["failures"]:
        if not isinstance(raw, dict) or not isinstance(raw.get("paths"), list):
            return None
        failures.append(
            ProviderFailure(
                provider=expected_provider,
                capability=str(raw.get("capability") or ""),
                code=str(raw.get("code") or ""),
                message=str(raw.get("message") or ""),
                paths=tuple(sorted(str(path) for path in raw.get("paths", []) if str(path))),
            )
        )
    raw_symbol_coverage = data.get("symbol_coverage")
    raw_call_coverage = data.get("call_coverage")
    raw_rpc_coverage = data.get("rpc_coverage", {"evidence_level": "precise", "coverage_gaps": []})
    if not isinstance(raw_symbol_coverage, dict) or not isinstance(raw_call_coverage, dict) or not isinstance(raw_rpc_coverage, dict):
        return None
    if (
        not isinstance(raw_symbol_coverage.get("coverage_gaps"), list)
        or not isinstance(raw_call_coverage.get("coverage_gaps"), list)
        or not isinstance(raw_rpc_coverage.get("coverage_gaps"), list)
    ):
        return None
    symbol_coverage = CapabilityEvidence(
        evidence_level=str(raw_symbol_coverage.get("evidence_level") or ""),
        coverage_gaps=tuple(sorted(str(value) for value in raw_symbol_coverage.get("coverage_gaps", []) if str(value))),
    )
    call_coverage = CapabilityEvidence(
        evidence_level=str(raw_call_coverage.get("evidence_level") or ""),
        coverage_gaps=tuple(sorted(str(value) for value in raw_call_coverage.get("coverage_gaps", []) if str(value))),
    )
    rpc_coverage = CapabilityEvidence(
        evidence_level=str(raw_rpc_coverage.get("evidence_level") or ""),
        coverage_gaps=tuple(sorted(str(value) for value in raw_rpc_coverage.get("coverage_gaps", []) if str(value))),
    )
    if (
        symbol_coverage.evidence_level not in {"precise", "conservative"}
        or call_coverage.evidence_level not in {"precise", "conservative"}
        or rpc_coverage.evidence_level not in {"precise", "conservative"}
    ):
        return None
    return SemanticProviderResult(
        provider=expected_provider,
        languages=tuple(sorted(str(value) for value in data["languages"] if str(value))),
        symbols=tuple(symbols),
        calls=tuple(calls),
        rpc_invocations=tuple(sorted(rpc_invocations, key=lambda item: item.fact_id)),
        symbol_analyzed_paths=tuple(sorted(str(value) for value in data["symbol_analyzed_paths"] if str(value))),
        call_analyzed_paths=tuple(sorted(str(value) for value in data["call_analyzed_paths"] if str(value))),
        rpc_analyzed_paths=tuple(sorted(str(value) for value in raw_rpc_analyzed_paths if str(value))),
        symbol_failed_paths=tuple(sorted(str(value) for value in data["symbol_failed_paths"] if str(value))),
        call_failed_paths=tuple(sorted(str(value) for value in data["call_failed_paths"] if str(value))),
        rpc_failed_paths=tuple(sorted(str(value) for value in raw_rpc_failed_paths if str(value))),
        failures=tuple(failures),
        symbol_coverage=symbol_coverage,
        call_coverage=call_coverage,
        rpc_coverage=rpc_coverage,
        tool=data["tool"],
    )


def _snapshot_from_dict(data: dict[str, Any]) -> GraphSnapshot | None:
    if data.get("schema") != "repoctl.graph.snapshot" or data.get("schema_version") != 1:
        return None
    repository = data.get("repository")
    sources = data.get("sources")
    completeness = data.get("completeness")
    raw_nodes = data.get("nodes")
    raw_edges = data.get("edges")
    capabilities = data.get("capabilities")
    if not isinstance(repository, dict) or not isinstance(sources, list) or not isinstance(completeness, dict) or not isinstance(raw_nodes, list) or not isinstance(raw_edges, list) or not isinstance(capabilities, list):
        return None
    nodes: list[GraphNode] = []
    for raw in raw_nodes:
        if not isinstance(raw, dict) or not isinstance(raw.get("identity"), dict):
            return None
        nodes.append(
            GraphNode(
                id=str(raw.get("id") or ""),
                kind=str(raw.get("kind") or ""),
                identity=raw["identity"],
                facts=raw.get("facts") if isinstance(raw.get("facts"), dict) else {},
            )
        )
    edges: list[GraphEdge] = []
    for raw in raw_edges:
        if not isinstance(raw, dict):
            return None
        edges.append(
            GraphEdge(
                kind=str(raw.get("kind") or ""),
                from_id=str(raw.get("from") or ""),
                to_id=str(raw.get("to") or ""),
                assertion=str(raw.get("assertion") or ""),
                source=str(raw.get("source") or ""),
                facts=raw.get("facts") if isinstance(raw.get("facts"), dict) else {},
            )
        )
    snapshot = GraphSnapshot(
        repository={str(key): str(value) for key, value in repository.items()},
        sources=[source for source in sources if isinstance(source, dict)],
        completeness=completeness,
        nodes=nodes,
        edges=edges,
        schema="repoctl.graph.snapshot",
        schema_version=1,
        authoritative=bool(data.get("authoritative")),
        capabilities=[str(value) for value in capabilities],
    ).with_digest()
    return snapshot if snapshot.snapshot_digest == str(data.get("snapshot_digest") or "") else None


def _validate_materialization_state(
    root: Path,
    *,
    target: RepoTarget,
    state_dir: Path,
    manifest: dict[str, Any],
    snapshot: GraphSnapshot,
) -> tuple[Problem | None, str]:
    if manifest.get("schema") != GRAPH_STATE_SCHEMA or manifest.get("schema_version") != GRAPH_STATE_SCHEMA_VERSION:
        return Problem(
            "error",
            "graph_materialization_schema_mismatch",
            "materialized Graph schema is incompatible; run repoctl graph build --rebuild",
            _state_path_label(root, state_dir),
        ), "incompatible"
    expected_input_digest = digest_data(
        {
            key: value
            for key, value in manifest.items()
            if key not in {"file_records", "root_evidence_records", "input_digest", "snapshot_digest", "provider_result_digests"}
        }
    )
    if str(manifest.get("input_digest") or "") != expected_input_digest:
        return Problem(
            "error",
            "graph_materialization_invalid",
            "materialized Graph manifest input digest is invalid; run repoctl graph build --rebuild",
            _state_path_label(root, state_dir / "manifest.json"),
        ), "invalid"
    if str(manifest.get("snapshot_digest") or "") != snapshot.snapshot_digest:
        return Problem(
            "error",
            "graph_materialization_incomplete",
            "materialized Graph manifest and snapshot do not match; run repoctl graph build --rebuild",
            _state_path_label(root, state_dir),
        ), "incomplete"
    expected_repository = target.to_dict()
    manifest_repository = manifest.get("repository")
    if manifest_repository != expected_repository or snapshot.repository != expected_repository or manifest_repository != snapshot.repository:
        return Problem(
            "error",
            "graph_materialization_repository_mismatch",
            "materialized Graph belongs to a different repository identity; run repoctl graph build --rebuild",
            _state_path_label(root, state_dir),
        ), "repository_mismatch"
    return None, "materialized"


def _admit_materialization(
    root: Path,
    *,
    target: RepoTarget,
    state_root: Path | None = None,
) -> tuple[_MaterializedGraph | None, list[Problem], str]:
    state_dir = _state_dir(root, target, state_root=state_root)
    manifest_data, manifest_status, manifest_problem = _read_materialization_json(root, state_dir / "manifest.json")
    snapshot_data, snapshot_status, snapshot_problem = _read_materialization_json(root, state_dir / "snapshot.json")
    read_problems = [problem for problem in (manifest_problem, snapshot_problem) if problem is not None]
    if read_problems:
        status = "unavailable" if any(problem.code == "graph_materialization_unavailable" for problem in read_problems) else "invalid"
        return None, read_problems, status
    if manifest_status == "missing" and snapshot_status == "missing":
        if any(path.is_file() or path.is_symlink() for path in state_dir.rglob("*")):
            return None, [
                Problem(
                    "error",
                    "graph_materialization_incomplete",
                    "materialized Graph state is incomplete; run repoctl graph build --rebuild",
                    _state_path_label(root, state_dir),
                )
            ], "incomplete"
        return None, [
            Problem("error", "graph_snapshot_missing", "materialized Graph is missing; run repoctl graph build", target.display_path)
        ], "missing"
    if manifest_status == "missing" or snapshot_status == "missing":
        return None, [
            Problem(
                "error",
                "graph_materialization_incomplete",
                "materialized Graph state is incomplete; run repoctl graph build --rebuild",
                _state_path_label(root, state_dir),
            )
        ], "incomplete"
    assert manifest_data is not None and snapshot_data is not None
    snapshot = _snapshot_from_dict(snapshot_data)
    if snapshot is None:
        return None, [
            Problem(
                "error",
                "graph_materialization_invalid",
                "materialized Graph snapshot is invalid; run repoctl graph build --rebuild",
                _state_path_label(root, state_dir / "snapshot.json"),
            )
        ], "invalid"
    problem, status = _validate_materialization_state(
        root,
        target=target,
        state_dir=state_dir,
        manifest=manifest_data,
        snapshot=snapshot,
    )
    if problem is not None:
        return None, [problem], status

    provider_results, provider_problems = _load_provider_results_strict(
        root,
        state_dir=state_dir,
        manifest=manifest_data,
    )
    if provider_problems:
        provider_status = (
            "unavailable"
            if any(problem.code == "graph_materialization_unavailable" for problem in provider_problems)
            else "incomplete"
            if any(problem.code == "graph_materialization_incomplete" for problem in provider_problems)
            else "invalid"
        )
        return None, provider_problems, provider_status

    evidence_metadata, evidence_problems = load_evidence_index_metadata(
        root,
        target=target,
        database_path=state_dir / "evidence.sqlite3",
    )
    if not evidence_problems:
        evidence_problems = evidence_index_binding_problems(
            root,
            target=target,
            metadata=evidence_metadata,
            snapshot_digest=snapshot.snapshot_digest,
            graph_input_digest=str(manifest_data.get("input_digest") or ""),
            database_path=state_dir / "evidence.sqlite3",
        )
    if evidence_problems:
        evidence_status = (
            "unavailable"
            if any(problem.code == "evidence_index_unavailable" for problem in evidence_problems)
            else "incomplete"
            if any(problem.code == "evidence_index_missing" for problem in evidence_problems)
            else "invalid"
        )
        return None, evidence_problems, evidence_status

    return _MaterializedGraph(manifest_data, snapshot, provider_results, evidence_metadata), [], "materialized"


def load_materialized_graph(
    root: Path,
    *,
    target: RepoTarget,
    state_root: Path | None = None,
) -> tuple[GraphSnapshot | None, list[Problem], dict[str, Any]]:
    materialized, problems, status = _admit_materialization(root, target=target, state_root=state_root)
    if materialized is None:
        return None, problems, {"repository": target.to_dict(), "materialization": {"status": status}}
    return materialized.snapshot, [], {
        "repository": target.to_dict(),
        "materialization": {
            "status": "materialized",
            "input_digest": str(materialized.manifest.get("input_digest") or ""),
        },
    }


def graph_materialization_freshness(
    root: Path,
    *,
    target: RepoTarget,
    state_root: Path | None = None,
    snapshot: GraphSnapshot | None = None,
) -> tuple[dict[str, Any], list[Problem]]:
    state_dir = _state_dir(root, target, state_root=state_root)
    provider_results: dict[str, SemanticProviderResult]
    if snapshot is None:
        materialized, admission_problems, status = _admit_materialization(root, target=target, state_root=state_root)
        if materialized is None:
            return {"status": status, "changed_paths": []}, admission_problems
        manifest = materialized.manifest
        snapshot = materialized.snapshot
        provider_results = materialized.provider_results
    else:
        manifest, manifest_status, manifest_problem = _read_materialization_json(root, state_dir / "manifest.json")
        if manifest_problem is not None:
            return {"status": manifest_status, "changed_paths": []}, [manifest_problem]
        if manifest is None:
            return {"status": "incomplete", "changed_paths": []}, [
                Problem(
                    "error",
                    "graph_materialization_incomplete",
                    "materialized Graph state is incomplete; run repoctl graph build --rebuild",
                    _state_path_label(root, state_dir),
                )
            ]
        admission_problem, status = _validate_materialization_state(
            root,
            target=target,
            state_dir=state_dir,
            manifest=manifest,
            snapshot=snapshot,
        )
        if admission_problem is not None:
            return {"status": status, "changed_paths": []}, [admission_problem]
        provider_results, provider_problems = _load_provider_results_strict(
            root,
            state_dir=state_dir,
            manifest=manifest,
        )
        if provider_problems:
            provider_status = (
                "unavailable"
                if any(problem.code == "graph_materialization_unavailable" for problem in provider_problems)
                else "incomplete"
                if any(problem.code == "graph_materialization_incomplete" for problem in provider_problems)
                else "invalid"
            )
            return {"status": provider_status, "changed_paths": []}, provider_problems
    inventory, inventory_problems, _inventory_meta = meta_inventory(root, changed=False, target=target)
    previous_records = manifest.get("file_records") if isinstance(manifest.get("file_records"), dict) else {}
    current_records, git_state = repo_file_state_records(
        root,
        paths=[item.path for item in inventory if item.classification not in {"orphan_annotation", "orphan_exclusion"}],
        target=target,
        previous={str(path): value for path, value in previous_records.items() if isinstance(value, dict)},
    )
    problems = list(inventory_problems)
    if not git_state.available:
        problems.append(Problem("error", "graph_git_unavailable", git_state.reason, target.display_path))
    current_fingerprints = {
        path: _file_record_fingerprint(record)
        for path, record in sorted(current_records.items())
    }
    previous_fingerprints = manifest.get("file_fingerprints") if isinstance(manifest.get("file_fingerprints"), dict) else {}
    changed_paths = sorted(
        path
        for path in set(current_fingerprints) | set(str(value) for value in previous_fingerprints)
        if str(current_fingerprints.get(path) or "") != str(previous_fingerprints.get(path) or "")
    )
    inventory_records = [
        {
            "path": item.path,
            "language": str(language_for_path(item.path)),
            "classification": item.classification,
            "fingerprint": current_fingerprints.get(item.path, ""),
        }
        for item in inventory
    ]
    previous_entries = {entry.path: entry for entry in _code_index_entries(snapshot)}
    inventory_stale_paths = sorted(
        item.path
        for item in inventory
        if (
            (previous := previous_entries.get(item.path)) is not None
            and (
                previous.classification != item.classification
                or previous.language != str(language_for_path(item.path))
            )
        )
    )
    previous_provider_states = manifest.get("providers") if isinstance(manifest.get("providers"), dict) else {}
    changed_provider_configs: dict[str, list[str]] = {}
    provider_stale_paths: dict[str, list[str]] = {}
    provider_state_changed = False
    for provider in PROVIDER_LANGUAGES:
        previous_state = previous_provider_states.get(provider) if isinstance(previous_provider_states.get(provider), dict) else {}
        current_config_files, current_config_digest = _provider_config_state(target, provider, inventory_records)
        previous_config_files = previous_state.get("config_files") if isinstance(previous_state.get("config_files"), dict) else {}
        config_paths = sorted(
            path
            for path in set(current_config_files) | set(str(value) for value in previous_config_files)
            if str(current_config_files.get(path) or "") != str(previous_config_files.get(path) or "")
        )
        config_changed = str(previous_state.get("config_digest") or "") != current_config_digest
        input_version_changed = previous_state.get("input_version") != PROVIDER_INPUT_VERSIONS[provider]
        if config_changed or input_version_changed:
            provider_state_changed = True
            changed_provider_configs[provider] = config_paths
            result = provider_results[provider]
            provider_stale_paths[provider] = sorted(
                {
                    *result.symbol_analyzed_paths,
                    *result.call_analyzed_paths,
                    *result.rpc_analyzed_paths,
                    *result.symbol_failed_paths,
                    *result.call_failed_paths,
                    *result.rpc_failed_paths,
                }
            )
    graph_input_version_changed = (
        manifest.get("code_index_input_version") != CODE_INDEX_INPUT_VERSION
        or manifest.get("structured_relation_input_version") != STRUCTURED_RELATION_INPUT_VERSION
    )
    semantic_stale_paths = sorted(
        {
            path
            for paths in provider_stale_paths.values()
            for path in paths
        }
    )
    graph_input_stale_paths = sorted(set(current_fingerprints) | {str(path) for path in previous_fingerprints}) if graph_input_version_changed else []
    stale_paths = sorted(
        {
            *changed_paths,
            *inventory_stale_paths,
            *semantic_stale_paths,
            *graph_input_stale_paths,
        }
    )
    current_classifications = {item.path: item.classification for item in inventory}
    stale_path_classifications = {
        path: current_classifications[path]
        for path in stale_paths
        if path in current_classifications
    }
    previous_root_records = manifest.get("root_evidence_records") if isinstance(manifest.get("root_evidence_records"), dict) else {}
    root_records = _root_evidence_records(
        root,
        target,
        previous={str(path): value for path, value in previous_root_records.items() if isinstance(value, dict)},
        discover_receipt_artifacts=False,
    )
    current_root_fingerprints = {
        str(record.get("path") or ""): _root_record_fingerprint(record)
        for record in root_records
        if str(record.get("path") or "")
    }
    previous_root_fingerprints = manifest.get("root_evidence_fingerprints") if isinstance(manifest.get("root_evidence_fingerprints"), dict) else {}
    changed_root_paths = sorted(
        path
        for path in set(current_root_fingerprints) | set(str(value) for value in previous_root_fingerprints)
        if str(current_root_fingerprints.get(path) or "") != str(previous_root_fingerprints.get(path) or "")
    )
    root_evidence_digest = digest_data(current_root_fingerprints)
    root_evidence_changed = root_evidence_digest != str(manifest.get("root_evidence_digest") or "")
    status = (
        "current"
        if not changed_paths
        and not inventory_stale_paths
        and not root_evidence_changed
        and not provider_state_changed
        and not graph_input_version_changed
        and not any(problem.severity == "error" for problem in problems)
        else "stale"
    )
    return {
        "status": status,
        "changed_paths": changed_paths,
        "changed_path_count": len(changed_paths),
        "changed_path_classifications": {
            item.path: item.classification
            for item in inventory
            if item.path in changed_paths
        },
        "inventory_stale_paths": inventory_stale_paths,
        "root_evidence_changed": root_evidence_changed,
        "changed_root_paths": changed_root_paths,
        "changed_root_path_count": len(changed_root_paths),
        "provider_state_changed": provider_state_changed,
        "changed_provider_configs": changed_provider_configs,
        "changed_provider_config_count": sum(len(paths) for paths in changed_provider_configs.values()),
        "provider_stale_paths": provider_stale_paths,
        "semantic_stale_paths": semantic_stale_paths,
        "graph_input_version_changed": graph_input_version_changed,
        "graph_input_stale_paths": graph_input_stale_paths,
        "stale_paths": stale_paths,
        "stale_path_classifications": stale_path_classifications,
        "stale_path_count": len(stale_paths),
        "materialized_input_digest": str(manifest.get("input_digest") or ""),
    }, problems


def graph_stale_paths(freshness: Any) -> set[str]:
    """Return the canonical repo-relative paths whose Graph evidence is stale."""
    if not isinstance(freshness, dict):
        return set()
    raw_paths = freshness.get("stale_paths")
    if not isinstance(raw_paths, list):
        raw_paths = freshness.get("changed_paths", [])
    return {str(path) for path in raw_paths if str(path)}


def compact_graph_freshness(freshness: Any) -> dict[str, Any]:
    """Project the bounded freshness fields used by default agent-facing views."""
    if not isinstance(freshness, dict):
        return {}
    return {
        key: freshness[key]
        for key in (
            "status",
            "root_evidence_changed",
        )
        if key in freshness
    }


def _previous_import_pairs(snapshot: GraphSnapshot | None) -> set[tuple[str, str]]:
    if snapshot is None:
        return set()
    nodes = {node.id: node for node in snapshot.nodes}
    pairs: set[tuple[str, str]] = set()
    for edge in snapshot.edges:
        if edge.kind != "IMPORTS_FILE":
            continue
        importer = nodes.get(edge.from_id)
        target = nodes.get(edge.to_id)
        importer_path = str(importer.identity.get("path") or "") if importer is not None else ""
        target_path = str(target.identity.get("path") or "") if target is not None else ""
        if importer_path and target_path:
            pairs.add((importer_path, target_path))
    return pairs


def _reverse_dependents(
    seeds: set[str],
    *,
    current_paths: set[str],
    import_resolutions: list[ImportResolution],
    previous_snapshot: GraphSnapshot | None,
) -> set[str]:
    reverse: dict[str, set[str]] = defaultdict(set)
    for resolution in import_resolutions:
        reverse[resolution.target_path].add(resolution.importer_path)
    for importer, target in _previous_import_pairs(previous_snapshot):
        reverse[target].add(importer)
    affected = set(seeds)
    pending = deque(sorted(seeds))
    while pending:
        target = pending.popleft()
        for importer in sorted(reverse.get(target, set())):
            if importer in current_paths and importer not in affected:
                affected.add(importer)
                pending.append(importer)
    return affected & current_paths


def _affected_paths(
    provider: str,
    *,
    changed_paths: set[str],
    current_state: dict[str, Any],
    previous_state: dict[str, Any],
    import_resolutions: list[ImportResolution],
    previous_snapshot: GraphSnapshot | None,
) -> set[str]:
    current_files = current_state.get("files") if isinstance(current_state.get("files"), dict) else {}
    current_paths = set(str(path) for path in current_files)
    previous_files = previous_state.get("files") if isinstance(previous_state.get("files"), dict) else {}
    previous_paths = set(str(path) for path in previous_files)
    if provider == "python_ast" and current_paths != previous_paths:
        return current_paths
    current_units = current_state.get("units") if isinstance(current_state.get("units"), dict) else {}
    previous_units = previous_state.get("units") if isinstance(previous_state.get("units"), dict) else {}
    if provider == "csharp_roslyn":
        changed_units = {
            str(current_units.get(path) or previous_units.get(path) or "<unassigned>")
            for path in changed_paths
        }
        return {
            path
            for path in current_paths
            if str(current_units.get(path) or "<unassigned>") in changed_units
        }
    if provider == "typescript_compiler":
        configured_units = {
            str(current_units.get(path) or previous_units.get(path) or "")
            for path in changed_paths
            if str(current_units.get(path) or previous_units.get(path) or "")
        }
        configured = {
            path
            for path in current_paths
            if str(current_units.get(path) or "") in configured_units
        }
        unconfigured_seeds = {
            path
            for path in changed_paths
            if not str(current_units.get(path) or previous_units.get(path) or "")
        }
        return configured | _reverse_dependents(
            unconfigured_seeds,
            current_paths=current_paths,
            import_resolutions=import_resolutions,
            previous_snapshot=previous_snapshot,
        )
    return _reverse_dependents(
        changed_paths,
        current_paths=current_paths,
        import_resolutions=import_resolutions,
        previous_snapshot=previous_snapshot,
    )


def _empty_provider_result(provider: str) -> SemanticProviderResult:
    return SemanticProviderResult(provider=provider, languages=tuple(sorted(PROVIDER_LANGUAGES[provider])))


def _merge_failures(
    previous: SemanticProviderResult,
    update: SemanticProviderResult,
    *,
    replace_paths: set[str],
    current_paths: set[str],
) -> tuple[ProviderFailure, ...]:
    failures: dict[tuple[str, str, str, tuple[str, ...]], ProviderFailure] = {}
    for failure in previous.failures:
        remaining = tuple(sorted(path for path in failure.paths if path not in replace_paths and path in current_paths))
        if not remaining:
            continue
        retained = ProviderFailure(failure.provider, failure.capability, failure.code, failure.message, remaining)
        failures[(retained.capability, retained.code, retained.message, retained.paths)] = retained
    for failure in update.failures:
        paths = tuple(sorted(path for path in failure.paths if path in current_paths))
        if not paths:
            continue
        current = ProviderFailure(failure.provider, failure.capability, failure.code, failure.message, paths)
        failures[(current.capability, current.code, current.message, current.paths)] = current
    return tuple(failures[key] for key in sorted(failures))


def _merge_provider_result(
    previous: SemanticProviderResult,
    update: SemanticProviderResult,
    *,
    replace_paths: set[str],
    current_paths: set[str],
) -> SemanticProviderResult:
    symbols = {
        symbol.provider_symbol_id: symbol
        for symbol in previous.symbols
        if symbol.path not in replace_paths and symbol.path in current_paths
    }
    symbols.update({symbol.provider_symbol_id: symbol for symbol in update.symbols if symbol.path in current_paths})
    symbol_ids = set(symbols)
    calls = {
        (call.caller_provider_symbol_id, call.callee_provider_symbol_id, call.path, call.anchor.start_line, call.anchor.start_col): call
        for call in previous.calls
        if call.path not in replace_paths and call.path in current_paths
    }
    calls.update(
        {
            (call.caller_provider_symbol_id, call.callee_provider_symbol_id, call.path, call.anchor.start_line, call.anchor.start_col): call
            for call in update.calls
            if call.path in current_paths
        }
    )
    calls = {
        key: call
        for key, call in calls.items()
        if call.caller_provider_symbol_id in symbol_ids and call.callee_provider_symbol_id in symbol_ids
    }
    rpc_invocations = {
        fact.fact_id: fact
        for fact in previous.rpc_invocations
        if fact.path not in replace_paths and fact.path in current_paths
    }
    rpc_invocations.update(
        {
            fact.fact_id: fact
            for fact in update.rpc_invocations
            if fact.path in current_paths
        }
    )

    def merged_paths(previous_paths: tuple[str, ...], updated_paths: tuple[str, ...]) -> tuple[str, ...]:
        return tuple(sorted(((set(previous_paths) - replace_paths) | set(updated_paths)) & current_paths))

    return SemanticProviderResult(
        provider=previous.provider,
        languages=previous.languages or update.languages,
        symbols=tuple(symbols[key] for key in sorted(symbols)),
        calls=tuple(calls[key] for key in sorted(calls)),
        rpc_invocations=tuple(rpc_invocations[key] for key in sorted(rpc_invocations)),
        symbol_analyzed_paths=merged_paths(previous.symbol_analyzed_paths, update.symbol_analyzed_paths),
        call_analyzed_paths=merged_paths(previous.call_analyzed_paths, update.call_analyzed_paths),
        rpc_analyzed_paths=merged_paths(previous.rpc_analyzed_paths, update.rpc_analyzed_paths),
        symbol_failed_paths=merged_paths(previous.symbol_failed_paths, update.symbol_failed_paths),
        call_failed_paths=merged_paths(previous.call_failed_paths, update.call_failed_paths),
        rpc_failed_paths=merged_paths(previous.rpc_failed_paths, update.rpc_failed_paths),
        failures=_merge_failures(previous, update, replace_paths=replace_paths, current_paths=current_paths),
        symbol_coverage=(
            update.symbol_coverage
            if update.symbol_analyzed_paths or update.symbol_failed_paths
            else previous.symbol_coverage
        ),
        call_coverage=(
            update.call_coverage
            if update.call_analyzed_paths or update.call_failed_paths
            else previous.call_coverage
        ),
        rpc_coverage=(
            update.rpc_coverage
            if update.rpc_analyzed_paths or update.rpc_failed_paths
            else previous.rpc_coverage
        ),
        tool=update.tool or previous.tool,
    )


def _load_provider_results_strict(
    root: Path,
    *,
    state_dir: Path,
    manifest: dict[str, Any],
) -> tuple[dict[str, SemanticProviderResult], list[Problem]]:
    expected = manifest.get("provider_result_digests") if isinstance(manifest.get("provider_result_digests"), dict) else {}
    if set(str(provider) for provider in expected) != set(PROVIDER_LANGUAGES):
        return {}, [
            Problem(
                "error",
                "graph_materialization_incomplete",
                "materialized Graph provider cache set is incomplete; run repoctl graph build --rebuild",
                _state_path_label(root, state_dir / "providers"),
            )
        ]
    results: dict[str, SemanticProviderResult] = {}
    for provider in PROVIDER_LANGUAGES:
        path = state_dir / "providers" / f"{provider}.json"
        data, status, read_problem = _read_materialization_json(root, path)
        if read_problem is not None:
            return {}, [read_problem]
        if data is None or status == "missing":
            return {}, [
                Problem(
                    "error",
                    "graph_materialization_incomplete",
                    "materialized Graph provider cache is missing; run repoctl graph build --rebuild",
                    _state_path_label(root, path),
                )
            ]
        if str(expected.get(provider) or "") != digest_data(data):
            return {}, [
                Problem(
                    "error",
                    "graph_materialization_invalid",
                    "materialized Graph provider cache is invalid; run repoctl graph build --rebuild",
                    _state_path_label(root, path),
                )
            ]
        result = _provider_result_from_dict(data, expected_provider=provider)
        if result is None:
            return {}, [
                Problem(
                    "error",
                    "graph_materialization_invalid",
                    "materialized Graph provider cache is invalid; run repoctl graph build --rebuild",
                    _state_path_label(root, path),
                )
            ]
        results[provider] = result
    return results, []


def materialize_graph(
    root: Path,
    *,
    target: RepoTarget,
    rebuild: bool = False,
    state_root: Path | None = None,
) -> tuple[GraphSnapshot | None, list[Problem], dict[str, Any]]:
    state_dir = _state_dir(root, target, state_root=state_root)
    previous_manifest: dict[str, Any] = {}
    previous_snapshot: GraphSnapshot | None = None
    previous_provider_results: dict[str, SemanticProviderResult] = {}
    if not rebuild:
        materialized, admission_problems, admission_status = _admit_materialization(
            root,
            target=target,
            state_root=state_root,
        )
        if admission_status != "missing":
            if admission_problems or materialized is None:
                return None, admission_problems, {
                    "repository": target.to_dict(),
                    "materialization": {"status": admission_status},
                }
            previous_manifest = materialized.manifest
            previous_snapshot = materialized.snapshot
            previous_provider_results = materialized.provider_results
    index_result, provider_entries, import_result, current, index_update = collect_graph_inputs(
        root,
        target=target,
        previous_manifest=previous_manifest,
        previous_snapshot=previous_snapshot,
        rebuild=rebuild,
    )
    if any(problem.severity == "error" for problem in index_result[1]):
        return None, index_result[1], {"repository": target.to_dict(), "materialization": {"status": "unavailable"}}
    import_resolutions, _import_meta = import_result
    if not rebuild and previous_snapshot is not None and str(previous_manifest.get("input_digest") or "") == current["input_digest"]:
        evidence, evidence_problems = materialize_evidence_index(
            root,
            target=target,
            snapshot=previous_snapshot,
            entries=index_result[0],
            file_fingerprints=current["file_fingerprints"],
            changed_paths=set(index_update["changed_paths"]),
            graph_input_digest=current["input_digest"],
            database_path=state_dir / "evidence.sqlite3",
        )
        if any(problem.severity == "error" for problem in evidence_problems):
            return previous_snapshot, evidence_problems, {
                "repository": target.to_dict(),
                "materialization": {"status": "failed", "input_digest": current["input_digest"], "evidence": evidence},
            }
        reused_manifest = {
            **current,
            "snapshot_digest": previous_snapshot.snapshot_digest,
            "provider_result_digests": previous_manifest.get("provider_result_digests", {}),
        }
        atomic_write(state_dir / "manifest.json", json.dumps(reused_manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
        return previous_snapshot, evidence_problems, {
            "repository": target.to_dict(),
            "materialization": {
                "status": "reused",
                "input_digest": current["input_digest"],
                "reused_providers": sorted(PROVIDER_LANGUAGES),
                "updated_providers": [],
                "updated_paths": {},
                "code_index": index_update,
                "evidence": evidence,
            },
        }

    previous_provider_states = previous_manifest.get("providers") if isinstance(previous_manifest.get("providers"), dict) else {}
    results: list[SemanticProviderResult] = []
    reused_providers: list[str] = []
    updated_providers: list[str] = []
    updated_paths: dict[str, list[str]] = {}
    for provider in PROVIDER_LANGUAGES:
        current_state = current["providers"][provider]
        previous_state = previous_provider_states.get(provider) if isinstance(previous_provider_states.get(provider), dict) else {}
        previous_result = previous_provider_results.get(provider)
        current_files = current_state.get("files") if isinstance(current_state.get("files"), dict) else {}
        previous_files = previous_state.get("files") if isinstance(previous_state.get("files"), dict) else {}
        current_paths = set(str(path) for path in current_files)
        old_paths = set(str(path) for path in previous_files)
        config_changed = (
            rebuild
            or previous_result is None
            or previous_state.get("input_version") != current_state.get("input_version")
            or previous_state.get("config_digest") != current_state.get("config_digest")
        )
        changed_paths = {
            path
            for path in current_paths | old_paths
            if str(current_files.get(path) or "") != str(previous_files.get(path) or "")
        }
        if config_changed:
            analysis_paths = set(current_paths)
            replace_paths = current_paths | old_paths
        else:
            analysis_paths = _affected_paths(
                provider,
                changed_paths=changed_paths,
                current_state=current_state,
                previous_state=previous_state,
                import_resolutions=import_resolutions,
                previous_snapshot=previous_snapshot,
            )
            replace_paths = analysis_paths | (changed_paths - current_paths)
        if previous_result is not None and not replace_paths:
            results.append(previous_result)
            reused_providers.append(provider)
            continue

        base = previous_result or _empty_provider_result(provider)
        update = (
            build_semantic_provider(
                provider,
                root,
                target=target,
                entries=provider_entries,
                import_resolutions=import_resolutions,
                analysis_paths=analysis_paths,
                previous=base,
            )
            if analysis_paths
            else _empty_provider_result(provider)
        )
        result = _merge_provider_result(
            base,
            update,
            replace_paths=replace_paths,
            current_paths=current_paths,
        )
        results.append(result)
        updated_providers.append(provider)
        updated_paths[provider] = sorted(analysis_paths)

    snapshot, problems, meta = build_graph(
        root,
        target=target,
        code_index_result=index_result,
        cached_semantic_results=results,
    )
    if snapshot is None or any(problem.severity == "error" for problem in problems):
        return snapshot, problems, {**meta, "materialization": {"status": "failed", "input_digest": current["input_digest"]}}

    evidence_update_paths = set(index_update["changed_paths"])
    for paths in updated_paths.values():
        evidence_update_paths.update(paths)
    evidence, evidence_problems = materialize_evidence_index(
        root,
        target=target,
        snapshot=snapshot,
        entries=index_result[0],
        file_fingerprints=current["file_fingerprints"],
        changed_paths=evidence_update_paths,
        graph_input_digest=current["input_digest"],
        rebuild=bool(index_update["full_reindex"]),
        allow_reset=rebuild,
        database_path=state_dir / "evidence.sqlite3",
    )
    problems.extend(evidence_problems)
    if any(problem.severity == "error" for problem in evidence_problems):
        return snapshot, problems, {
            **meta,
            "materialization": {"status": "failed", "input_digest": current["input_digest"], "evidence": evidence},
        }

    provider_result_digests: dict[str, str] = {}
    for result in results:
        data = _provider_result_to_dict(result)
        provider_result_digests[result.provider] = digest_data(data)
        atomic_write(
            state_dir / "providers" / f"{result.provider}.json",
            json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + "\n",
        )
    atomic_write(state_dir / "snapshot.json", json.dumps(snapshot.to_dict(), ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    manifest = {
        **current,
        "snapshot_digest": snapshot.snapshot_digest,
        "provider_result_digests": provider_result_digests,
    }
    atomic_write(state_dir / "manifest.json", json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n")
    return snapshot, problems, {
        **meta,
        "materialization": {
            "status": "rebuilt" if rebuild or not previous_manifest else "updated",
            "input_digest": current["input_digest"],
            "reused_providers": reused_providers,
            "updated_providers": updated_providers,
            "updated_paths": updated_paths,
            "code_index": index_update,
            "evidence": evidence,
        },
    }

"""One-shot work-path navigation using existing Graph and current source.

This adds no provider, persistence, lifecycle, or execution authority. A source
match is a candidate, never an invented provider-confirmed CALLS/TESTS_FILE edge.
"""
from __future__ import annotations

import ast
import re
from pathlib import Path
from typing import Any

from .graph_model import GraphSnapshot
from .git import normalize_repo_path
from .path_roles import is_test_path
from .repositories import RepoTarget

MAX_WORKFLOW_SOURCE_FILES = 2048
MAX_WORKFLOW_FILE_BYTES = 512 * 1024
MAX_WORKFLOW_TOTAL_SOURCE_BYTES = 8 * 1024 * 1024
MAX_WORKFLOW_STAGE_ENTRIES = 30
_ROUTE_METHODS = frozenset({"route", "api_route", "get", "post", "put", "patch", "delete"})
_FALLBACK_SUFFIXES = frozenset({".rs", ".go", ".java", ".kt", ".kts", ".swift"})


def _source(root: Path, target: RepoTarget, path: str) -> str:
    """Only regular, bounded files inside the selected product Git root."""
    if not path or normalize_repo_path(path) != path:
        return ""
    file = target.root_path / path
    try:
        base = target.root_path.resolve()
        resolved = file.resolve()
        if base not in (resolved, *resolved.parents) or not file.is_file() or file.is_symlink():
            return ""
        if file.stat().st_size > MAX_WORKFLOW_FILE_BYTES:
            return ""
        return file.read_text(encoding="utf-8")
    except (OSError, UnicodeError, ValueError, RuntimeError):
        return ""


def _node_path_index(snapshot: GraphSnapshot) -> tuple[dict[str, str], dict[str, dict[str, str]], dict[str, dict[str, Any]]]:
    nodes = {node.id: node for node in snapshot.nodes}
    owner = {
        edge.to_id: str(nodes[edge.from_id].identity.get("path") or "")
        for edge in snapshot.edges
        if edge.kind == "DEFINES" and edge.from_id in nodes and edge.to_id in nodes
    }
    symbols = {
        node.id: {
            "name": str((node.facts.get("provider") or {}).get("qualified_name") or ""),
            "path": owner.get(node.id, ""),
        }
        for node in snapshot.nodes
        if node.kind == "symbol" and isinstance(node.facts.get("provider"), dict)
    }
    return owner, symbols, nodes


def _route_functions(source: str, route: str) -> list[tuple[str, int, ast.AST]]:
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return []
    matches: list[tuple[str, int, ast.AST]] = []
    for node in ast.walk(tree):
        if not isinstance(node, (ast.FunctionDef, ast.AsyncFunctionDef)):
            continue
        for decorator in node.decorator_list:
            if not isinstance(decorator, ast.Call) or not isinstance(decorator.func, ast.Attribute):
                continue
            if decorator.func.attr not in _ROUTE_METHODS or not decorator.args:
                continue
            first = decorator.args[0]
            if isinstance(first, ast.Constant) and isinstance(first.value, str) and first.value == route:
                matches.append((node.name, node.lineno, node))
    return matches


def _called_names(function: ast.AST) -> set[tuple[str, str]]:
    calls: set[tuple[str, str]] = set()
    for node in ast.walk(function):
        if not isinstance(node, ast.Call):
            continue
        if isinstance(node.func, ast.Attribute):
            receiver = ast.unparse(node.func.value) if hasattr(ast, "unparse") else ""
            calls.add((node.func.attr, receiver))
        elif isinstance(node.func, ast.Name):
            calls.add((node.func.id, ""))
    return calls


def _entry(path: str, *, relation: str, evidence: str, symbol: str = "", line: int = 0) -> dict[str, Any]:
    item: dict[str, Any] = {"path": path, "relation": relation, "evidence": evidence}
    if symbol:
        item["symbol"] = symbol
    if line:
        item["line"] = line
    return item


def _dedupe_entries(entries: list[dict[str, Any]]) -> tuple[list[dict[str, Any]], int]:
    # Multiple relevant tests in one file should not create one item per test.
    merged: dict[str, dict[str, Any]] = {}
    for item in entries:
        key = str(item.get("path") or "")
        previous = merged.get(key)
        if previous is None or (previous["relation"] == "candidate" and item["relation"] != "candidate"):
            merged[key] = item
    result = [merged[key] for key in sorted(merged)]
    return result[:MAX_WORKFLOW_STAGE_ENTRIES], len(result)


def query_graph_workflow(
    snapshot: GraphSnapshot,
    *,
    root: Path,
    target: RepoTarget,
    route: str = "",
    symbol: str = "",
    in_file: str = "",
    freshness: dict[str, Any] | None = None,
) -> dict[str, Any]:
    """Return source-anchored navigation without treating candidates as precise edges."""
    if bool(route) == bool(symbol):
        return {"status": "invalid", "problem": "choose exactly one --route or --symbol"}
    if in_file and (normalize_repo_path(in_file) != in_file or in_file.startswith("/")):
        return {"status": "invalid", "problem": "--in-file must be a normalized repository-relative file"}
    freshness = freshness or {}
    stale = set(freshness.get("stale_paths") or [])
    _owner, symbols, nodes = _node_path_index(snapshot)
    graph_files = {
        str(node.identity.get("path") or "")
        for node in snapshot.nodes
        if node.kind == "file" and str(node.identity.get("path") or "")
    }
    changed = {str(path) for path in freshness.get("changed_paths") or [] if isinstance(path, str)}
    # A stale, nearly empty Graph can still point at changed current files.
    files = sorted(graph_files | changed)
    bounded_files = files[:MAX_WORKFLOW_SOURCE_FILES]
    incomplete_inventory = len(files) > len(bounded_files)
    # --in-file selects the entrypoint/implementation, not the dependent or
    # test source corpus; otherwise an exact seed loses relevant tests.
    seed_files = [path for path in bounded_files if not in_file or path == in_file]
    source_cache: dict[str, str] = {}
    read_bytes = 0
    scan_exhausted = False

    def source_read(path: str) -> str:
        nonlocal read_bytes, scan_exhausted
        if path not in source_cache:
            if read_bytes >= MAX_WORKFLOW_TOTAL_SOURCE_BYTES:
                scan_exhausted = True
                return ""
            value = _source(root, target, path)
            read_bytes += len(value.encode("utf-8"))
            source_cache[path] = value
        return source_cache[path]

    entrypoints: list[dict[str, Any]] = []
    implementations: list[dict[str, Any]] = []
    dependents: list[dict[str, Any]] = []
    tests: list[dict[str, Any]] = []
    links: list[dict[str, Any]] = []
    chosen_symbol_ids: set[str] = set()
    entrypoint_source: list[tuple[str, str, ast.AST]] = []
    if route:
        for path in seed_files:
            if not path.endswith(".py") or is_test_path(path):
                continue
            for name, line, function in _route_functions(source_read(path), route):
                entrypoints.append(_entry(path, relation="source", evidence="HTTP decorator literal", symbol=name, line=line))
                entrypoint_source.append((path, name, function))
        if len(entrypoints) > 1:
            return {
                "query": {"route": route, "in_file": in_file},
                "status": "ambiguous", "entrypoints": entrypoints[:MAX_WORKFLOW_STAGE_ENTRIES],
                "warnings": ["Multiple entrypoints match; pass --in-file to disambiguate"],
                "completeness": {"source_scan_bounded": not incomplete_inventory},
            }
        for path, name, _function in entrypoint_source:
            chosen_symbol_ids.update(
                sid for sid, info in symbols.items()
                if info["path"] == path and info["name"].rsplit(".", 1)[-1] == name and path not in stale
            )
        # A handler-to-method attribute call is not statically resolved just
        # because the method name is identical: expose a bounded candidate.
        if entrypoint_source:
            path, _name, func = entrypoint_source[0]
            method_names = _called_names(func)
            for method, receiver in sorted(method_names):
                matches = [
                    (sid, info) for sid, info in symbols.items()
                    if info["path"] and info["path"] != path
                    and info["name"].endswith("." + method)
                    and not is_test_path(info["path"])
                    and info["path"] not in stale
                ]
                if receiver:
                    receiver_norm = re.sub(r"[^a-z0-9]", "", receiver.casefold())
                    refined = [
                        item for item in matches
                        if receiver_norm.endswith(re.sub(r"[^a-z0-9]", "", item[1]["name"].split(".", 1)[0].casefold()))
                    ]
                    if refined:
                        matches = refined
                if len(matches) == 1:
                    sid, info = matches[0]
                    chosen_symbol_ids.add(sid)
                    implementations.append(_entry(info["path"], relation="candidate", evidence="unresolved attribute call in route handler", symbol=info["name"]))
                    links.append({"from": path, "to": info["path"], "relation": "candidate", "evidence": "unresolved attribute call"})
    else:
        matches = [
            (sid, info) for sid, info in symbols.items()
            if info["path"] and (info["name"] == symbol or info["name"].rsplit(".", 1)[-1] == symbol)
            and (not in_file or info["path"] == in_file)
            and info["path"] not in stale
        ]
        if len(matches) > 1:
            return {
                "query": {"symbol": symbol, "in_file": in_file},
                "status": "ambiguous", "implementations": [
                    _entry(info["path"], relation="confirmed", evidence="provider symbol", symbol=info["name"])
                    for _sid, info in matches[:MAX_WORKFLOW_STAGE_ENTRIES]
                ], "warnings": ["Multiple precise symbols match; pass a qualified name and --in-file"],
            }
        for sid, info in matches:
            chosen_symbol_ids.add(sid)
            implementations.append(_entry(info["path"], relation="confirmed", evidence="provider symbol", symbol=info["name"]))
        if not matches and symbol:
            # Unsupported languages still get source locations, never a
            # fabricated precise symbol, caller, or zero-impact assertion.
            # Accept snake_case test names such as host_audio_clock_default,
            # but not an unrelated longer alphanumeric word such as mainline.
            needle = re.compile(r"(?<![A-Za-z0-9])" + re.escape(symbol.rsplit(".", 1)[-1]) + r"(?![A-Za-z0-9])")
            for path in seed_files:
                suffix = Path(path).suffix.casefold()
                if suffix not in _FALLBACK_SUFFIXES and not (
                    str(freshness.get("status") or "current") != "current" and suffix in {".dart", ".py"}
                ):
                    continue
                source = source_read(path)
                matched_line = next((i for i, line in enumerate(source.splitlines(), 1) if needle.search(line)), 0)
                if matched_line or symbol.casefold() in Path(path).stem.casefold():
                    implementations.append(_entry(
                        path, relation="candidate", symbol=symbol, line=matched_line,
                        evidence="current source text or module filename; no provider-confirmed relation",
                    ))
                    if suffix == ".rs" and re.search(
                        r"#\[(?:tokio::)?test\]\s*(?:async\s+)?fn\s+[A-Za-z0-9_]*"
                        + re.escape(symbol.rsplit(".", 1)[-1]), source,
                    ):
                        tests.append(_entry(path, relation="candidate", evidence="named Rust embedded unit test"))

    # Provider-confirmed calls/imports and TESTS_FILE paths are preserved. An
    # edge touching a stale source is never presented as a current relation.
    selected_files = {entry["path"] for entry in [*entrypoints, *implementations]}
    for edge in snapshot.edges:
        if edge.kind not in {"CALLS", "IMPORTS_FILE", "TESTS_FILE"}:
            continue
        a = nodes.get(edge.from_id)
        b = nodes.get(edge.to_id)
        from_path = symbols.get(edge.from_id, {}).get("path", "") if edge.kind == "CALLS" else str(a.identity.get("path") or "") if a else ""
        to_path = symbols.get(edge.to_id, {}).get("path", "") if edge.kind == "CALLS" else str(b.identity.get("path") or "") if b else ""
        if not from_path or not to_path or from_path in stale or to_path in stale:
            continue
        if edge.kind == "CALLS" and edge.to_id in chosen_symbol_ids:
            if is_test_path(from_path):
                tests.append(_entry(from_path, relation="confirmed", evidence="CALLS"))
            else:
                dependents.append(_entry(from_path, relation="confirmed", evidence="CALLS"))
        if edge.kind == "IMPORTS_FILE" and to_path in selected_files and from_path not in selected_files:
            if is_test_path(from_path):
                tests.append(_entry(from_path, relation="confirmed", evidence="IMPORTS_FILE"))
            else:
                dependents.append(_entry(from_path, relation="confirmed", evidence="IMPORTS_FILE"))
        if edge.kind == "TESTS_FILE" and to_path in selected_files:
            tests.append(_entry(from_path, relation="confirmed", evidence="TESTS_FILE"))
    # Name-nearby test modules are navigation candidates, not confirmed
    # TESTS_FILE relations (particularly when the snapshot is stale).
    for item in implementations:
        impl_path = item["path"]
        if Path(impl_path).suffix.casefold() == ".dart":
            expected_stem = Path(impl_path).stem + "_test"
            for test_path in bounded_files:
                if is_test_path(test_path) and Path(test_path).stem == expected_stem:
                    tests.append(_entry(test_path, relation="candidate", evidence="matching Dart test module name"))
    if route:
        # Tests posting a literal route need not import its handler; this is a
        # useful source-matched *candidate*, not a proven test-to-file edge.
        for path in bounded_files:
            if is_test_path(path) and path.endswith(".py") and route in source_read(path):
                tests.append(_entry(path, relation="candidate", evidence="HTTP route literal in test source"))

    entrypoints, entry_count = _dedupe_entries(entrypoints)
    implementations, implementation_count = _dedupe_entries(implementations)
    dependents, dependent_count = _dedupe_entries(dependents)
    tests, test_count = _dedupe_entries(tests)
    any_found = bool(entrypoints or implementations)
    candidate_present = any(item["relation"] == "candidate" for item in [*implementations, *dependents, *tests])
    # Empty/stale materialization cannot prove a negative; no-test evidence
    # does not mean a code path has no tests.
    partial = candidate_present or incomplete_inventory or scan_exhausted or not tests or str(freshness.get("status") or "current") != "current"
    if not any_found:
        status = "partial" if str(freshness.get("status") or "current") != "current" or incomplete_inventory else "unsupported" if symbol and any(Path(path).suffix.casefold() in _FALLBACK_SUFFIXES for path in bounded_files) else "not_found"
    else:
        status = "partial" if partial else "found"
    warnings: list[str] = []
    if incomplete_inventory or scan_exhausted:
        warnings.append("Source scan exceeded file or byte budget; narrow with --in-file")
    if str(freshness.get("status") or "current") != "current":
        warnings.append("Graph snapshot is stale; confirmed stale relations are excluded; rebuild before treating negative results as complete")
    if candidate_present:
        warnings.append("Candidate links are source matches, not provider-confirmed relations")
    if not tests:
        warnings.append("No confirmed relevant tests located; this is not a zero-impact claim")
    return {
        "query": {"route": route, "symbol": symbol, "in_file": in_file},
        "status": status,
        "stages": {
            "entrypoints": entrypoints,
            "implementation": implementations,
            "callers_or_dependents": dependents,
            "tests": tests,
        },
        "counts": {"entrypoints": entry_count, "implementation": implementation_count, "callers_or_dependents": dependent_count, "tests": test_count},
        "links": links,
        "completeness": {
            "source_scan_bounded": not (incomplete_inventory or scan_exhausted),
            "source_file_count": len(files),
            "files_scanned": len(source_cache),
            "source_bytes_scanned": read_bytes,
            "graph_freshness": str(freshness.get("status") or "unknown"),
            "precision": "partial" if status == "partial" else "confirmed" if status == "found" else "unavailable",
        },
        "warnings": warnings,
    }


def evaluate_workflow_benchmark(result: dict[str, Any], case: dict[str, Any]) -> dict[str, Any]:
    """Measure source-path recall and confirmed recall, not edge counts/bytes."""
    expected = case.get("expected_paths") if isinstance(case.get("expected_paths"), dict) else {}
    stages = result.get("stages") if isinstance(result.get("stages"), dict) else {}
    metrics: dict[str, Any] = {}
    total = hits = confirmed_hits = 0
    for stage in sorted(expected):
        expected_paths = sorted({str(value) for value in expected[stage] if isinstance(value, str)})
        observations = [entry for entry in stages.get(stage, []) if isinstance(entry, dict)]
        matches = {str(entry.get("path") or ""): entry for entry in observations}
        hit_paths = sorted(set(expected_paths) & set(matches))
        confirmed_paths = [path for path in hit_paths if matches[path].get("relation") == "confirmed"]
        total += len(expected_paths)
        hits += len(hit_paths)
        confirmed_hits += len(confirmed_paths)
        metrics[stage] = {
            "expected_count": len(expected_paths),
            "found_count": len(hit_paths),
            "confirmed_count": len(confirmed_paths),
            "missing_paths": sorted(set(expected_paths) - set(matches)),
            "path_recall": round(len(hit_paths) / len(expected_paths), 4) if expected_paths else None,
        }
    allowed = case.get("expected_statuses") if isinstance(case.get("expected_statuses"), list) else []
    return {
        "case_id": str(case.get("id") or ""),
        "query_status": result.get("status") or "unavailable",
        "status_expected": result.get("status") in allowed,
        "expected_path_count": total,
        "found_path_count": hits,
        "confirmed_path_count": confirmed_hits,
        "path_recall": round(hits / total, 4) if total else None,
        "confirmed_path_recall": round(confirmed_hits / total, 4) if total else None,
        "stages": metrics,
        "limitations": list(result.get("warnings") or []),
    }

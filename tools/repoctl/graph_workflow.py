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
            "kind": str((node.facts.get("provider") or {}).get("kind") or ""),
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


def _python_test_exercises_operation(source: str, *, method: str, route: str) -> bool:
    """Require a direct operation call inside a Python test; imports alone do not prove coverage."""
    try:
        tree = ast.parse(source)
    except (SyntaxError, ValueError):
        return False
    for test in ast.walk(tree):
        if not isinstance(test, (ast.FunctionDef, ast.AsyncFunctionDef)) or not test.name.startswith("test_"):
            continue
        for node in ast.walk(test):
            if not isinstance(node, ast.Call) or not isinstance(node.func, ast.Attribute):
                continue
            if node.func.attr == method:
                return True
            if (node.func.attr in {"get", "post", "put", "patch", "delete"}
                and node.args and isinstance(node.args[0], ast.Constant)
                and node.args[0].value == route):
                return True
    return False


def _flask_factory_attribute_wiring(
    paths: list[str], source_read: Any, *, route_path: str, implementation_path: str,
    receiver: str, method: str, blueprint_export: str, implementation_class: str,
) -> dict[str, Any] | None:
    """Verify an app factory's *source wiring*, not runtime type dispatch.

    Flask's `current_app` is dynamic. Only report a current-source path when a
    factory explicitly imports the expected implementation and blueprint,
    constructs and assigns `app.<route_receiver_attribute>`, and registers the blueprint. This is a
    useful work-path link, not a compiler-confirmed CALLS edge.
    """
    if (not receiver.startswith("current_app.") or receiver.count(".") != 1
            or not implementation_path.endswith(".py") or not blueprint_export
            or not implementation_class.isidentifier()):
        return None
    receiver_attribute = receiver.partition(".")[2]
    if not receiver_attribute.isidentifier():
        return None
    manager_module = implementation_path[:-3].replace("/", ".")
    blueprint_module = route_path[:-3].replace("/", ".")
    for candidate in paths:
        if not candidate.endswith(("/main.py", "/app.py", "/factory.py")):
            continue
        source = source_read(candidate)
        if not source or receiver_attribute not in source:
            continue
        try:
            tree = ast.parse(source)
        except (SyntaxError, ValueError):
            continue
        manager_names = {
            alias.asname or alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == manager_module
            for alias in node.names if alias.name == implementation_class
        }
        blueprint_names = {
            alias.asname or alias.name for node in ast.walk(tree)
            if isinstance(node, ast.ImportFrom) and node.module == blueprint_module
            for alias in node.names if alias.name == blueprint_export
        }
        if not manager_names or not blueprint_names:
            continue
        # These operations must happen inside the *same* app factory.
        # Merely finding an assignment and an unrelated blueprint registration
        # in two different functions is insufficient even for source wiring.
        for factory in tree.body:
            if not isinstance(factory, (ast.FunctionDef, ast.AsyncFunctionDef)):
                continue
            statements = [child for node in factory.body for child in ast.walk(node)]
            factory_assignments = [
                node for node in statements
                if isinstance(node, ast.Assign)
                and any(isinstance(target, ast.Attribute)
                        and isinstance(target.value, ast.Name) and target.value.id == "app"
                        and target.attr == receiver_attribute for target in node.targets)
                and isinstance(node.value, ast.Call)
                and isinstance(node.value.func, ast.Name)
                and node.value.func.id in manager_names
            ]
            registrations = [
                node for node in statements
                if isinstance(node, ast.Call) and isinstance(node.func, ast.Attribute)
                and isinstance(node.func.value, ast.Name)
                and node.func.value.id == "app" and node.func.attr == "register_blueprint"
                and node.args and isinstance(node.args[0], ast.Name)
                and node.args[0].id in blueprint_names
            ]
            if factory_assignments and registrations:
                return {
                    "path": candidate,
                    "line": factory_assignments[0].lineno,
                    "evidence": "AST-verified Flask factory imported implementation, attribute assignment and blueprint registration; runtime dispatch unconfirmed",
                    "method": method,
                }
    return None


def _entry(path: str, *, relation: str, evidence: str, symbol: str = "", line: int = 0) -> dict[str, Any]:
    item: dict[str, Any] = {"path": path, "relation": relation, "evidence": evidence}
    if symbol:
        item["symbol"] = symbol
    if line:
        item["line"] = line
    return item


def _dedupe_entries(entries: list[dict[str, Any]], *, preferred_symbol: str = "") -> tuple[list[dict[str, Any]], int]:
    # Multiple relevant tests in one file should not create one item per test.
    merged: dict[str, dict[str, Any]] = {}
    for item in entries:
        key = str(item.get("path") or "")
        previous = merged.get(key)
        if previous is None or (previous["relation"] == "candidate" and item["relation"] != "candidate"):
            merged[key] = item
    # Keep the queried symbol ahead of tangential matches. A route's primary
    # implementation is the first navigation action, not alphabetic filename.
    result = list(merged.values())
    result.sort(key=lambda item: (
        0 if item.get("symbol", "").rsplit(".", 1)[-1] == preferred_symbol else 1,
        0 if item["relation"] == "confirmed" else 1,
        item["path"],
    ))
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
    all_files = sorted(graph_files | changed)
    # Honor an explicit file before global file count, byte or sort order.
    # A current file missing from a stale Graph is still a valid exact seed.
    if in_file:
        if not (target.root_path / in_file).is_file() or (target.root_path / in_file).is_symlink():
            return {"status": "not_found", "query": {"in_file": in_file},
                    "warnings": ["Exact file does not exist in the selected repository"]}
        files = [in_file, *(path for path in all_files if path != in_file)]
    else:
        files = all_files
    bounded_files = files[:MAX_WORKFLOW_SOURCE_FILES]
    incomplete_inventory = len(files) > len(bounded_files)
    seed_files = [in_file] if in_file else bounded_files
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
            primary_name = route.strip("/").split("/")[-1]
            for method, receiver in sorted(method_names):
                # A route answer starts with the operation requested, not
                # every metrics/logging/helper side effect in the handler.
                if method != primary_name:
                    continue
                matches = [
                    (sid, info) for sid, info in symbols.items()
                    if info["path"] and info["path"] != path
                    and info["name"].endswith("." + method)
                    and not is_test_path(info["path"])
                ]
                # Stale symbol bytes cannot establish a call. For a source
                # candidate we require the current file to still define that
                # qualified function before suggesting it.
                def has_current_method(info: dict[str, str]) -> bool:
                    current_text = source_read(info["path"])
                    if not current_text:
                        return False
                    try:
                        parsed = ast.parse(current_text)
                    except (SyntaxError, ValueError):
                        return False
                    class_name = info["name"].split(".", 1)[0]
                    return any(
                        isinstance(cls, ast.ClassDef) and cls.name == class_name
                        and any(isinstance(member, (ast.FunctionDef, ast.AsyncFunctionDef)) and member.name == method
                                for member in cls.body)
                        for cls in ast.walk(parsed)
                    )
                matches = [(sid, info) for sid, info in matches if has_current_method(info)]
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
                    blueprint_export = next((
                        decorator.func.value.id
                        for decorator in func.decorator_list
                        if isinstance(decorator, ast.Call)
                        and isinstance(decorator.func, ast.Attribute)
                        and decorator.func.attr in _ROUTE_METHODS
                        and isinstance(decorator.func.value, ast.Name)
                    ), "")
                    wiring = _flask_factory_attribute_wiring(
                        bounded_files, source_read, route_path=path,
                        implementation_path=info["path"], receiver=receiver,
                        method=method, blueprint_export=blueprint_export,
                        implementation_class=info["name"].split(".", 1)[0],
                    )
                    if wiring:
                        implementations.append(_entry(
                            info["path"], relation="source", evidence=wiring["evidence"], symbol=info["name"],
                        ))
                        links.extend([
                            {"from": path, "to": wiring["path"], "relation": "source", "evidence": "Flask blueprint registration"},
                            {"from": wiring["path"], "to": info["path"], "relation": "source", "evidence": "Imported implementation constructed and assigned to current_app receiver property", "line": wiring["line"]},
                        ])
                    else:
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
            # Rust `mod audio_clock;` declares the module file, rather than
            # implementing the code inside the declaring main.rs.
            if info.get("kind") == "module":
                linked_paths = {
                    str(nodes[edge.to_id].identity.get("path") or "")
                    for edge in snapshot.edges
                    if edge.kind == "IMPORTS_FILE" and edge.source == "tree_sitter_structural"
                    and edge.from_id in nodes and edge.to_id in nodes
                    and str(nodes[edge.from_id].identity.get("path") or "") == info["path"]
                    and (
                        Path(str(nodes[edge.to_id].identity.get("path") or "")).stem == symbol
                        or str(nodes[edge.to_id].identity.get("path") or "").endswith("/"+symbol+"/mod.rs")
                    )
                }
                if len(linked_paths) == 1:
                    implementations.append(_entry(next(iter(linked_paths)), relation="confirmed",
                                                  evidence="resolved Rust mod file", symbol=info["name"]))
                    continue
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
                # Dart can remain queryable as source evidence even on a
                # machine without a Dart SDK/analyzer. Only the compiler
                # provider may elevate a Dart symbol to `confirmed`.
                if suffix not in _FALLBACK_SUFFIXES and suffix != ".dart" and not (
                    str(freshness.get("status") or "current") != "current" and suffix == ".py"
                ):
                    continue
                source = source_read(path)
                matched_line = next((i for i, line in enumerate(source.splitlines(), 1) if needle.search(line)), 0)
                if matched_line or symbol.casefold() in Path(path).stem.casefold():
                    if suffix == ".rs" and re.search(
                        r"(?m)^\s*(?:pub(?:\([^)]*\))?\s+)?(?:mod\s+" + re.escape(symbol.rsplit(".", 1)[-1])
                        + r"\s*;|use\s+[^;]*\b" + re.escape(symbol.rsplit(".", 1)[-1]) + r"\s*::)",
                        source,
                    ) and Path(path).stem != symbol.rsplit(".", 1)[-1]:
                        dependents.append(_entry(path, relation="source", evidence="Rust module/use declaration", symbol=symbol, line=matched_line))
                    elif not is_test_path(path):
                        # A matched basename is not a Rust module for other
                        # languages. Dart `void main()` can be proven by
                        # current source syntax, but a filename match alone
                        # is merely a candidate, never a verified call edge.
                        main_name = symbol.rsplit(".", 1)[-1]
                        rust_module = suffix == ".rs" and Path(path).stem == main_name
                        dart_top_level = (
                            suffix == ".dart"
                            and bool(re.search(
                                r"(?m)^(?:(?:Future\s*<\s*void\s*>|void|dynamic|int|String)\s+)"
                                + re.escape(main_name) + r"\s*\(", source,
                            ))
                        )
                        implementations.append(_entry(
                            path,
                            relation="source" if rust_module or dart_top_level else "candidate",
                            symbol=symbol, line=matched_line,
                            evidence=(
                                "Rust module source file" if rust_module
                                else "Dart top-level function declaration" if dart_top_level
                                else "current source text or module name; semantic relation unconfirmed"
                            ),
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
                if route and from_path.endswith(".py") and not _python_test_exercises_operation(
                    source_read(from_path), method=route.strip("/").split("/")[-1], route=route,
                ):
                    continue
                tests.append(_entry(from_path, relation="confirmed", evidence="IMPORTS_FILE"))
            else:
                dependents.append(_entry(from_path, relation="confirmed", evidence="IMPORTS_FILE"))
        if edge.kind == "TESTS_FILE" and to_path in selected_files:
            if route and from_path.endswith(".py") and not _python_test_exercises_operation(
                source_read(from_path), method=route.strip("/").split("/")[-1], route=route,
            ):
                continue
            tests.append(_entry(from_path, relation="confirmed", evidence="TESTS_FILE"))
    # Rust often embeds tests inside a production `lib.rs` rather than a
    # separate tests/ file. A named #[test] function mentioning this exact
    # component is useful *candidate* navigation, not a typed tested-by edge.
    # Keep this independent of symbol lookup: a confirmed Rust `mod` import
    # must not hide valid embedded test locations.
    if symbol and any(item["path"].endswith(".rs") for item in implementations):
        component = symbol.rsplit(".", 1)[-1]
        if re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", component):
            token = re.compile(r"(?<![A-Za-z0-9])" + re.escape(component) + r"(?![A-Za-z0-9])")
            rust_test = re.compile(
                r"(?m)#\[(?:[A-Za-z_][A-Za-z0-9_]*::)?test\]"
                r"\s*(?:pub(?:\([^)]*\))?\s+)?(?:async\s+)?fn\s+([A-Za-z_][A-Za-z0-9_]*)"
            )
            for candidate in bounded_files:
                if not candidate.endswith(".rs") or candidate in stale:
                    continue
                content = source_read(candidate)
                for found in rust_test.finditer(content):
                    if not token.search(found.group(1)):
                        continue
                    tests.append(_entry(
                        candidate, relation="candidate",
                        evidence="Rust embedded #[test] name references component; test relation unconfirmed",
                        symbol=found.group(1),
                        line=content.count("\n", 0, found.start()) + 1,
                    ))
                    break

    # A Dart test imports the owning library, which can include another
    # physical file through a verified `part`/`part of` declaration. The
    # two-hop test -> library -> part path is typed source evidence rather
    # than a similar basename guess; do not claim individual assertions ran.
    dart_part_owners: set[str] = set()
    for edge in snapshot.edges:
        if edge.kind != "IMPORTS_FILE" or edge.facts.get("match_kind") != "dart_part_directive":
            continue
        owner_node, part_node = nodes.get(edge.from_id), nodes.get(edge.to_id)
        owner_path = str(owner_node.identity.get("path") or "") if owner_node else ""
        part_path = str(part_node.identity.get("path") or "") if part_node else ""
        if part_path in selected_files and owner_path and owner_path not in stale and part_path not in stale:
            dart_part_owners.add(owner_path)
    if dart_part_owners:
        for edge in snapshot.edges:
            if edge.kind != "TESTS_FILE":
                continue
            test_node, owner_node = nodes.get(edge.from_id), nodes.get(edge.to_id)
            test_path = str(test_node.identity.get("path") or "") if test_node else ""
            owner_path = str(owner_node.identity.get("path") or "") if owner_node else ""
            if test_path and test_path not in stale and owner_path in dart_part_owners and is_test_path(test_path):
                tests.append(_entry(test_path, relation="confirmed", evidence="TESTS_FILE through exact Dart library/part chain"))

    # Name-nearby test modules are navigation candidates, not confirmed
    # TESTS_FILE relations (particularly when the snapshot is stale).
    for item in implementations:
        impl_path = item["path"]
        if Path(impl_path).suffix.casefold() == ".dart":
            expected_stem = Path(impl_path).stem + "_test"
            for test_path in bounded_files:
                if is_test_path(test_path) and Path(test_path).stem == expected_stem:
                    tests.append(_entry(test_path, relation="candidate", evidence="matching Dart test module name"))
    # Verify direct Dart importers from current sibling source, including when
    # the snapshot's stored import edge is stale. This is a source-proven
    # module dependency, not an invented precise function CALLS edge.
    for implementation in implementations:
        implementation_path = str(implementation.get("path") or "")
        if not implementation_path.endswith(".dart"):
            continue
        module_name = Path(implementation_path).name
        parent = Path(implementation_path).parent
        for candidate in bounded_files:
            if candidate == implementation_path or Path(candidate).suffix != ".dart":
                continue
            if Path(candidate).parent != parent or is_test_path(candidate):
                continue
            text = source_read(candidate)
            pattern = r"(?m)^\s*(?:import|export|part)\s+['\"][^'\"]*" + re.escape(module_name) + r"['\"]"
            if re.search(pattern, text):
                dependents.append(_entry(candidate, relation="source", evidence="current Dart module import/export literal"))
    if route:
        # Check likely test locations first, including tests that exercise a
        # manager under concurrency without embedding the HTTP route literal.
        route_stem = route.strip("/").split("/")[-1]
        implementation_stems = {
            Path(item["path"]).stem for item in implementations if item.get("path")
        }
        direct_test_paths = [
            path for path in bounded_files
            if is_test_path(path) and path.endswith(".py")
            and (route_stem in Path(path).stem or any(stem in Path(path).stem for stem in implementation_stems))
        ]
        for path in direct_test_paths:
            text = source_read(path)
            if not _python_test_exercises_operation(text, method=route_stem, route=route):
                continue
            if route in text:
                tests.append(_entry(path, relation="candidate", evidence="HTTP route literal in current test source"))
            elif implementation_stems and text and any(stem in Path(path).stem for stem in implementation_stems):
                tests.append(_entry(path, relation="candidate", evidence="test module name matches current implementation; inspect assertions"))
        # A small workflow should not scan every unrelated test source by
        # default; missing tests are explicitly partial rather than zero.

    entrypoints, entry_count = _dedupe_entries(entrypoints)
    implementations, implementation_count = _dedupe_entries(
        implementations, preferred_symbol=route.strip("/").split("/")[-1] if route else symbol.rsplit(".", 1)[-1]
    )
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
    """Score required stages, false positives, provenance, and first source.

    Path recall is diagnostic only. A string candidate or a fabricated empty
    caller answer cannot meet a required compiler-confirmed relation stage.
    """
    expected = case.get("expected_paths") if isinstance(case.get("expected_paths"), dict) else {}
    stages = result.get("stages") if isinstance(result.get("stages"), dict) else {}
    aliases = {"entrypoint": "entrypoints", "current_source": "implementation"}
    required = case.get("required_stages") if isinstance(case.get("required_stages"), list) else []
    metrics: dict[str, Any] = {}
    total = hits = confirmed_hits = output_count = false_positive_count = 0
    for stage in sorted(expected):
        values = expected[stage]
        expected_paths = sorted({str(value) for value in values if isinstance(value, str) and value}) if isinstance(values, list) else []
        stage_key = aliases.get(stage, stage)
        observations = [entry for entry in stages.get(stage_key, []) if isinstance(entry, dict)]
        matches = {str(entry.get("path") or ""): entry for entry in observations if entry.get("path")}
        hit_paths = sorted(set(expected_paths) & set(matches))
        confirmed_paths = sorted(
            path for path in hit_paths if matches[path].get("relation") == "confirmed"
        )
        structural_paths = sorted(
            path for path in hit_paths if matches[path].get("relation") in {"source", "confirmed"}
        )
        false_positive_paths = sorted(set(matches) - set(expected_paths))
        total += len(expected_paths)
        hits += len(hit_paths)
        confirmed_hits += len(confirmed_paths)
        output_count += len(matches)
        false_positive_count += len(false_positive_paths)
        first_observed = str(observations[0].get("path") or "") if observations else ""
        metrics[stage] = {
            "expected_count": len(expected_paths),
            "found_count": len(hit_paths),
            "confirmed_count": len(confirmed_paths),
            "structural_count": len(structural_paths),
            "missing_paths": sorted(set(expected_paths) - set(matches)),
            "false_positive_paths": false_positive_paths[:MAX_WORKFLOW_STAGE_ENTRIES],
            "false_positive_count": len(false_positive_paths),
            "first_expected_path": expected_paths[0] if expected_paths else "",
            "first_observed_path": first_observed,
            "first_path_correct": bool(expected_paths and first_observed in expected_paths),
            "path_recall": round(len(hit_paths) / len(expected_paths), 4) if expected_paths else None,
            "path_precision": round(len(hit_paths) / len(matches), 4) if matches else None,
        }
    required_details: dict[str, dict[str, Any]] = {}
    for requested in required:
        stage = aliases.get(requested, requested)
        if stage == "refresh":
            refresh_proof = result.get("refresh") if isinstance(result.get("refresh"), dict) else {}
            evidence = refresh_proof.get("source_rechecked") is True and refresh_proof.get("status") == "current"
            required_details[requested] = {"status": "verified" if evidence else "not_evaluated", "covered": evidence, "precise": evidence}
            continue
        row = metrics.get(requested) or metrics.get(stage)
        if not row or row["expected_count"] == 0:
            required_details[requested] = {"status": "unscored_no_ground_truth", "covered": False, "precise": False}
            continue
        covered = row["found_count"] == row["expected_count"]
        # The concrete route decorator is a source-anchored entrypoint. For
        # implementation, callers and tests, source matches are not semantic
        # compiler-confirmed relations.
        precise = covered and (
            row["structural_count"] == row["expected_count"] if stage == "entrypoints"
            else row["confirmed_count"] == row["expected_count"]
        )
        required_details[requested] = {
            "status": "precise" if precise else "candidate_only" if covered else "missing",
            "covered": covered, "precise": precise,
        }
    all_covered = bool(required) and all(row["covered"] for row in required_details.values())
    all_precise = bool(required) and all(row["precise"] for row in required_details.values())
    allowed = case.get("expected_statuses") if isinstance(case.get("expected_statuses"), list) else []
    status_expected = result.get("status") in allowed
    primary_stage = "entrypoints" if "entrypoints" in expected else "implementation"
    primary_row = metrics.get(primary_stage) or {}
    first_path_correct = bool(primary_row.get("first_path_correct"))
    return {
        "case_id": str(case.get("id") or ""),
        "query_status": result.get("status") or "unavailable",
        "status_expected": status_expected,
        "required_stages": required_details,
        "required_stages_covered": all_covered,
        "required_stages_precise": all_precise,
        "first_path_correct": first_path_correct,
        "task_navigation_complete": status_expected and all_covered and all_precise and first_path_correct,
        "expected_path_count": total,
        "found_path_count": hits,
        "confirmed_path_count": confirmed_hits,
        "false_positive_count": false_positive_count,
        "false_positive_rate": round(false_positive_count / output_count, 4) if output_count else None,
        "path_recall": round(hits / total, 4) if total else None,
        "confirmed_path_recall": round(confirmed_hits / total, 4) if total else None,
        "stages": metrics,
        "limitations": list(result.get("warnings") or []),
    }

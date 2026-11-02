"""Language-native AST structural Graph provider, not a type checker.

Tree-sitter supplies actual definitions, source ranges and unambiguous
same-module direct calls across Rust, Go, Java, Kotlin, Swift. Anything that
requires compiler/type resolution is left unbound rather than fabricated.
"""
from __future__ import annotations

from collections import defaultdict
from bisect import bisect_right
from pathlib import Path
from typing import Any

from .code_index import CodeIndexEntry
from .graph_semantic_model import CapabilityEvidence, PreciseCall, PreciseSymbol, ProviderFailure, SemanticProviderResult, SourceAnchor
from .repositories import RepoTarget

PROVIDER = "tree_sitter_structural"
LANGUAGES = frozenset({"rust", "go", "java", "kotlin", "swift"})
# The index has one Java profile for both Java and Kotlin; use actual suffix.
EXTENSIONS = {".rs": "rust", ".go": "go", ".java": "java", ".kt": "kotlin", ".kts": "kotlin", ".swift": "swift"}
LANGUAGE_IDS = frozenset(EXTENSIONS.values())
MAX_SOURCE_BYTES = 2 * 1024 * 1024
MAX_AST_NODES = 150000  # bounded non-recursive AST walk
DEFINITION_KINDS = {
    "rust": {"function_item": "function", "struct_item": "type", "enum_item": "type", "trait_item": "type", "mod_item": "module", "type_item": "type"},
    "go": {"function_declaration": "function", "method_declaration": "method", "type_spec": "type"},
    "java": {"method_declaration": "method", "constructor_declaration": "constructor", "class_declaration": "type", "interface_declaration": "type", "enum_declaration": "type"},
    "kotlin": {"function_declaration": "function", "class_declaration": "type", "object_declaration": "type"},
    "swift": {"function_declaration": "function", "class_declaration": "type", "struct_declaration": "type", "protocol_declaration": "type", "enum_declaration": "type"},
}
CONTAINERS = {"impl_item", "class_declaration", "interface_declaration", "enum_declaration", "object_declaration", "struct_declaration", "protocol_declaration"}
CALL_NODES = {"call_expression", "method_invocation"}


def _node_text(source: bytes, node: Any) -> str:
    return source[node.start_byte:node.end_byte].decode("utf-8", errors="replace")


def _definition_name(node: Any, source: bytes) -> str:
    named = node.child_by_field_name("name")
    if named is None:
        for index in range(node.named_child_count):
            child = node.named_child(index)
            if child is not None and child.type in {
                "identifier", "field_identifier", "type_identifier", "simple_identifier"
            }:
                named = child
                break
    return _node_text(source, named) if named is not None else ""


def _container_name(node: Any, source: bytes) -> str:
    if node.type == "impl_item":
        type_node = node.child_by_field_name("type")
        if type_node is not None:
            return _node_text(source, type_node)
        return "impl"
    return _definition_name(node, source)


def _anchor(path: str, node: Any, line_starts: list[int]) -> SourceAnchor:
    # Some tree-sitter-language-pack node bindings crash while materializing
    # native Point objects on complex real-world Rust ASTs. Offsets are stable
    # and preserve the same byte-based source column semantics.
    start_byte, end_byte = node.start_byte, node.end_byte
    start_row = bisect_right(line_starts, start_byte) - 1
    end_row = bisect_right(line_starts, end_byte) - 1
    return SourceAnchor(
        path, start_row + 1, start_byte - line_starts[start_row],
        end_row + 1, end_byte - line_starts[end_row],
    )


def _direct_target(call: Any, source: bytes) -> str:
    if call.type == "method_invocation":
        if call.child_by_field_name("object") is not None:
            return ""
        return _definition_name(call, source)
    function = call.child_by_field_name("function")
    if function is None and call.named_child_count:
        function = call.named_child(0)
    if function is None or function.type not in {"identifier", "simple_identifier"}:
        return ""
    return _node_text(source, function)


# Local binding syntax is not a type/target resolution. A locally declared
# callable may shadow a same-file top-level function name: such a call is NOT
# provider-confirmed even if there is only one function of that name.
_LOCAL_BINDING_NODES = frozenset({
    "let_declaration", "short_var_declaration", "var_spec",
    "parameter", "parameter_declaration", "formal_parameter",
    "local_variable_declaration", "variable_declarator", "property_declaration",
})


def _local_binding_names(node: Any, source: bytes) -> set[str]:
    """Conservatively collect identifiers on a declaration's binding side."""
    if node.type not in _LOCAL_BINDING_NODES:
        return set()
    binding = (
        node.child_by_field_name("pattern")
        or node.child_by_field_name("left")
        or node.child_by_field_name("name")
    )
    if binding is None:
        # Rust `parameter` patterns and Java/Kotlin declarations may expose
        # their binding as an unnamed field but a named child identifier.
        for index in range(node.named_child_count):
            child = node.named_child(index)
            if child is not None and child.type in {
                "identifier", "simple_identifier", "variable_declarator", "identifier_list",
            }:
                binding = child
                break
    if binding is None:
        return set()
    names: set[str] = set()
    pending = [binding]
    budget = 0
    while pending:
        current = pending.pop()
        budget += 1
        if budget > 256:
            return {"*"}  # unknown binding shape: do not assert a local target
        if current.type in {"identifier", "simple_identifier"}:
            names.add(_node_text(source, current))
            continue
        for index in range(current.named_child_count - 1, -1, -1):
            child = current.named_child(index)
            if child is not None:
                pending.append(child)
    return names


def _unqualified_call_target_is_provable(
    caller: PreciseSymbol, callee: PreciseSymbol, language: str,
) -> bool:
    """Never resolve an implicit call across unrelated lexical scopes.

    Native AST names alone do not prove implicit receiver dispatch. Rust/Go
    member methods require explicit receiver syntax; Java/Kotlin/Swift
    implicit member calls are restricted to the same lexical container.
    """
    callee_scope = callee.qualified_name.rpartition(".")[0]
    caller_scope = caller.qualified_name.rpartition(".")[0]
    if language in {"rust", "go"}:
        # `fn work` at module root is callable as `work()`. `impl A::work`
        # and Go `(a A) work` are not free functions. Inline module imports
        # are deliberately unconfirmed without symbol scope resolution.
        return callee.kind == "function" and not callee_scope and not caller_scope
    if language == "java":
        return bool(callee_scope) and caller_scope == callee_scope and callee.kind == "method"
    if language in {"kotlin", "swift"}:
        return (not callee_scope and callee.kind == "function") or (
            bool(callee_scope) and caller_scope == callee_scope
            and callee.kind in {"function", "method"}
        )
    return False


def build_tree_sitter_semantics(
    *, target: RepoTarget, entries: list[CodeIndexEntry],
    analysis_paths: set[str] | None = None,
) -> SemanticProviderResult:
    eligible = sorted(
        (
            entry for entry in entries
            if entry.classification != "excluded"
            and Path(entry.path).suffix.casefold() in EXTENSIONS
            and (analysis_paths is None or entry.path in analysis_paths)
        ), key=lambda item: item.path,
    )
    if not eligible:
        return SemanticProviderResult(provider=PROVIDER, languages=tuple(sorted(LANGUAGE_IDS)))
    try:
        from tree_sitter_language_pack import get_parser
    except ImportError:
        missing = tuple(entry.path for entry in eligible)
        return SemanticProviderResult(
            provider=PROVIDER, languages=tuple(sorted(LANGUAGE_IDS)),
            symbol_failed_paths=missing, call_failed_paths=missing,
            failures=(ProviderFailure(PROVIDER, "symbols,calls", "tree_sitter_missing", "tree-sitter language pack is not installed", missing),),
        )
    parsers: dict[str, Any] = {}
    analyzed: list[str] = []
    failed: list[str] = []
    all_symbols: list[PreciseSymbol] = []
    all_calls: list[PreciseCall] = []
    module_relations: list[dict[str, object]] = []
    eligible_paths = {entry.path for entry in eligible}
    for entry in eligible:
        path = entry.path
        language = EXTENSIONS[Path(path).suffix.casefold()]
        source_path = target.root_path / path
        try:
            # No symlink or out-of-target source is eligible as code evidence.
            resolved = source_path.resolve(strict=True)
            if target.root_path.resolve(strict=True) not in (resolved, *resolved.parents) or source_path.is_symlink():
                raise ValueError("symbol source escapes selected repository")
            size = source_path.stat().st_size
            if size > MAX_SOURCE_BYTES:
                raise ValueError("source exceeds 2MiB AST budget")
            source = source_path.read_bytes()
            line_starts = [0, *(i + 1 for i, byte in enumerate(source) if byte == 10)]
            if language not in parsers:
                parsers[language] = get_parser(language)
            parser = parsers[language]
            tree = parser.parse(source)
        except (OSError, UnicodeError, ValueError, RuntimeError):
            failed.append(path)
            continue
        if tree.root_node.has_error:
            failed.append(path)
            # A syntax-invalid source may still yield nodes, but the Graph
            # must not present them as resolved semantic relations.
            continue
        symbols: list[PreciseSymbol] = []
        direct_calls: list[tuple[PreciseSymbol, Any, str]] = []
        local_bindings: dict[str, set[str]] = defaultdict(set)
        relation_offset = len(module_relations)
        try:
            # Real Rust repositories contain many nested expressions. A
            # recursive walk through tree-sitter Node.named_children can
            # segfault in native bindings before Python raises RecursionError.
            # Indexed child iteration keeps the C stack bounded, with source
            # scope and callable identity carried as ordinary Python values.
            pending: list[tuple[Any, PreciseSymbol | None, tuple[str, ...]]] = [
                (tree.root_node, None, ())
            ]
            node_count = 0
            while pending:
                node, current_function, scopes = pending.pop()
                node_count += 1
                if node_count > MAX_AST_NODES:
                    raise ValueError("source AST exceeds bounded walk budget")
                is_container = node.type in CONTAINERS
                container = _container_name(node, source) if is_container else ""
                inner_scopes = (*scopes, container) if container else scopes
                kind = DEFINITION_KINDS[language].get(node.type)
                name = _definition_name(node, source) if kind else ""
                discovered = current_function
                if node.type == "mod_item" and name and path.endswith(('/main.rs', '/lib.rs', '/mod.rs')):
                    # A Rust `mod name;` references a sibling module file;
                    # inline modules have no separate file target.
                    if _node_text(source, node).rstrip().endswith(';'):
                        parent = Path(path).parent
                        module_candidates = [
                            (parent / f"{name}.rs").as_posix(),
                            (parent / name / "mod.rs").as_posix(),
                        ]
                        targets = [candidate for candidate in module_candidates if candidate in eligible_paths]
                        if len(targets) == 1:
                            module_relations.append({
                                "importer_path": path, "target_path": targets[0],
                                "anchor": _anchor(path, node, line_starts).to_dict(),
                                "kind": "rust_mod_declaration",
                            })
                if kind and name:
                    qualified_parts = list(inner_scopes)
                    if not (is_container and container == name):
                        qualified_parts.append(name)
                    qualified = ".".join(qualified_parts)
                    symbol = PreciseSymbol(
                        path=path, provider=PROVIDER,
                        provider_symbol_id=f"{path}:{node.start_byte}:{qualified}",
                        language=language, kind=kind, name=name,
                        qualified_name=qualified, anchor=_anchor(path, node, line_starts),
                    )
                    symbols.append(symbol)
                    if kind in {"function", "method", "constructor"}:
                        discovered = symbol
                if discovered is not None and node.type in _LOCAL_BINDING_NODES:
                    local_bindings[discovered.provider_symbol_id].update(_local_binding_names(node, source))
                if node.type in CALL_NODES and discovered is not None:
                    target_name = _direct_target(node, source)
                    if target_name:
                        direct_calls.append((discovered, node, target_name))
                for index in range(node.named_child_count - 1, -1, -1):
                    child = node.named_child(index)
                    if child is not None:
                        pending.append((child, discovered, inner_scopes))
        except (ValueError, RuntimeError):
            # Discard this file's partial facts; an incomplete traversal is
            # never presented as precise source or call coverage.
            del module_relations[relation_offset:]
            failed.append(path)
            continue
        analyzed.append(path)
        all_symbols.extend(symbols)
        by_name: dict[str, list[PreciseSymbol]] = defaultdict(list)
        for item in symbols:
            if item.kind in {"function", "method", "constructor"}:
                by_name[item.name].append(item)
        # Only a unique direct unqualified local target is proven by syntax.
        # Member calls, imported names, overloads and dynamic dispatch are
        # not made into precise Graph edges by name equality.
        for caller, call_node, target_name in direct_calls:
            shadowed = local_bindings.get(caller.provider_symbol_id, set())
            if target_name in shadowed or "*" in shadowed:
                continue
            candidates = by_name.get(target_name, [])
            if (
                len(candidates) == 1
                and caller.provider_symbol_id != candidates[0].provider_symbol_id
                and _unqualified_call_target_is_provable(caller, candidates[0], language)
            ):
                callee = candidates[0]
                all_calls.append(PreciseCall(
                    path=path, provider=PROVIDER,
                    caller_provider_symbol_id=caller.provider_symbol_id,
                    callee_provider_symbol_id=callee.provider_symbol_id,
                    language=language, scope="same_file", anchor=_anchor(path, call_node, line_starts),
                ))
    failure_paths = tuple(sorted(failed))
    return SemanticProviderResult(
        provider=PROVIDER, languages=tuple(sorted(LANGUAGE_IDS)),
        symbols=tuple(all_symbols), calls=tuple(all_calls),
        symbol_analyzed_paths=tuple(analyzed), call_analyzed_paths=tuple(analyzed),
        symbol_failed_paths=failure_paths, call_failed_paths=failure_paths,
        failures=(ProviderFailure(PROVIDER, "symbols,calls", "tree_sitter_source_partial", "Some sources could not be parsed structurally", failure_paths),) if failure_paths else (),
        symbol_coverage=CapabilityEvidence("conservative", ("syntax_is_not_type_resolution",)),
        call_coverage=CapabilityEvidence("conservative", ("cross_file_and_receiver_dispatch_unresolved",)),
        tool={"kind": "tree_sitter_language_pack", "parser_languages": sorted(LANGUAGE_IDS),
              "module_file_relations": sorted(module_relations, key=lambda item: (str(item["importer_path"]),str(item["target_path"]))),
              "precision": "syntax_definitions_unambiguous_rust_module_edges_and_unique_unqualified_same_file_calls_only"},
    )

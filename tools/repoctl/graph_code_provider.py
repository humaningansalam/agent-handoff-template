from __future__ import annotations

import ast
from dataclasses import dataclass, field
from pathlib import Path

from .code_index import CodeIndexEntry
from .graph_import_resolver import ImportResolution
from .repositories import RepoTarget


PYTHON_PROVIDER_LANGUAGES = frozenset({"python"})


@dataclass(frozen=True)
class SourceAnchor:
    path: str
    start_line: int
    start_col: int
    end_line: int
    end_col: int

    def to_dict(self) -> dict[str, int | str]:
        return {
            "path": self.path,
            "start_line": self.start_line,
            "start_col": self.start_col,
            "end_line": self.end_line,
            "end_col": self.end_col,
        }


@dataclass(frozen=True)
class PreciseSymbol:
    path: str
    provider: str
    provider_symbol_id: str
    language: str
    kind: str
    name: str
    qualified_name: str
    anchor: SourceAnchor

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "provider": self.provider,
            "provider_symbol_id": self.provider_symbol_id,
            "language": self.language,
            "kind": self.kind,
            "name": self.name,
            "qualified_name": self.qualified_name,
            "anchor": self.anchor.to_dict(),
        }


@dataclass(frozen=True)
class PreciseCall:
    path: str
    provider: str
    caller_provider_symbol_id: str
    callee_provider_symbol_id: str
    language: str
    scope: str
    anchor: SourceAnchor

    def to_dict(self) -> dict[str, object]:
        return {
            "path": self.path,
            "provider": self.provider,
            "caller_provider_symbol_id": self.caller_provider_symbol_id,
            "callee_provider_symbol_id": self.callee_provider_symbol_id,
            "language": self.language,
            "scope": self.scope,
            "anchor": self.anchor.to_dict(),
        }


@dataclass(frozen=True)
class _Binding:
    kind: str
    symbol: PreciseSymbol | None = None
    alias_name: str = ""
    raw_import: str = ""
    imported_name: str = ""


@dataclass
class _Scope:
    kind: str
    name: str
    qualified_name: str
    parent: _Scope | None
    node: ast.AST
    symbol: PreciseSymbol | None = None
    bindings: dict[str, list[_Binding]] = field(default_factory=dict)
    global_names: set[str] = field(default_factory=set)
    nonlocal_names: set[str] = field(default_factory=set)
    children: list[_Scope] = field(default_factory=list)


@dataclass(frozen=True)
class _ModuleAnalysis:
    path: str
    tree: ast.Module
    root_scope: _Scope
    scopes: tuple[_Scope, ...]
    symbols: tuple[PreciseSymbol, ...]


def _anchor_for(path: str, node: ast.AST) -> SourceAnchor:
    return SourceAnchor(
        path=path,
        start_line=int(getattr(node, "lineno", 0) or 0),
        start_col=int(getattr(node, "col_offset", 0) or 0),
        end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 0)) or 0),
        end_col=int(getattr(node, "end_col_offset", getattr(node, "col_offset", 0)) or 0),
    )


class _ScopeBindingCollector(ast.NodeVisitor):
    def __init__(self) -> None:
        self.definitions: list[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef] = []
        self.local_names: set[str] = set()
        self.aliases: list[tuple[str, str]] = []
        self.imports: list[tuple[str, str, str]] = []
        self.global_names: set[str] = set()
        self.nonlocal_names: set[str] = set()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.definitions.append(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.definitions.append(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.definitions.append(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_ListComp(self, node: ast.ListComp) -> None:
        return

    def visit_SetComp(self, node: ast.SetComp) -> None:
        return

    def visit_DictComp(self, node: ast.DictComp) -> None:
        return

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        return

    def visit_Global(self, node: ast.Global) -> None:
        self.global_names.update(node.names)

    def visit_Nonlocal(self, node: ast.Nonlocal) -> None:
        self.nonlocal_names.update(node.names)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            self.local_names.add(alias.asname or alias.name.split(".", 1)[0])

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = "." * node.level
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            raw_import = f"{prefix}{module}.{alias.name}" if module else f"{prefix}{alias.name}"
            self.imports.append((alias.asname or alias.name, raw_import, alias.name))

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Name):
            self.aliases.append((node.targets[0].id, node.value.id))
        else:
            for target in node.targets:
                self.local_names.update(_target_names(target))
        self.visit(node.value)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.local_names.add(node.name)
        if node.type is not None:
            self.visit(node.type)
        for statement in node.body:
            self.visit(statement)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Store):
            self.local_names.add(node.id)


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    if isinstance(node, ast.Tuple | ast.List):
        return {name for item in node.elts for name in _target_names(item)}
    return set()


def _function_parameters(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
    if node.args.vararg is not None:
        names.add(node.args.vararg.arg)
    if node.args.kwarg is not None:
        names.add(node.args.kwarg.arg)
    return names


def _module_scope(scope: _Scope) -> _Scope:
    current = scope
    while current.parent is not None:
        current = current.parent
    return current


def _binding_owner(scope: _Scope, name: str) -> _Scope:
    if scope.kind != "module" and name in scope.global_names:
        return _module_scope(scope)
    if name in scope.nonlocal_names:
        current = scope.parent
        while current is not None:
            if current.kind == "function" and name in current.bindings:
                return current
            current = current.parent
    return scope


def _add_binding(scope: _Scope, name: str, binding: _Binding) -> None:
    values = scope.bindings.setdefault(name, [])
    if binding not in values:
        values.append(binding)


def _symbol_for(path: str, scope: _Scope, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef) -> PreciseSymbol:
    qualified_name = ".".join(part for part in (scope.qualified_name, node.name) if part)
    if isinstance(node, ast.ClassDef):
        kind = "class"
    else:
        kind = "method" if scope.kind == "class" else "function"
    anchor = _anchor_for(path, node)
    provider_symbol_id = f"python_ast:{path}:{qualified_name}:{kind}:{anchor.start_line}:{anchor.start_col}:{anchor.end_line}:{anchor.end_col}"
    return PreciseSymbol(
        path=path,
        provider="python_ast",
        provider_symbol_id=provider_symbol_id,
        language="python",
        kind=kind,
        name=node.name,
        qualified_name=qualified_name,
        anchor=anchor,
    )


def _populate_scope(path: str, scope: _Scope, body: list[ast.stmt], scopes: list[_Scope], symbols: list[PreciseSymbol]) -> None:
    collector = _ScopeBindingCollector()
    for statement in body:
        collector.visit(statement)
    scope.global_names = collector.global_names
    scope.nonlocal_names = collector.nonlocal_names

    if isinstance(scope.node, ast.FunctionDef | ast.AsyncFunctionDef):
        for name in sorted(_function_parameters(scope.node)):
            _add_binding(scope, name, _Binding("local"))
    for name in sorted(collector.local_names):
        _add_binding(_binding_owner(scope, name), name, _Binding("local"))
    for name, alias_name in collector.aliases:
        owner = _binding_owner(scope, name)
        binding = _Binding("alias", alias_name=alias_name) if scope.kind == "module" else _Binding("local")
        _add_binding(owner, name, binding)
    for name, raw_import, imported_name in collector.imports:
        owner = _binding_owner(scope, name)
        binding = (
            _Binding("import", raw_import=raw_import, imported_name=imported_name)
            if scope.kind == "module"
            else _Binding("local")
        )
        _add_binding(
            owner,
            name,
            binding,
        )

    for node in collector.definitions:
        symbol = _symbol_for(path, scope, node)
        symbols.append(symbol)
        _add_binding(_binding_owner(scope, node.name), node.name, _Binding("symbol", symbol=symbol))
        child = _Scope(
            kind="class" if isinstance(node, ast.ClassDef) else "function",
            name=node.name,
            qualified_name=symbol.qualified_name,
            parent=scope,
            node=node,
            symbol=symbol,
        )
        scope.children.append(child)
        scopes.append(child)
        _populate_scope(path, child, node.body, scopes, symbols)


def _analyze_module(path: str, text: str) -> _ModuleAnalysis:
    tree = ast.parse(text)
    root = _Scope("module", "", "", None, tree)
    scopes = [root]
    symbols: list[PreciseSymbol] = []
    _populate_scope(path, root, tree.body, scopes, symbols)
    return _ModuleAnalysis(path, tree, root, tuple(scopes), tuple(symbols))


class _CurrentScopeCallVisitor(ast.NodeVisitor):
    def __init__(self) -> None:
        self.calls: list[tuple[ast.Call, frozenset[str]]] = []
        self._shadowed_names: list[set[str]] = []

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        return

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        return

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        return

    def visit_Lambda(self, node: ast.Lambda) -> None:
        return

    def visit_Call(self, node: ast.Call) -> None:
        shadowed = frozenset(name for names in self._shadowed_names for name in names)
        self.calls.append((node, shadowed))
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, [node.key, node.value])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def _visit_comprehension(self, generators: list[ast.comprehension], values: list[ast.AST]) -> None:
        if not generators:
            for value in values:
                self.visit(value)
            return

        # Python evaluates the first iterable in the enclosing scope, then binds
        # comprehension targets in the child scope for filters and result values.
        self.visit(generators[0].iter)
        shadowed: set[str] = set()
        self._shadowed_names.append(shadowed)
        try:
            for index, generator in enumerate(generators):
                if index:
                    self.visit(generator.iter)
                shadowed.update(_target_names(generator.target))
                for condition in generator.ifs:
                    self.visit(condition)
            for value in values:
                self.visit(value)
        finally:
            self._shadowed_names.pop()


def _import_targets(
    analyses: list[_ModuleAnalysis],
    symbols: list[PreciseSymbol],
    import_resolutions: list[ImportResolution],
) -> dict[tuple[str, str, str], PreciseSymbol]:
    available_paths = {analysis.path for analysis in analyses}
    resolutions = {
        (resolution.importer_path, resolution.raw_import): resolution
        for resolution in import_resolutions
        if resolution.language == "python"
        and resolution.provider == "python_import_resolver"
        and resolution.importer_path in available_paths
        and resolution.target_path in available_paths
    }
    module_functions: dict[tuple[str, str], list[PreciseSymbol]] = {}
    for symbol in symbols:
        if symbol.kind == "function" and "." not in symbol.qualified_name:
            module_functions.setdefault((symbol.path, symbol.name), []).append(symbol)

    result: dict[tuple[str, str, str], PreciseSymbol] = {}
    for analysis in analyses:
        for scope in analysis.scopes:
            for bindings in scope.bindings.values():
                for binding in bindings:
                    if binding.kind != "import":
                        continue
                    resolution = resolutions.get((analysis.path, binding.raw_import))
                    if resolution is None:
                        continue
                    candidates = module_functions.get((resolution.target_path, binding.imported_name), [])
                    if len(candidates) == 1:
                        result[(analysis.path, binding.raw_import, binding.imported_name)] = candidates[0]
    return result


def _resolve_scope_binding(
    analysis: _ModuleAnalysis,
    scope: _Scope,
    name: str,
    import_targets: dict[tuple[str, str, str], PreciseSymbol],
    seen: set[tuple[int, str]],
    *,
    call_scope: _Scope,
    call_line: int,
) -> PreciseSymbol | None:
    bindings = scope.bindings.get(name, [])
    if len(bindings) != 1:
        return None
    binding = bindings[0]
    if binding.kind == "symbol":
        symbol = binding.symbol
        if symbol is not None and scope is call_scope and symbol.anchor.start_line > call_line:
            return None
        return symbol if symbol is not None and symbol.kind in {"function", "method"} else None
    if binding.kind == "import":
        return import_targets.get((analysis.path, binding.raw_import, binding.imported_name))
    if binding.kind == "alias" and binding.alias_name:
        return _resolve_name(
            analysis,
            scope,
            binding.alias_name,
            import_targets,
            seen,
            call_scope=call_scope,
            call_line=call_line,
        )
    return None


def _resolve_name(
    analysis: _ModuleAnalysis,
    scope: _Scope,
    name: str,
    import_targets: dict[tuple[str, str, str], PreciseSymbol],
    seen: set[tuple[int, str]] | None = None,
    *,
    call_scope: _Scope,
    call_line: int,
) -> PreciseSymbol | None:
    seen = seen or set()
    marker = (id(scope), name)
    if marker in seen:
        return None
    seen.add(marker)

    if scope.kind != "module" and name in scope.global_names:
        module = _module_scope(scope)
        return _resolve_scope_binding(
            analysis,
            module,
            name,
            import_targets,
            seen,
            call_scope=call_scope,
            call_line=call_line,
        )
    if name in scope.nonlocal_names:
        current = scope.parent
        while current is not None:
            if current.kind == "function" and name in current.bindings:
                return _resolve_scope_binding(
                    analysis,
                    current,
                    name,
                    import_targets,
                    seen,
                    call_scope=call_scope,
                    call_line=call_line,
                )
            current = current.parent
        return None
    if name in scope.bindings:
        return _resolve_scope_binding(
            analysis,
            scope,
            name,
            import_targets,
            seen,
            call_scope=call_scope,
            call_line=call_line,
        )

    current = scope.parent
    while current is not None:
        if current.kind == "function" and name in current.bindings:
            return _resolve_scope_binding(
                analysis,
                current,
                name,
                import_targets,
                seen,
                call_scope=call_scope,
                call_line=call_line,
            )
        if current.kind == "module":
            return _resolve_scope_binding(
                analysis,
                current,
                name,
                import_targets,
                seen,
                call_scope=call_scope,
                call_line=call_line,
            )
        current = current.parent
    return None


def _has_lexical_binding(scope: _Scope, name: str) -> bool:
    if name in scope.bindings:
        return True
    current = scope.parent
    while current is not None:
        if current.kind == "function" and name in current.bindings:
            return True
        if current.kind == "module":
            return name in current.bindings
        current = current.parent
    return False


def _self_method_target(scope: _Scope, node: ast.Attribute) -> PreciseSymbol | None:
    if not isinstance(node.value, ast.Name) or node.value.id != "self" or not _has_lexical_binding(scope, "self"):
        return None
    current = scope.parent
    while current is not None and current.kind != "class":
        current = current.parent
    if current is None:
        return None
    bindings = current.bindings.get(node.attr, [])
    if len(bindings) != 1 or bindings[0].kind != "symbol":
        return None
    symbol = bindings[0].symbol
    return symbol if symbol is not None and symbol.kind == "method" else None


def _calls_for_analysis(
    analysis: _ModuleAnalysis,
    import_targets: dict[tuple[str, str, str], PreciseSymbol],
) -> list[PreciseCall]:
    calls: list[PreciseCall] = []
    for scope in analysis.scopes:
        if scope.kind != "function" or scope.symbol is None:
            continue
        node = scope.node
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        visitor = _CurrentScopeCallVisitor()
        for statement in node.body:
            visitor.visit(statement)
        for call, shadowed_names in visitor.calls:
            callee: PreciseSymbol | None = None
            if isinstance(call.func, ast.Name) and call.func.id not in shadowed_names:
                callee = _resolve_name(
                    analysis,
                    scope,
                    call.func.id,
                    import_targets,
                    call_scope=scope,
                    call_line=int(getattr(call, "lineno", 0) or 0),
                )
            elif isinstance(call.func, ast.Attribute) and "self" not in shadowed_names:
                callee = _self_method_target(scope, call.func)
            if callee is None or callee.provider_symbol_id == scope.symbol.provider_symbol_id:
                continue
            calls.append(
                PreciseCall(
                    path=analysis.path,
                    provider="python_ast",
                    caller_provider_symbol_id=scope.symbol.provider_symbol_id,
                    callee_provider_symbol_id=callee.provider_symbol_id,
                    language="python",
                    scope="same_file" if callee.path == analysis.path else "cross_file_import",
                    anchor=_anchor_for(analysis.path, call),
                )
            )
    deduped = {
        (
            call.caller_provider_symbol_id,
            call.callee_provider_symbol_id,
            call.anchor.start_line,
            call.anchor.start_col,
        ): call
        for call in calls
    }
    return sorted(
        deduped.values(),
        key=lambda item: (
            item.caller_provider_symbol_id,
            item.callee_provider_symbol_id,
            item.anchor.start_line,
            item.anchor.start_col,
        ),
    )


def build_python_semantics(
    root: Path,
    *,
    target: RepoTarget,
    entries: list[CodeIndexEntry],
    import_resolutions: list[ImportResolution] | None = None,
) -> tuple[list[PreciseSymbol], list[PreciseCall], dict[str, object]]:
    del root
    analyses: list[_ModuleAnalysis] = []
    failed_paths: list[str] = []
    for entry in entries:
        if entry.language != "python":
            continue
        path = target.root_path / entry.path
        try:
            text = path.read_text(encoding="utf-8")
            analyses.append(_analyze_module(entry.path, text))
        except (OSError, UnicodeDecodeError, SyntaxError):
            failed_paths.append(entry.path)

    symbols = sorted(
        [symbol for analysis in analyses for symbol in analysis.symbols],
        key=lambda item: item.provider_symbol_id,
    )
    import_targets = _import_targets(analyses, symbols, import_resolutions or [])
    calls = sorted(
        [call for analysis in analyses for call in _calls_for_analysis(analysis, import_targets)],
        key=lambda item: (
            item.caller_provider_symbol_id,
            item.callee_provider_symbol_id,
            item.anchor.start_line,
            item.anchor.start_col,
        ),
    )
    meta = {
        "provider": "python_ast",
        "languages": sorted(PYTHON_PROVIDER_LANGUAGES),
        "analyzed_paths": sorted(analysis.path for analysis in analyses),
        "failed_paths": sorted(failed_paths),
        "symbol_count": len(symbols),
        "call_count": len(calls),
        "scopes": sorted({call.scope for call in calls}),
    }
    return symbols, calls, meta

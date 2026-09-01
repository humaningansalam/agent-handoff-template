from __future__ import annotations

import ast
import symtable
from dataclasses import dataclass, field
from pathlib import Path
from typing import Literal

from .code_index import CodeIndexEntry
from .graph_import_resolver import ImportResolution
from .graph_semantic_model import PreciseCall, PreciseSymbol, SourceAnchor
from .repositories import RepoTarget


PYTHON_PROVIDER_LANGUAGES = frozenset({"python"})
PYTHON_PROVIDER_INPUT_VERSION = 20


@dataclass(frozen=True)
class _Binding:
    kind: str
    symbol: PreciseSymbol | None = None
    alias_name: str = ""
    raw_import: str = ""
    import_form: str = ""
    import_module: str = ""
    imported_name: str = ""
    import_level: int = 0
    execution_certain: bool = True
    identity_certain: bool = True
    position: tuple[int, int] | None = None
    source_position: tuple[int, int] | None = None
    timeline_ordered: bool = True
    receiver_class_symbol_id: str = ""

    @property
    def target_certain(self) -> bool:
        return self.execution_certain and self.identity_certain


@dataclass(frozen=True)
class _BindingSelection:
    status: Literal["bound", "unbound", "ambiguous"]
    binding: _Binding | None = None


@dataclass(frozen=True)
class _ImportTargets:
    direct: dict[tuple[str, str, str], PreciseSymbol]
    module_paths: dict[tuple[str, str], str]
    module_callables: dict[tuple[str, str], tuple[PreciseSymbol, ...]]


@dataclass(frozen=True, order=True)
class PythonCallableExport:
    path: str
    name: str
    provider_symbol_id: str

    def to_dict(self) -> dict[str, str]:
        return {
            "path": self.path,
            "name": self.name,
            "provider_symbol_id": self.provider_symbol_id,
        }


@dataclass(frozen=True)
class _CallSite:
    target: ast.expr
    anchor: ast.AST
    blocked_names: frozenset[str]
    skip_current_scope: bool


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
        self.definitions: list[tuple[ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, bool]] = []
        self.local_bindings: list[tuple[str, bool, tuple[int, int]]] = []
        self.aliases: list[tuple[str, str, bool, tuple[int, int], tuple[int, int]]] = []
        self.imports: list[tuple[str, str, str, str, int, bool, tuple[int, int]]] = []
        self.module_imports: list[tuple[str, str, bool, bool, tuple[int, int]]] = []
        self.tombstones: list[tuple[str, bool, tuple[int, int]]] = []
        self._conditional_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self.definitions.append((node, self._conditional_depth == 0))
        self._visit_function_definition_expressions(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self.definitions.append((node, self._conditional_depth == 0))
        self._visit_function_definition_expressions(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self.definitions.append((node, self._conditional_depth == 0))
        for decorator in node.decorator_list:
            self.visit(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def _visit_conditional_suite(self, node: ast.AST) -> None:
        self._conditional_depth += 1
        try:
            self.generic_visit(node)
        finally:
            self._conditional_depth -= 1

    def visit_If(self, node: ast.If) -> None:
        self._visit_conditional_suite(node)

    def visit_For(self, node: ast.For) -> None:
        self._visit_conditional_suite(node)

    def visit_AsyncFor(self, node: ast.AsyncFor) -> None:
        self._visit_conditional_suite(node)

    def visit_While(self, node: ast.While) -> None:
        self._visit_conditional_suite(node)

    def visit_Try(self, node: ast.Try) -> None:
        self._visit_conditional_suite(node)

    def visit_TryStar(self, node: ast.TryStar) -> None:
        self._visit_conditional_suite(node)

    def visit_Match(self, node: ast.Match) -> None:
        self._visit_conditional_suite(node)

    def visit_With(self, node: ast.With) -> None:
        self._visit_conditional_suite(node)

    def visit_AsyncWith(self, node: ast.AsyncWith) -> None:
        self._visit_conditional_suite(node)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_argument_defaults(node.args)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension_binding_effects(node.generators, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension_binding_effects(node.generators, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension_binding_effects(node.generators, [node.key, node.value])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension_binding_effects(node.generators, [node.elt])

    def _visit_function_definition_expressions(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self.visit(decorator)
        self._visit_argument_defaults(node.args)

    def _visit_argument_defaults(self, arguments: ast.arguments) -> None:
        for default in arguments.defaults:
            self.visit(default)
        for default in arguments.kw_defaults:
            if default is not None:
                self.visit(default)

    def _visit_comprehension_binding_effects(
        self,
        generators: list[ast.comprehension],
        values: list[ast.AST],
    ) -> None:
        for generator in generators:
            self.visit(generator.iter)
            for condition in generator.ifs:
                self.visit(condition)
        for value in values:
            self.visit(value)

    def visit_Import(self, node: ast.Import) -> None:
        for alias in node.names:
            bound_name = alias.asname or alias.name.split(".", 1)[0]
            self.module_imports.append(
                (
                    bound_name,
                    alias.name,
                    self._conditional_depth == 0,
                    alias.asname is not None or "." not in alias.name,
                    _node_end_position(node),
                )
            )

    def visit_ImportFrom(self, node: ast.ImportFrom) -> None:
        prefix = "." * node.level
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            raw_import = f"{prefix}{module}.{alias.name}" if module else f"{prefix}{alias.name}"
            self.imports.append(
                (
                    alias.asname or alias.name,
                    raw_import,
                    alias.name,
                    module,
                    node.level,
                    self._conditional_depth == 0,
                    _node_end_position(node),
                )
            )

    def visit_Assign(self, node: ast.Assign) -> None:
        if len(node.targets) == 1 and isinstance(node.targets[0], ast.Name) and isinstance(node.value, ast.Name):
            self.aliases.append(
                (
                    node.targets[0].id,
                    node.value.id,
                    self._conditional_depth == 0,
                    _node_end_position(node),
                    _node_position(node.value),
                )
            )
        else:
            for target in node.targets:
                self.visit(target)
        self.visit(node.value)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        if isinstance(node.target, ast.Name) and isinstance(node.value, ast.Name):
            self.aliases.append(
                (
                    node.target.id,
                    node.value.id,
                    self._conditional_depth == 0,
                    _node_end_position(node),
                    _node_position(node.value),
                )
            )
        else:
            self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_ExceptHandler(self, node: ast.ExceptHandler) -> None:
        if node.name:
            self.local_bindings.append((node.name, self._conditional_depth == 0, _node_position(node)))
        if node.type is not None:
            self.visit(node.type)
        for statement in node.body:
            self.visit(statement)

    def visit_Name(self, node: ast.Name) -> None:
        if isinstance(node.ctx, ast.Del):
            self.tombstones.append((node.id, self._conditional_depth == 0, _node_end_position(node)))
        elif isinstance(node.ctx, ast.Store):
            self.local_bindings.append((node.id, self._conditional_depth == 0, _node_end_position(node)))


def _target_names(node: ast.AST) -> set[str]:
    if isinstance(node, ast.Name):
        return {node.id}
    if isinstance(node, ast.Starred):
        return _target_names(node.value)
    if isinstance(node, ast.Tuple | ast.List):
        return {name for item in node.elts for name in _target_names(item)}
    return set()


def _node_position(node: ast.AST) -> tuple[int, int]:
    return (
        int(getattr(node, "lineno", 0) or 0),
        int(getattr(node, "col_offset", 0) or 0),
    )


def _node_end_position(node: ast.AST) -> tuple[int, int]:
    return (
        int(getattr(node, "end_lineno", getattr(node, "lineno", 0)) or 0),
        int(getattr(node, "end_col_offset", getattr(node, "col_offset", 0)) or 0),
    )


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


def _scope_executes_in_owner(scope: _Scope, owner: _Scope) -> bool:
    current = scope
    while current is not owner:
        parent = current.parent
        if current.kind != "class" or parent is None or current.symbol is None:
            return False
        defining_bindings = [
            binding
            for binding in parent.bindings.get(current.name, [])
            if binding.kind == "symbol"
            and binding.symbol is not None
            and binding.symbol.provider_symbol_id == current.symbol.provider_symbol_id
        ]
        if len(defining_bindings) != 1 or not defining_bindings[0].execution_certain:
            return False
        current = parent
    return True


def _binding_execution_certain(scope: _Scope, owner: _Scope, statement_certain: bool) -> bool:
    return statement_certain and _scope_executes_in_owner(scope, owner)


def _add_binding(scope: _Scope, name: str, binding: _Binding) -> None:
    values = scope.bindings.setdefault(name, [])
    if binding not in values:
        values.append(binding)


def _earliest_position(
    first: tuple[int, int] | None,
    second: tuple[int, int] | None,
) -> tuple[int, int] | None:
    if first is None:
        return second
    if second is None:
        return first
    return min(first, second)


def _binding_selection(
    scope: _Scope,
    name: str,
    *,
    before: tuple[int, int] | None = None,
) -> _BindingSelection:
    bindings = scope.bindings.get(name, [])
    if not bindings:
        return _BindingSelection("unbound")
    if any(not binding.timeline_ordered for binding in bindings):
        return _BindingSelection("ambiguous")
    baselines = [binding for binding in bindings if binding.position is None]
    events = [
        binding
        for binding in bindings
        if binding.position is not None and (before is None or binding.position < before)
    ]
    if events:
        latest_position = max(binding.position for binding in events if binding.position is not None)
        latest = [binding for binding in events if binding.position == latest_position]
        if len(latest) == 1:
            return _BindingSelection("bound", latest[0])
        return _BindingSelection("ambiguous")
    if len(baselines) == 1:
        return _BindingSelection("bound", baselines[0])
    if baselines:
        return _BindingSelection("ambiguous")
    return _BindingSelection("unbound")


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


def _compiler_child_table(
    compiler_scope: symtable.SymbolTable,
    node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
) -> symtable.SymbolTable | None:
    expected_type = "class" if isinstance(node, ast.ClassDef) else "function"
    pending = list(compiler_scope.get_children())
    while pending:
        child = pending.pop(0)
        child_type = str(child.get_type())
        if child_type == expected_type and child.get_name() == node.name and child.get_lineno() == node.lineno:
            return child
        if child_type == "type parameters" and child.get_name() == node.name and child.get_lineno() == node.lineno:
            pending[0:0] = list(child.get_children())
    return None


def _instance_receiver_binding(scope: _Scope) -> tuple[str, str]:
    node = scope.node
    class_scope = scope.parent
    if (
        scope.kind != "function"
        or not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef)
        or node.decorator_list
        or class_scope is None
        or class_scope.kind != "class"
        or class_scope.symbol is None
    ):
        return "", ""
    class_binding = _binding_selection(class_scope, scope.name)
    if (
        class_binding.status != "bound"
        or class_binding.binding is None
        or class_binding.binding.kind != "symbol"
        or not class_binding.binding.target_certain
        or class_binding.binding.symbol is None
        or scope.symbol is None
        or class_binding.binding.symbol.provider_symbol_id != scope.symbol.provider_symbol_id
    ):
        return "", ""
    positional = [*node.args.posonlyargs, *node.args.args]
    if not positional:
        return "", ""
    return positional[0].arg, class_scope.symbol.provider_symbol_id


def _populate_scope(
    path: str,
    scope: _Scope,
    body: list[ast.stmt],
    scopes: list[_Scope],
    symbols: list[PreciseSymbol],
    compiler_scope: symtable.SymbolTable,
) -> None:
    collector = _ScopeBindingCollector()
    for statement in body:
        collector.visit(statement)
    compiler_symbols = {symbol.get_name(): symbol for symbol in compiler_scope.get_symbols()}
    scope.global_names = {
        name
        for name, symbol in compiler_symbols.items()
        if symbol.is_declared_global()
    }
    scope.nonlocal_names = {
        name
        for name, symbol in compiler_symbols.items()
        if symbol.is_nonlocal()
    }

    receiver_name, receiver_class_symbol_id = _instance_receiver_binding(scope)
    for name, symbol in sorted(compiler_symbols.items()):
        if symbol.is_parameter():
            _add_binding(
                scope,
                name,
                _Binding(
                    "parameter",
                    receiver_class_symbol_id=receiver_class_symbol_id if name == receiver_name else "",
                ),
            )
    for name, execution_certain, position in collector.local_bindings:
        owner = _binding_owner(scope, name)
        _add_binding(
            owner,
            name,
            _Binding(
                "local",
                execution_certain=_binding_execution_certain(scope, owner, execution_certain),
                position=position,
                timeline_ordered=_scope_executes_in_owner(scope, owner),
            ),
        )
    for name, alias_name, execution_certain, position, source_position in collector.aliases:
        owner = _binding_owner(scope, name)
        binding = (
            _Binding(
                "alias",
                alias_name=alias_name,
                execution_certain=_binding_execution_certain(scope, owner, execution_certain),
                position=position,
                source_position=source_position,
                timeline_ordered=_scope_executes_in_owner(scope, owner),
            )
            if owner.kind == "module"
            else _Binding(
                "local",
                execution_certain=_binding_execution_certain(scope, owner, execution_certain),
                position=position,
                timeline_ordered=_scope_executes_in_owner(scope, owner),
            )
        )
        _add_binding(owner, name, binding)
    for name, raw_import, imported_name, import_module, import_level, execution_certain, position in collector.imports:
        owner = _binding_owner(scope, name)
        binding = (
            _Binding(
                "import",
                raw_import=raw_import,
                import_form="from",
                import_module=import_module,
                imported_name=imported_name,
                import_level=import_level,
                execution_certain=_binding_execution_certain(scope, owner, execution_certain),
                position=position,
                timeline_ordered=_scope_executes_in_owner(scope, owner),
            )
            if owner.kind == "module"
            else _Binding(
                "local",
                execution_certain=_binding_execution_certain(scope, owner, execution_certain),
                position=position,
                timeline_ordered=_scope_executes_in_owner(scope, owner),
            )
        )
        _add_binding(
            owner,
            name,
            binding,
        )
    for name, raw_import, execution_certain, identity_certain, position in collector.module_imports:
        owner = _binding_owner(scope, name)
        binding = (
            _Binding(
                "module_import",
                raw_import=raw_import,
                import_form="module",
                import_module=raw_import,
                execution_certain=_binding_execution_certain(scope, owner, execution_certain),
                identity_certain=identity_certain,
                position=position,
                timeline_ordered=_scope_executes_in_owner(scope, owner),
            )
            if owner.kind == "module"
            else _Binding(
                "local",
                execution_certain=_binding_execution_certain(scope, owner, execution_certain),
                position=position,
                timeline_ordered=_scope_executes_in_owner(scope, owner),
            )
        )
        _add_binding(owner, name, binding)
    for name, execution_certain, position in collector.tombstones:
        owner = _binding_owner(scope, name)
        _add_binding(
            owner,
            name,
            _Binding(
                "tombstone",
                execution_certain=_binding_execution_certain(scope, owner, execution_certain),
                position=position,
                timeline_ordered=_scope_executes_in_owner(scope, owner),
            ),
        )

    definition_names = {node.name for node, _unconditional in collector.definitions}
    for name, symbol in sorted(compiler_symbols.items()):
        if symbol.is_local() and name not in scope.bindings and name not in definition_names:
            _add_binding(scope, name, _Binding("local"))

    pending_children: list[
        tuple[
            ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef,
            _Scope,
            symtable.SymbolTable,
        ]
    ] = []
    for node, execution_certain in collector.definitions:
        child_compiler_scope = _compiler_child_table(compiler_scope, node)
        if child_compiler_scope is None:
            raise SyntaxError(f"compiler scope missing for {node.name} at {path}:{node.lineno}")
        symbol = _symbol_for(path, scope, node)
        symbols.append(symbol)
        owner = _binding_owner(scope, node.name)
        _add_binding(
            owner,
            node.name,
            _Binding(
                "symbol",
                symbol=symbol,
                execution_certain=_binding_execution_certain(scope, owner, execution_certain),
                identity_certain=not node.decorator_list,
                position=_node_end_position(node),
                timeline_ordered=_scope_executes_in_owner(scope, owner),
            ),
        )
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
        pending_children.append((node, child, child_compiler_scope))

    # Child scopes may use nonlocal names declared by later siblings, so the
    # enclosing scope's complete binding set must exist before recursion.
    for node, child, child_compiler_scope in pending_children:
        _populate_scope(path, child, node.body, scopes, symbols, child_compiler_scope)


def _analyze_module(path: str, text: str) -> _ModuleAnalysis:
    tree = ast.parse(text)
    compiler_scope = symtable.symtable(text, path, "exec")
    root = _Scope("module", "", "", None, tree)
    scopes = [root]
    symbols: list[PreciseSymbol] = []
    _populate_scope(path, root, tree.body, scopes, symbols, compiler_scope)
    return _ModuleAnalysis(path, tree, root, tuple(scopes), tuple(symbols))


class _CurrentScopeCallVisitor(ast.NodeVisitor):
    def __init__(self, *, blocked_names: set[str] | None = None, class_scope: bool = False) -> None:
        self.calls: list[_CallSite] = []
        self._blocked_names: list[set[str]] = [set(blocked_names)] if blocked_names else []
        self._class_scope = class_scope
        self._comprehension_depth = 0

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._visit_function_definition_expressions(node)

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._visit_function_definition_expressions(node)

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        for decorator in node.decorator_list:
            self._visit_decorator(decorator)
        for base in node.bases:
            self.visit(base)
        for keyword in node.keywords:
            self.visit(keyword.value)

    def visit_Lambda(self, node: ast.Lambda) -> None:
        self._visit_argument_defaults(node.args)

    def visit_AnnAssign(self, node: ast.AnnAssign) -> None:
        # Local annotations are not evaluated when the function runs.
        self.visit(node.target)
        if node.value is not None:
            self.visit(node.value)

    def visit_Call(self, node: ast.Call) -> None:
        self._record_call(node.func, node)
        self.generic_visit(node)

    def visit_ListComp(self, node: ast.ListComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_SetComp(self, node: ast.SetComp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def visit_DictComp(self, node: ast.DictComp) -> None:
        self._visit_comprehension(node.generators, [node.key, node.value])

    def visit_GeneratorExp(self, node: ast.GeneratorExp) -> None:
        self._visit_comprehension(node.generators, [node.elt])

    def _visit_function_definition_expressions(self, node: ast.FunctionDef | ast.AsyncFunctionDef) -> None:
        for decorator in node.decorator_list:
            self._visit_decorator(decorator)
        self._visit_argument_defaults(node.args)

    def _visit_decorator(self, decorator: ast.expr) -> None:
        self.visit(decorator)
        if isinstance(decorator, ast.Name | ast.Attribute):
            self._record_call(decorator, decorator)

    def _record_call(self, target: ast.expr, anchor: ast.AST) -> None:
        blocked = frozenset(name for names in self._blocked_names for name in names)
        self.calls.append(
            _CallSite(
                target=target,
                anchor=anchor,
                blocked_names=blocked,
                skip_current_scope=self._class_scope and self._comprehension_depth > 0,
            )
        )

    def _visit_argument_defaults(self, arguments: ast.arguments) -> None:
        for default in arguments.defaults:
            self.visit(default)
        for default in arguments.kw_defaults:
            if default is not None:
                self.visit(default)

    def _visit_comprehension(self, generators: list[ast.comprehension], values: list[ast.AST]) -> None:
        if not generators:
            for value in values:
                self.visit(value)
            return

        # Python evaluates the first iterable in the enclosing scope, then binds
        # comprehension targets in the child scope for filters and result values.
        self.visit(generators[0].iter)
        shadowed: set[str] = set()
        self._blocked_names.append(shadowed)
        self._comprehension_depth += 1
        try:
            for index, generator in enumerate(generators):
                if index:
                    self.visit(generator.iter)
                self.visit(generator.target)
                shadowed.update(_target_names(generator.target))
                for condition in generator.ifs:
                    self.visit(condition)
            for value in values:
                self.visit(value)
        finally:
            self._comprehension_depth -= 1
            self._blocked_names.pop()


def _binding_import_key(
    importer_path: str,
    binding: _Binding,
) -> tuple[str, str, str, str, int, str, str]:
    return (
        importer_path,
        "python",
        binding.import_form,
        binding.import_module,
        binding.import_level,
        binding.imported_name,
        binding.raw_import,
    )


def _import_targets(
    analyses: list[_ModuleAnalysis],
    symbols: list[PreciseSymbol],
    import_resolutions: list[ImportResolution],
    exported_callables: dict[tuple[str, str], str],
) -> _ImportTargets:
    analyzed_paths = {analysis.path for analysis in analyses}
    resolutions = {
        resolution.occurrence_key: resolution
        for resolution in import_resolutions
        if resolution.language == "python"
        and resolution.provider == "python_import_resolver"
        and resolution.importer_path in analyzed_paths
    }
    module_callables: dict[tuple[str, str], list[PreciseSymbol]] = {}
    symbols_by_id = {symbol.provider_symbol_id: symbol for symbol in symbols}
    for key, provider_symbol_id in exported_callables.items():
        symbol = symbols_by_id.get(provider_symbol_id)
        if symbol is not None:
            module_callables.setdefault(key, []).append(symbol)

    direct: dict[tuple[str, str, str], PreciseSymbol] = {}
    module_paths: dict[tuple[str, str], str] = {}
    for analysis in analyses:
        for scope in analysis.scopes:
            for bindings in scope.bindings.values():
                for binding in bindings:
                    if not binding.target_certain:
                        continue
                    resolution = resolutions.get(_binding_import_key(analysis.path, binding))
                    if resolution is None:
                        continue
                    if binding.kind == "import":
                        if resolution.match_kind != "attribute_exact":
                            continue
                        candidates = module_callables.get((resolution.target_path, binding.imported_name), [])
                        if len(candidates) == 1:
                            direct[(analysis.path, binding.raw_import, binding.imported_name)] = candidates[0]
                    elif binding.kind == "module_import":
                        module_paths[(analysis.path, binding.raw_import)] = resolution.target_path
    return _ImportTargets(
        direct=direct,
        module_paths=module_paths,
        module_callables={key: tuple(value) for key, value in module_callables.items()},
    )


def _export_binding_target(
    analysis: _ModuleAnalysis,
    name: str,
    *,
    resolutions: dict[tuple[str, str, str, str, int, str, str], ImportResolution],
    exported_callables: dict[tuple[str, str], str],
    seen: set[tuple[str, tuple[int, int] | None]] | None = None,
    available_before: tuple[int, int] | None = None,
) -> str:
    seen = seen or set()
    marker = (name, available_before)
    if marker in seen:
        return ""
    seen.add(marker)
    selection = _binding_selection(analysis.root_scope, name, before=available_before)
    if selection.status != "bound" or selection.binding is None:
        return ""
    binding = selection.binding
    if not binding.target_certain:
        return ""
    if binding.kind == "symbol" and binding.symbol is not None:
        return binding.symbol.provider_symbol_id if binding.symbol.kind in {"class", "function"} else ""
    if binding.kind == "alias" and binding.alias_name:
        return _export_binding_target(
            analysis,
            binding.alias_name,
            resolutions=resolutions,
            exported_callables=exported_callables,
            seen=seen,
            available_before=binding.source_position or binding.position,
        )
    if binding.kind == "import":
        resolution = resolutions.get(_binding_import_key(analysis.path, binding))
        if resolution is None or resolution.match_kind != "attribute_exact":
            return ""
        return exported_callables.get((resolution.target_path, binding.imported_name), "")
    return ""


def _exported_callables(
    analyses: list[_ModuleAnalysis],
    symbols: list[PreciseSymbol],
    import_resolutions: list[ImportResolution],
    known_exports: tuple[PythonCallableExport, ...],
    wildcard_paths: set[str],
) -> dict[tuple[str, str], str]:
    analyzed_paths = {analysis.path for analysis in analyses}
    symbol_ids = {symbol.provider_symbol_id for symbol in symbols}
    exported = {
        (item.path, item.name): item.provider_symbol_id
        for item in known_exports
        if item.path not in analyzed_paths
        and item.path not in wildcard_paths
        and item.provider_symbol_id in symbol_ids
    }
    resolutions = {
        resolution.occurrence_key: resolution
        for resolution in import_resolutions
        if resolution.language == "python" and resolution.provider == "python_import_resolver"
    }
    pending = [
        (analysis, name)
        for analysis in analyses
        if analysis.path not in wildcard_paths
        for name in sorted(analysis.root_scope.bindings)
    ]
    while pending:
        next_pending: list[tuple[_ModuleAnalysis, str]] = []
        changed = False
        for analysis, name in pending:
            target = _export_binding_target(
                analysis,
                name,
                resolutions=resolutions,
                exported_callables=exported,
            )
            if not target:
                next_pending.append((analysis, name))
                continue
            key = (analysis.path, name)
            if exported.get(key) != target:
                exported[key] = target
                changed = True
        if not changed:
            break
        pending = next_pending
    return exported


def _resolve_scope_binding(
    analysis: _ModuleAnalysis,
    scope: _Scope,
    name: str,
    import_targets: _ImportTargets,
    seen: set[tuple[int, str]],
    *,
    call_scope: _Scope,
    call_position: tuple[int, int],
    available_before: tuple[int, int] | None = None,
) -> PreciseSymbol | None:
    boundary = available_before
    if scope is call_scope:
        boundary = _earliest_position(boundary, call_position)
    selection = _binding_selection(scope, name, before=boundary)
    if selection.status != "bound" or selection.binding is None:
        return None
    binding = selection.binding
    if not binding.target_certain:
        return None
    if binding.kind == "symbol":
        symbol = binding.symbol
        return symbol if symbol is not None and symbol.kind in {"class", "function", "method"} else None
    if binding.kind == "import":
        return import_targets.direct.get((analysis.path, binding.raw_import, binding.imported_name))
    if binding.kind == "alias" and binding.alias_name:
        return _resolve_name(
            analysis,
            scope,
            binding.alias_name,
            import_targets,
            seen,
            call_scope=call_scope,
            call_position=call_position,
            available_before=binding.source_position or binding.position,
        )
    return None


def _resolve_name(
    analysis: _ModuleAnalysis,
    scope: _Scope,
    name: str,
    import_targets: _ImportTargets,
    seen: set[tuple[int, str]] | None = None,
    *,
    call_scope: _Scope,
    call_position: tuple[int, int],
    available_before: tuple[int, int] | None = None,
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
            call_position=call_position,
            available_before=available_before,
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
                    call_position=call_position,
                    available_before=available_before,
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
            call_position=call_position,
            available_before=available_before,
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
                call_position=call_position,
                available_before=available_before,
            )
        if current.kind == "module":
            return _resolve_scope_binding(
                analysis,
                current,
                name,
                import_targets,
                seen,
                call_scope=call_scope,
                call_position=call_position,
                available_before=available_before,
            )
        current = current.parent
    return None


def _lexical_binding(
    scope: _Scope,
    name: str,
    *,
    before: tuple[int, int] | None = None,
) -> tuple[_Scope, _Binding] | None:
    if scope.kind != "module" and name in scope.global_names:
        scope = _module_scope(scope)
    elif name in scope.nonlocal_names:
        current = scope.parent
        while current is not None:
            selection = _binding_selection(current, name, before=before)
            if current.kind == "function" and selection.status == "bound" and selection.binding is not None:
                return current, selection.binding
            if current.kind == "function" and name in current.bindings:
                return None
            current = current.parent
        return None

    selection = _binding_selection(scope, name, before=before)
    if selection.status == "bound" and selection.binding is not None:
        return scope, selection.binding
    if name in scope.bindings:
        return None
    current = scope.parent
    while current is not None:
        selection = _binding_selection(current, name, before=before)
        if current.kind in {"function", "module"} and selection.status == "bound" and selection.binding is not None:
            return current, selection.binding
        if current.kind in {"function", "module"} and name in current.bindings:
            return None
        current = current.parent
    return None


def _module_import_binding(
    scope: _Scope,
    name: str,
    seen: set[tuple[int, str]] | None = None,
    *,
    available_before: tuple[int, int] | None = None,
) -> _Binding | None:
    seen = seen or set()
    marker = (id(scope), name)
    if marker in seen:
        return None
    seen.add(marker)
    resolved = _lexical_binding(scope, name, before=available_before)
    if resolved is None:
        return None
    owner, binding = resolved
    if not binding.target_certain:
        return None
    if binding.kind == "module_import":
        return binding
    if binding.kind == "alias" and binding.alias_name:
        return _module_import_binding(
            owner,
            binding.alias_name,
            seen,
            available_before=binding.source_position or binding.position,
        )
    return None


def _module_attribute_target(
    analysis: _ModuleAnalysis,
    scope: _Scope,
    node: ast.Attribute,
    import_targets: _ImportTargets,
    *,
    available_before: tuple[int, int] | None = None,
) -> PreciseSymbol | None:
    if not isinstance(node.value, ast.Name):
        return None
    binding = _module_import_binding(scope, node.value.id, available_before=available_before)
    if binding is None:
        return None
    target_path = import_targets.module_paths.get((analysis.path, binding.raw_import))
    if not target_path:
        return None
    candidates = import_targets.module_callables.get((target_path, node.attr), ())
    return candidates[0] if len(candidates) == 1 else None


def _receiver_method_target(
    scope: _Scope,
    node: ast.Attribute,
    *,
    call_position: tuple[int, int],
) -> PreciseSymbol | None:
    if not isinstance(node.value, ast.Name):
        return None
    resolved = _lexical_binding(scope, node.value.id, before=call_position)
    if resolved is None:
        return None
    owner, binding = resolved
    class_scope = owner.parent
    if (
        binding.kind != "parameter"
        or not binding.target_certain
        or not binding.receiver_class_symbol_id
        or owner.kind != "function"
        or class_scope is None
        or class_scope.kind != "class"
        or class_scope.symbol is None
        or class_scope.symbol.provider_symbol_id != binding.receiver_class_symbol_id
    ):
        return None
    selection = _binding_selection(class_scope, node.attr)
    if selection.status != "bound" or selection.binding is None:
        return None
    method_binding = selection.binding
    if method_binding.kind != "symbol" or not method_binding.target_certain:
        return None
    symbol = method_binding.symbol
    return symbol if symbol is not None and symbol.kind == "method" else None


def _call_lookup_scope(
    scope: _Scope,
    name: str,
    *,
    skip_current_scope: bool,
    call_position: tuple[int, int],
) -> tuple[_Scope | None, tuple[int, int] | None]:
    if scope.kind != "class":
        return scope, None
    boundary = _node_position(scope.node)
    if name in scope.global_names:
        return _module_scope(scope), call_position
    if name in scope.nonlocal_names:
        current = scope.parent
        while current is not None:
            if current.kind == "function" and name in current.bindings:
                return current, call_position
            current = current.parent
        return None, None
    if not skip_current_scope:
        selection = _binding_selection(scope, name, before=call_position)
        if selection.status == "ambiguous":
            return scope, None
        if selection.status == "bound" and selection.binding is not None:
            if selection.binding.kind != "tombstone" or not selection.binding.target_certain:
                return scope, None
    current = scope.parent
    while current is not None and current.kind == "class":
        current = current.parent
    return current, boundary


def _calls_for_analysis(
    analysis: _ModuleAnalysis,
    import_targets: _ImportTargets,
) -> list[PreciseCall]:
    calls: list[PreciseCall] = []
    for scope in analysis.scopes:
        if scope.kind not in {"class", "function"} or scope.symbol is None:
            continue
        node = scope.node
        if not isinstance(node, ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        visitor = _CurrentScopeCallVisitor(class_scope=scope.kind == "class")
        for statement in node.body:
            visitor.visit(statement)
        for call_site in visitor.calls:
            callee: PreciseSymbol | None = None
            target_node = call_site.target
            if isinstance(target_node, ast.Name) and target_node.id not in call_site.blocked_names:
                lookup_scope, available_before = _call_lookup_scope(
                    scope,
                    target_node.id,
                    skip_current_scope=call_site.skip_current_scope,
                    call_position=_node_position(call_site.anchor),
                )
                if lookup_scope is not None:
                    callee = _resolve_name(
                        analysis,
                        lookup_scope,
                        target_node.id,
                        import_targets,
                        call_scope=scope,
                        call_position=_node_position(call_site.anchor),
                        available_before=available_before,
                    )
            elif (
                isinstance(target_node, ast.Attribute)
                and isinstance(target_node.value, ast.Name)
                and target_node.value.id not in call_site.blocked_names
            ):
                lookup_scope, available_before = _call_lookup_scope(
                    scope,
                    target_node.value.id,
                    skip_current_scope=call_site.skip_current_scope,
                    call_position=_node_position(call_site.anchor),
                )
                if lookup_scope is not None:
                    callee = _receiver_method_target(
                        lookup_scope,
                        target_node,
                        call_position=_node_position(call_site.anchor),
                    )
                    if callee is None:
                        callee = _module_attribute_target(
                            analysis,
                            lookup_scope,
                            target_node,
                            import_targets,
                            available_before=available_before,
                        )
            if callee is None:
                continue
            calls.append(
                PreciseCall(
                    path=analysis.path,
                    provider="python_ast",
                    caller_provider_symbol_id=scope.symbol.provider_symbol_id,
                    callee_provider_symbol_id=callee.provider_symbol_id,
                    language="python",
                    scope="same_file" if callee.path == analysis.path else "cross_file_import",
                    anchor=_anchor_for(analysis.path, call_site.anchor),
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
    analysis_paths: set[str] | None = None,
    known_symbols: tuple[PreciseSymbol, ...] = (),
    known_exported_callables: tuple[PythonCallableExport, ...] = (),
) -> tuple[list[PreciseSymbol], list[PreciseCall], dict[str, object]]:
    del root
    selected_paths = {
        entry.path
        for entry in entries
        if entry.language == "python" and (analysis_paths is None or entry.path in analysis_paths)
    }
    analyses: list[_ModuleAnalysis] = []
    failed_paths: list[str] = []
    for entry in entries:
        if entry.path not in selected_paths:
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
    current_paths = {entry.path for entry in entries if entry.language == "python"}
    retained_known_symbols = [
        symbol
        for symbol in known_symbols
        if symbol.path in current_paths and symbol.path not in selected_paths
    ]
    all_symbols = [*retained_known_symbols, *symbols]
    exported_callables = _exported_callables(
        analyses,
        all_symbols,
        import_resolutions or [],
        known_exported_callables,
        {entry.path for entry in entries if entry.module_wildcard_import},
    )
    import_targets = _import_targets(
        analyses,
        all_symbols,
        import_resolutions or [],
        exported_callables,
    )
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
        "exported_callables": [
            PythonCallableExport(path, name, provider_symbol_id).to_dict()
            for (path, name), provider_symbol_id in sorted(exported_callables.items())
        ],
    }
    return symbols, calls, meta

from __future__ import annotations

import ast
from dataclasses import dataclass
from pathlib import Path

from .graph_import_resolver import ImportResolution
from .repositories import RepoTarget


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


def _anchor_for(path: str, node: ast.AST) -> SourceAnchor:
    return SourceAnchor(
        path=path,
        start_line=int(getattr(node, "lineno", 0) or 0),
        start_col=int(getattr(node, "col_offset", 0) or 0),
        end_line=int(getattr(node, "end_lineno", getattr(node, "lineno", 0)) or 0),
        end_col=int(getattr(node, "end_col_offset", getattr(node, "col_offset", 0)) or 0),
    )


class _PythonSymbolVisitor(ast.NodeVisitor):
    def __init__(self, path: str) -> None:
        self.path = path
        self.scope: list[tuple[str, str]] = []
        self.symbols: list[PreciseSymbol] = []

    def visit_ClassDef(self, node: ast.ClassDef) -> None:
        self._record(node, "class")
        self.scope.append(("class", node.name))
        self.generic_visit(node)
        self.scope.pop()

    def visit_FunctionDef(self, node: ast.FunctionDef) -> None:
        self._record(node, "method" if self.scope and self.scope[-1][0] == "class" else "function")
        self.scope.append(("function", node.name))
        self.generic_visit(node)
        self.scope.pop()

    def visit_AsyncFunctionDef(self, node: ast.AsyncFunctionDef) -> None:
        self._record(node, "method" if self.scope and self.scope[-1][0] == "class" else "function")
        self.scope.append(("function", node.name))
        self.generic_visit(node)
        self.scope.pop()

    def _record(self, node: ast.ClassDef | ast.FunctionDef | ast.AsyncFunctionDef, kind: str) -> None:
        names = [name for _kind, name in self.scope]
        qualified_name = ".".join([*names, node.name]) if names else node.name
        anchor = _anchor_for(self.path, node)
        provider_symbol_id = f"python_ast:{self.path}:{qualified_name}:{kind}:{anchor.start_line}:{anchor.start_col}:{anchor.end_line}:{anchor.end_col}"
        self.symbols.append(
            PreciseSymbol(
                path=self.path,
                provider="python_ast",
                provider_symbol_id=provider_symbol_id,
                language="python",
                kind=kind,
                name=node.name,
                qualified_name=qualified_name,
                anchor=anchor,
            )
        )


def _python_symbols(path: str, text: str) -> list[PreciseSymbol]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    visitor = _PythonSymbolVisitor(path)
    visitor.visit(tree)
    return sorted(visitor.symbols, key=lambda item: item.provider_symbol_id)


def _python_calls(path: str, text: str, symbols: list[PreciseSymbol]) -> list[PreciseCall]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []
    module_symbols = {symbol.name: symbol for symbol in symbols if symbol.path == path and symbol.kind == "function" and "." not in symbol.qualified_name}
    class_method_symbols = _class_method_symbols(path, symbols)
    aliases = _module_function_aliases(tree, module_symbols)
    calls: list[PreciseCall] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        caller = module_symbols.get(node.name)
        if caller is None:
            continue
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            call_name = _call_name(child.func)
            if not call_name:
                continue
            callee_name = aliases.get(call_name, call_name)
            callee = module_symbols.get(callee_name)
            if callee is None or callee.provider_symbol_id == caller.provider_symbol_id:
                continue
            calls.append(
                PreciseCall(
                    path=path,
                    provider="python_ast",
                    caller_provider_symbol_id=caller.provider_symbol_id,
                    callee_provider_symbol_id=callee.provider_symbol_id,
                    language="python",
                    scope="same_file",
                    anchor=_anchor_for(path, child),
                )
            )
    for class_node in [node for node in tree.body if isinstance(node, ast.ClassDef)]:
        methods = class_method_symbols.get(class_node.name, {})
        for node in class_node.body:
            if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
                continue
            caller = methods.get(node.name)
            if caller is None:
                continue
            for child in ast.walk(node):
                if not isinstance(child, ast.Call):
                    continue
                callee_name = _self_method_call_name(child.func)
                if not callee_name:
                    continue
                callee = methods.get(callee_name)
                if callee is None or callee.provider_symbol_id == caller.provider_symbol_id:
                    continue
                calls.append(
                    PreciseCall(
                        path=path,
                        provider="python_ast",
                        caller_provider_symbol_id=caller.provider_symbol_id,
                        callee_provider_symbol_id=callee.provider_symbol_id,
                        language="python",
                        scope="same_file",
                        anchor=_anchor_for(path, child),
                    )
                )
    return sorted(calls, key=lambda item: (item.caller_provider_symbol_id, item.callee_provider_symbol_id, item.anchor.start_line, item.anchor.start_col))


def _python_cross_file_imported_calls(
    path: str,
    text: str,
    symbols: list[PreciseSymbol],
    import_resolutions: list[ImportResolution],
) -> list[PreciseCall]:
    try:
        tree = ast.parse(text)
    except SyntaxError:
        return []

    caller_symbols = {symbol.name: symbol for symbol in symbols if symbol.path == path and symbol.kind == "function" and "." not in symbol.qualified_name}
    imported_callees = _imported_function_callees(path, tree, symbols, import_resolutions)
    if not caller_symbols or not imported_callees:
        return []

    calls: list[PreciseCall] = []
    for node in tree.body:
        if not isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef):
            continue
        caller = caller_symbols.get(node.name)
        if caller is None:
            continue
        shadowed_names = _local_bindings(node)
        for child in ast.walk(node):
            if not isinstance(child, ast.Call):
                continue
            call_name = _call_name(child.func)
            if not call_name or call_name in shadowed_names:
                continue
            callee = imported_callees.get(call_name)
            if callee is None or callee.provider_symbol_id == caller.provider_symbol_id:
                continue
            calls.append(
                PreciseCall(
                    path=path,
                    provider="python_ast",
                    caller_provider_symbol_id=caller.provider_symbol_id,
                    callee_provider_symbol_id=callee.provider_symbol_id,
                    language="python",
                    scope="cross_file_import",
                    anchor=_anchor_for(path, child),
                )
            )
    return calls


def _imported_function_callees(
    path: str,
    tree: ast.Module,
    symbols: list[PreciseSymbol],
    import_resolutions: list[ImportResolution],
) -> dict[str, PreciseSymbol]:
    resolutions = {
        (resolution.importer_path, resolution.raw_import): resolution
        for resolution in import_resolutions
        if resolution.language == "python" and resolution.provider == "python_import_resolver"
    }
    module_functions: dict[tuple[str, str], list[PreciseSymbol]] = {}
    for symbol in symbols:
        if symbol.language == "python" and symbol.kind == "function" and "." not in symbol.qualified_name:
            module_functions.setdefault((symbol.path, symbol.name), []).append(symbol)

    result: dict[str, PreciseSymbol] = {}
    for node in tree.body:
        if not isinstance(node, ast.ImportFrom):
            continue
        prefix = "." * node.level
        module = node.module or ""
        for alias in node.names:
            if alias.name == "*":
                continue
            raw_import = f"{prefix}{module}.{alias.name}" if module else f"{prefix}{alias.name}"
            resolution = resolutions.get((path, raw_import))
            if resolution is None:
                continue
            candidates = module_functions.get((resolution.target_path, alias.name), [])
            if len(candidates) != 1:
                continue
            local_name = alias.asname or alias.name
            result[local_name] = candidates[0]
    return result


def _local_bindings(node: ast.FunctionDef | ast.AsyncFunctionDef) -> set[str]:
    names = {arg.arg for arg in [*node.args.posonlyargs, *node.args.args, *node.args.kwonlyargs]}
    if node.args.vararg is not None:
        names.add(node.args.vararg.arg)
    if node.args.kwarg is not None:
        names.add(node.args.kwarg.arg)
    for child in ast.walk(node):
        if isinstance(child, ast.Name) and isinstance(child.ctx, ast.Store):
            names.add(child.id)
    return names


def _class_method_symbols(path: str, symbols: list[PreciseSymbol]) -> dict[str, dict[str, PreciseSymbol]]:
    result: dict[str, dict[str, PreciseSymbol]] = {}
    for symbol in symbols:
        if symbol.path != path or symbol.kind != "method" or "." not in symbol.qualified_name:
            continue
        class_name, method_name = symbol.qualified_name.rsplit(".", 1)
        if "." in class_name:
            continue
        result.setdefault(class_name, {})[method_name] = symbol
    return result


def _module_function_aliases(tree: ast.Module, symbols: dict[str, PreciseSymbol]) -> dict[str, str]:
    aliases: dict[str, str] = {}
    for node in tree.body:
        if not isinstance(node, ast.Assign) or len(node.targets) != 1:
            continue
        target = node.targets[0]
        if not isinstance(target, ast.Name) or not isinstance(node.value, ast.Name):
            continue
        if node.value.id in symbols:
            aliases[target.id] = node.value.id
    return aliases


def _call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    return ""


def _self_method_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Attribute) and isinstance(node.value, ast.Name) and node.value.id == "self":
        return node.attr
    return ""


def build_precise_symbols(root: Path, *, target: RepoTarget, paths: list[str]) -> tuple[list[PreciseSymbol], dict[str, object]]:
    symbols: list[PreciseSymbol] = []
    for rel in sorted(set(paths)):
        if Path(rel).suffix.lower() != ".py":
            continue
        path = target.root_path / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        symbols.extend(_python_symbols(rel, text))
    meta = {
        "provider": "python_ast",
        "languages": ["python"],
        "symbol_count": len(symbols),
    }
    return symbols, meta


def build_precise_calls(
    root: Path,
    *,
    target: RepoTarget,
    paths: list[str],
    symbols: list[PreciseSymbol],
    import_resolutions: list[ImportResolution] | None = None,
) -> tuple[list[PreciseCall], dict[str, object]]:
    calls: list[PreciseCall] = []
    import_resolutions = import_resolutions or []
    for rel in sorted(set(paths)):
        if Path(rel).suffix.lower() != ".py":
            continue
        path = target.root_path / rel
        try:
            text = path.read_text(encoding="utf-8")
        except (OSError, UnicodeDecodeError):
            continue
        calls.extend(_python_calls(rel, text, symbols))
        calls.extend(_python_cross_file_imported_calls(rel, text, symbols, import_resolutions))
    meta = {
        "provider": "python_ast",
        "languages": ["python"],
        "call_count": len(calls),
        "scopes": sorted({call.scope for call in calls}),
    }
    return calls, meta

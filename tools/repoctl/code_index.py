from __future__ import annotations

import ast
import re
import symtable
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Literal

from .language_profiles import index_dart, language_for_path
from .meta import FileClassification, meta_inventory
from .repositories import RepoTarget
from .tasks import Problem


CODE_INDEX_INPUT_VERSION = 7


@dataclass(frozen=True, order=True)
class PythonImportOccurrence:
    raw_import: str
    form: Literal["module", "from"]
    module: str
    imported_name: str = ""
    level: int = 0

    def to_dict(self) -> dict[str, object]:
        return {
            "raw_import": self.raw_import,
            "form": self.form,
            "module": self.module,
            "imported_name": self.imported_name,
            "level": self.level,
        }


@dataclass(frozen=True)
class CodeIndexEntry:
    path: str
    workspace_path: str
    language: str
    classification: str
    symbols: list[str]
    imports: list[str]
    calls: list[str]
    deps: list[str]
    observed_effects: list[str]
    parse_status: str = "ok"
    parse_error: str = ""
    import_occurrences: tuple[PythonImportOccurrence, ...] = ()
    module_bindings: tuple[str, ...] = ()
    module_certain_bindings: tuple[str, ...] = ()
    module_wildcard_import: bool = False

    def __post_init__(self) -> None:
        for field_name in ("symbols", "imports", "calls", "deps", "observed_effects"):
            values = getattr(self, field_name)
            object.__setattr__(self, field_name, sorted({value for value in values if value}))
        object.__setattr__(self, "import_occurrences", tuple(sorted(set(self.import_occurrences))))
        object.__setattr__(self, "module_bindings", tuple(sorted({value for value in self.module_bindings if value})))
        object.__setattr__(
            self,
            "module_certain_bindings",
            tuple(sorted({value for value in self.module_certain_bindings if value})),
        )

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "workspace_path": self.workspace_path,
            "language": self.language,
            "classification": self.classification,
            "symbols": list(self.symbols),
            "imports": list(self.imports),
            "calls": list(self.calls),
            "deps": list(self.deps),
            "observed_effects": list(self.observed_effects),
            "parse_status": self.parse_status,
            "import_occurrences": [occurrence.to_dict() for occurrence in self.import_occurrences],
            "module_bindings": list(self.module_bindings),
            "module_certain_bindings": list(self.module_certain_bindings),
            "module_wildcard_import": self.module_wildcard_import,
        }
        if self.parse_error:
            data["parse_error"] = self.parse_error
        return data


def semantic_provider_entries(entries: list[CodeIndexEntry]) -> list[CodeIndexEntry]:
    return [
        entry
        for entry in entries
        if entry.classification != "excluded" and entry.parse_status == "ok"
    ]


JS_IMPORT_RE = re.compile(r"(?:import\s+(?:[^'\"]+\s+from\s+)?|require\()\s*['\"]([^'\"]+)['\"]")

EFFECT_IMPORT_PREFIXES = {
    "crypto": ("hashlib", "crypto", "bcrypt", "jwt"),
    "db": ("sqlite", "sqlite3", "psycopg", "mysql", "sqlalchemy", "prisma"),
    "fs": ("os", "pathlib", "shutil", "fs"),
    "net": ("requests", "urllib", "http", "axios"),
    "time": ("time", "datetime"),
}
EFFECT_CALLS = {
    "crypto": {"hashlib.sha256", "hashlib.md5", "crypto.createHash"},
    "db": {"execute", "executemany", "query", "transaction"},
    "fs": {"open", "read_text", "write_text", "readFile", "writeFile"},
    "net": {"fetch", "axios", "requests.get", "requests.post", "urllib.request.urlopen"},
    "time": {"sleep", "datetime.now", "Date.now", "setTimeout", "setInterval"},
}


def _dedupe_sorted(values: list[str]) -> list[str]:
    return sorted({value for value in values if value})


def _python_call_name(node: ast.AST) -> str:
    if isinstance(node, ast.Name):
        return node.id
    if isinstance(node, ast.Attribute):
        base = _python_call_name(node.value)
        return f"{base}.{node.attr}" if base else node.attr
    return ""


def _python_import_occurrences(tree: ast.Module) -> tuple[PythonImportOccurrence, ...]:
    occurrences: list[PythonImportOccurrence] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.Import):
            occurrences.extend(
                PythonImportOccurrence(
                    raw_import=alias.name,
                    form="module",
                    module=alias.name,
                )
                for alias in node.names
            )
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            module = node.module or ""
            for alias in node.names:
                raw_import = f"{prefix}{module}.{alias.name}" if module else f"{prefix}{alias.name}"
                occurrences.append(
                    PythonImportOccurrence(
                        raw_import=raw_import,
                        form="from",
                        module=module,
                        imported_name=alias.name,
                        level=node.level,
                    )
                )
    return tuple(sorted(set(occurrences)))


def _module_binding_facts(text: str, tree: ast.Module) -> tuple[tuple[str, ...], tuple[str, ...]]:
    compiler_scope = symtable.symtable(text, "<code-index>", "exec")
    possible = {
        symbol.get_name()
        for symbol in compiler_scope.get_symbols()
        if symbol.is_assigned() or symbol.is_imported() or symbol.is_namespace() or symbol.is_declared_global()
    }
    present: set[str] = set()
    uncertain: set[str] = set()

    def target_names(node: ast.AST) -> set[str]:
        if isinstance(node, ast.Name):
            return {node.id}
        if isinstance(node, ast.Starred):
            return target_names(node.value)
        if isinstance(node, ast.Tuple | ast.List):
            return {name for item in node.elts for name in target_names(item)}
        return set()

    def statement_effects(statement: ast.stmt, *, conditional: bool) -> None:
        bound: set[str] = set()
        deleted: set[str] = set()
        if isinstance(statement, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            bound.add(statement.name)
        elif isinstance(statement, ast.Import):
            bound.update(alias.asname or alias.name.split(".", 1)[0] for alias in statement.names)
        elif isinstance(statement, ast.ImportFrom):
            bound.update(alias.asname or alias.name for alias in statement.names if alias.name != "*")
        elif isinstance(statement, ast.Assign):
            bound.update(name for target in statement.targets for name in target_names(target))
        elif isinstance(statement, ast.AnnAssign) and statement.value is not None:
            bound.update(target_names(statement.target))
        elif isinstance(statement, ast.AugAssign):
            bound.update(target_names(statement.target))
        elif isinstance(statement, ast.Delete):
            deleted.update(name for target in statement.targets for name in target_names(target))

        if conditional:
            uncertain.update(bound | deleted)
        else:
            present.difference_update(deleted)
            present.update(bound)

        if isinstance(statement, ast.If | ast.For | ast.AsyncFor | ast.While | ast.Try | ast.TryStar | ast.Match | ast.With | ast.AsyncWith):
            def visit_nested(node: ast.AST) -> None:
                for child in ast.iter_child_nodes(node):
                    if isinstance(child, ast.stmt):
                        statement_effects(child, conditional=True)
                    elif not isinstance(child, ast.expr):
                        visit_nested(child)

            visit_nested(statement)

    for statement in tree.body:
        statement_effects(statement, conditional=False)
    return tuple(sorted(possible)), tuple(sorted((present - uncertain) & possible))


def _index_python(
    text: str,
) -> tuple[
    list[str],
    list[str],
    list[str],
    str,
    str,
    tuple[PythonImportOccurrence, ...],
    tuple[str, ...],
    tuple[str, ...],
    bool,
]:
    try:
        tree = ast.parse(text)
        module_bindings, module_certain_bindings = _module_binding_facts(text, tree)
    except SyntaxError as exc:
        return [], [], [], "parse_error", exc.msg, (), (), (), False

    symbols: list[str] = []
    imports: list[str] = []
    calls: list[str] = []
    for node in ast.walk(tree):
        if isinstance(node, ast.FunctionDef | ast.AsyncFunctionDef | ast.ClassDef):
            symbols.append(node.name)
        elif isinstance(node, ast.Import):
            imports.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom):
            prefix = "." * node.level
            module = node.module or ""
            base = f"{prefix}{module}"
            imports.extend(f"{base}.{alias.name}" if module else f"{prefix}{alias.name}" for alias in node.names)
        elif isinstance(node, ast.Call):
            calls.append(_python_call_name(node.func))
    return (
        _dedupe_sorted(symbols),
        _dedupe_sorted(imports),
        _dedupe_sorted(calls),
        "ok",
        "",
        _python_import_occurrences(tree),
        module_bindings,
        module_certain_bindings,
        any(
            isinstance(node, ast.ImportFrom) and any(alias.name == "*" for alias in node.names)
            for node in ast.walk(tree)
        ),
    )


def _index_js_imports(text: str) -> tuple[list[str], list[str], list[str], str, str]:
    imports = [match.group(1) for match in JS_IMPORT_RE.finditer(text)]
    return [], _dedupe_sorted(imports), [], "ok", ""


def _observed_effects_for(imports: list[str], calls: list[str]) -> list[str]:
    effects: list[str] = []
    for effect, prefixes in EFFECT_IMPORT_PREFIXES.items():
        if any(import_name == prefix or import_name.startswith(f"{prefix}.") for import_name in imports for prefix in prefixes):
            effects.append(effect)
    for effect, names in EFFECT_CALLS.items():
        if any(call in names for call in calls):
            effects.append(effect)
    return _dedupe_sorted(effects)


def _index_file(repo: Path, file: FileClassification) -> CodeIndexEntry:
    language = language_for_path(file.path)
    if file.classification == "excluded":
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], [], [], "skipped", "excluded by policy")
    if language == "csharp":
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], [], [], "ok", "")
    if language not in {"python", "javascript", "typescript", "dart"}:
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], [], [], "skipped", "unsupported language")

    path = repo / file.path
    try:
        text = path.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], [], [], "skipped", "non-utf8 file")
    except OSError as exc:
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], [], [], "parse_error", str(exc))

    if language == "python":
        (
            symbols,
            imports,
            calls,
            status,
            error,
            import_occurrences,
            module_bindings,
            module_certain_bindings,
            module_wildcard_import,
        ) = _index_python(text)
    elif language in {"javascript", "typescript"}:
        symbols, imports, calls, status, error = _index_js_imports(text)
        import_occurrences = ()
        module_bindings = ()
        module_certain_bindings = ()
        module_wildcard_import = False
    else:
        symbols, imports, calls, status, error = index_dart(text)
        import_occurrences = ()
        module_bindings = ()
        module_certain_bindings = ()
        module_wildcard_import = False

    deps = _dedupe_sorted([import_name.split(".", 1)[0] for import_name in imports])
    effects = _observed_effects_for(imports, calls)
    return CodeIndexEntry(
        file.path,
        file.workspace_path,
        language,
        file.classification,
        symbols,
        imports,
        calls,
        deps,
        effects,
        status,
        error,
        import_occurrences,
        module_bindings,
        module_certain_bindings,
        module_wildcard_import,
    )


def _reused_index_entry(file: FileClassification, previous: CodeIndexEntry) -> CodeIndexEntry:
    return CodeIndexEntry(
        path=file.path,
        workspace_path=file.workspace_path,
        language=language_for_path(file.path),
        classification=file.classification,
        symbols=list(previous.symbols),
        imports=list(previous.imports),
        calls=list(previous.calls),
        deps=list(previous.deps),
        observed_effects=list(previous.observed_effects),
        parse_status=previous.parse_status,
        parse_error=previous.parse_error,
        import_occurrences=previous.import_occurrences,
        module_bindings=previous.module_bindings,
        module_certain_bindings=previous.module_certain_bindings,
        module_wildcard_import=previous.module_wildcard_import,
    )


def build_code_index_from_inventory(
    root: Path,
    *,
    files: list[FileClassification],
    inventory_problems: list[Problem],
    inventory_meta: dict[str, Any],
    target: RepoTarget,
    previous_entries: list[CodeIndexEntry] | None = None,
    reindex_paths: set[str] | None = None,
    limit: int = -1,
) -> tuple[list[CodeIndexEntry], list[Problem], dict[str, Any]]:
    if inventory_problems:
        return [], inventory_problems, {**inventory_meta, "authoritative": False}
    previous_by_path = {entry.path: entry for entry in previous_entries or []}
    reindex_paths = reindex_paths or set()
    entries: list[CodeIndexEntry] = []
    reindexed = 0
    reused = 0
    for file in files:
        if file.classification in {"orphan_annotation", "orphan_exclusion"}:
            continue
        previous = previous_by_path.get(file.path)
        excluded_boundary_changed = previous is not None and ((previous.classification == "excluded") != (file.classification == "excluded"))
        can_reuse = (
            previous is not None
            and file.path not in reindex_paths
            and previous.language == language_for_path(file.path)
            and not excluded_boundary_changed
        )
        if can_reuse:
            entries.append(_reused_index_entry(file, previous))
            reused += 1
        else:
            entries.append(_index_file(target.root_path, file))
            reindexed += 1
    entries.sort(key=lambda entry: entry.path)
    total_before_limit = len(entries)
    if limit >= 0:
        entries = entries[:limit]
    returned = len(entries)
    summary = {
        "total": total_before_limit,
        "returned": returned,
        "truncated": returned < total_before_limit,
        "dropped_count": max(0, total_before_limit - returned),
        "ok": sum(1 for entry in entries if entry.parse_status == "ok"),
        "skipped": sum(1 for entry in entries if entry.parse_status == "skipped"),
        "parse_error": sum(1 for entry in entries if entry.parse_status == "parse_error"),
        "reindexed": reindexed,
        "reused": reused,
        "languages": {
            language: sum(1 for entry in entries if entry.language == language)
            for language in sorted({entry.language for entry in entries})
        },
    }
    return entries, inventory_problems, {**inventory_meta, "summary": summary, "authoritative": False}


def build_code_index(root: Path, *, changed: bool = False, limit: int = 200, target: RepoTarget | None = None) -> tuple[list[CodeIndexEntry], list[Problem], dict[str, Any]]:
    files, problems, meta = meta_inventory(root, changed=changed, target=target)
    if problems:
        return [], problems, {**meta, "authoritative": False}

    repo = target.root_path if target is not None else root / str(meta.get("repository", {}).get("path") or "repos")
    entries = [_index_file(repo, file) for file in files if file.classification not in {"orphan_annotation", "orphan_exclusion"}]
    entries.sort(key=lambda entry: entry.path)
    total_before_limit = len(entries)
    if limit >= 0:
        entries = entries[:limit]
    returned = len(entries)

    summary = {
        "total": total_before_limit,
        "returned": returned,
        "truncated": returned < total_before_limit,
        "dropped_count": max(0, total_before_limit - returned),
        "ok": sum(1 for entry in entries if entry.parse_status == "ok"),
        "skipped": sum(1 for entry in entries if entry.parse_status == "skipped"),
        "parse_error": sum(1 for entry in entries if entry.parse_status == "parse_error"),
        "languages": {
            language: sum(1 for entry in entries if entry.language == language)
            for language in sorted({entry.language for entry in entries})
        },
    }
    return entries, problems, {**meta, "summary": summary, "authoritative": False}

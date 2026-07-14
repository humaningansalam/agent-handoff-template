from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .language_profiles import index_dart, language_for_path
from .meta import FileClassification, meta_inventory
from .repositories import RepoTarget
from .tasks import Problem


CODE_INDEX_INPUT_VERSION = 3


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

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "path": self.path,
            "workspace_path": self.workspace_path,
            "language": self.language,
            "classification": self.classification,
            "symbols": self.symbols,
            "imports": self.imports,
            "calls": self.calls,
            "deps": self.deps,
            "observed_effects": self.observed_effects,
            "parse_status": self.parse_status,
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


def _index_python(text: str) -> tuple[list[str], list[str], list[str], str, str]:
    try:
        tree = ast.parse(text)
    except SyntaxError as exc:
        return [], [], [], "parse_error", exc.msg

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
    return _dedupe_sorted(symbols), _dedupe_sorted(imports), _dedupe_sorted(calls), "ok", ""


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
        symbols, imports, calls, status, error = _index_python(text)
    elif language in {"javascript", "typescript"}:
        symbols, imports, calls, status, error = _index_js_imports(text)
    else:
        symbols, imports, calls, status, error = index_dart(text)

    deps = _dedupe_sorted([import_name.split(".", 1)[0] for import_name in imports])
    effects = _observed_effects_for(imports, calls)
    return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, symbols, imports, calls, deps, effects, status, error)


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

from __future__ import annotations

import ast
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

from .meta import FileClassification, meta_inventory
from .repositories import RepoTarget
from .tasks import Problem


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


LANGUAGE_BY_SUFFIX = {
    ".py": "python",
    ".js": "javascript",
    ".jsx": "javascript",
    ".mjs": "javascript",
    ".cjs": "javascript",
    ".ts": "typescript",
    ".tsx": "typescript",
    ".mts": "typescript",
    ".cts": "typescript",
    ".sh": "shell",
    ".bash": "shell",
    ".zsh": "shell",
    ".md": "markdown",
    ".markdown": "markdown",
    ".json": "json",
    ".toml": "toml",
    ".yaml": "yaml",
    ".yml": "yaml",
}

JS_IMPORT_RE = re.compile(r"(?:import\s+(?:[^'\"]+\s+from\s+)?|require\()\s*['\"]([^'\"]+)['\"]")
JS_SYMBOL_RE = re.compile(r"\b(?:export\s+)?(?:async\s+)?(?:function|class)\s+([A-Za-z_$][\w$]*)|\b(?:export\s+)?(?:const|let|var)\s+([A-Za-z_$][\w$]*)\s*=")
JS_CALL_RE = re.compile(r"\b([A-Za-z_$][\w$]*(?:\.[A-Za-z_$][\w$]*)?)\s*\(")
JS_CALL_KEYWORDS = {"if", "for", "while", "switch", "catch", "function"}

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


def _language_for(path: str) -> str:
    if Path(path).name == "Dockerfile":
        return "dockerfile"
    return LANGUAGE_BY_SUFFIX.get(Path(path).suffix.lower(), "unknown")


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


def _index_js_like(text: str) -> tuple[list[str], list[str], list[str], str, str]:
    symbols = [match.group(1) or match.group(2) for match in JS_SYMBOL_RE.finditer(text)]
    imports = [match.group(1) for match in JS_IMPORT_RE.finditer(text)]
    calls = [match.group(1) for match in JS_CALL_RE.finditer(text)]
    calls = [call for call in calls if call not in JS_CALL_KEYWORDS]
    return _dedupe_sorted(symbols), _dedupe_sorted(imports), _dedupe_sorted(calls), "ok", ""


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
    language = _language_for(file.path)
    if file.classification == "excluded":
        return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, [], [], [], [], [], "skipped", "excluded by policy")

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
        symbols, imports, calls, status, error = _index_js_like(text)
    else:
        symbols, imports, calls, status, error = [], [], [], "skipped", "unsupported language"

    deps = _dedupe_sorted([import_name.split(".", 1)[0] for import_name in imports])
    effects = _observed_effects_for(imports, calls)
    return CodeIndexEntry(file.path, file.workspace_path, language, file.classification, symbols, imports, calls, deps, effects, status, error)


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
    }
    return entries, problems, {**meta, "summary": summary, "authoritative": False}

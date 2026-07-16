from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from posixpath import normpath
from typing import Literal

from .code_index import CodeIndexEntry, PythonImportOccurrence


IMPORT_RESOLVER_LANGUAGES = frozenset({"python", "javascript", "typescript", "dart"})
PythonImportMatch = Literal["", "module_exact", "attribute_exact", "submodule_exact"]
ImportForm = Literal["module", "from", "raw"]


@dataclass(frozen=True)
class _PythonModuleIndex:
    by_name: dict[str, set[str]]
    by_path: dict[str, set[str]]


@dataclass(frozen=True)
class ImportResolution:
    importer_path: str
    language: str
    raw_import: str
    form: ImportForm
    module: str
    imported_name: str
    level: int
    target_path: str
    provider: str
    match_kind: Literal["module_exact", "attribute_exact", "submodule_exact", "path_exact"]

    @property
    def occurrence_key(self) -> tuple[str, str, str, str, int, str, str]:
        return (
            self.importer_path,
            self.language,
            self.form,
            self.module,
            self.level,
            self.imported_name,
            self.raw_import,
        )

    def to_dict(self) -> dict[str, str]:
        return {
            "importer_path": self.importer_path,
            "language": self.language,
            "raw_import": self.raw_import,
            "form": self.form,
            "module": self.module,
            "imported_name": self.imported_name,
            "level": self.level,
            "target_path": self.target_path,
            "provider": self.provider,
            "match_kind": self.match_kind,
        }


def resolve_code_imports(entries: list[CodeIndexEntry], *, repo: Path | None = None) -> tuple[list[ImportResolution], dict[str, object]]:
    file_paths = {entry.path for entry in entries}
    entries_by_path = {entry.path: entry for entry in entries}
    python_import_roots = _python_import_roots(repo)
    python_modules = _python_module_index(file_paths, import_roots=python_import_roots)
    dart_package_name = _dart_package_name(repo) if repo is not None else ""
    resolutions: list[ImportResolution] = []
    for entry in entries:
        if entry.parse_status != "ok":
            continue
        if entry.language == "python":
            for occurrence in entry.import_occurrences:
                target_path, match_kind = _resolve_repo_local_python_import(
                    occurrence,
                    importer_path=entry.path,
                    module_index=python_modules,
                    entries_by_path=entries_by_path,
                )
                if not target_path:
                    continue
                resolutions.append(
                    ImportResolution(
                        importer_path=entry.path,
                        language=entry.language,
                        raw_import=occurrence.raw_import,
                        form=occurrence.form,
                        module=occurrence.module,
                        imported_name=occurrence.imported_name,
                        level=occurrence.level,
                        target_path=target_path,
                        provider="python_import_resolver",
                        match_kind=match_kind,
                    )
                )
            continue
        for raw_import in entry.imports:
            if entry.language in {"javascript", "typescript"}:
                target_path = _resolve_js_ts_relative_import(raw_import, file_paths, importer_path=entry.path)
                provider = "js_ts_relative_import_resolver"
                match_kind = "path_exact"
            elif entry.language == "dart":
                target_path = _resolve_dart_import(raw_import, file_paths, importer_path=entry.path, package_name=dart_package_name)
                provider = "dart_import_resolver"
                match_kind = "path_exact"
            else:
                continue
            if target_path:
                resolutions.append(
                    ImportResolution(
                        importer_path=entry.path,
                        language=entry.language,
                        raw_import=raw_import,
                        form="raw",
                        module=raw_import,
                        imported_name="",
                        level=0,
                        target_path=target_path,
                        provider=provider,
                        match_kind=match_kind,
                    )
                )
    return sorted(resolutions, key=lambda item: (*item.occurrence_key, item.target_path)), {
        "providers": ["python_import_resolver", "js_ts_relative_import_resolver", "dart_import_resolver"],
        "languages": sorted(IMPORT_RESOLVER_LANGUAGES),
        "analyzed_paths": sorted(entry.path for entry in entries if entry.language in IMPORT_RESOLVER_LANGUAGES),
        "resolution_count": len(resolutions),
        "python_import_roots": [
            {"path": path, "module_prefix": module_prefix}
            for path, module_prefix in python_import_roots
        ],
    }


def _resolve_repo_local_python_import(
    occurrence: PythonImportOccurrence,
    *,
    importer_path: str,
    module_index: _PythonModuleIndex,
    entries_by_path: dict[str, CodeIndexEntry],
) -> tuple[str, PythonImportMatch]:
    if occurrence.form == "module":
        return _unique_python_module(occurrence.module, module_index), "module_exact"

    base_modules = _from_import_base_modules(
        occurrence,
        importer_path=importer_path,
        module_index=module_index,
    )
    if not base_modules:
        return "", ""
    if occurrence.imported_name == "*":
        targets = {_unique_python_module(base_module, module_index) for base_module in base_modules}
        if "" in targets or len(targets) != 1:
            return "", ""
        return next(iter(targets)), "module_exact"
    resolved = {
        _resolve_python_from_target(
            base_module,
            occurrence.imported_name,
            module_index=module_index,
            entries_by_path=entries_by_path,
        )
        for base_module in base_modules
    }
    if len(resolved) != 1:
        return "", ""
    return next(iter(resolved))


def _unique_python_module(module: str, module_index: _PythonModuleIndex) -> str:
    candidates = module_index.by_name.get(module, set())
    return next(iter(candidates)) if len(candidates) == 1 else ""


def _from_import_base_modules(
    occurrence: PythonImportOccurrence,
    *,
    importer_path: str,
    module_index: _PythonModuleIndex,
) -> set[str]:
    if occurrence.level == 0:
        return {occurrence.module} if occurrence.module else set()
    importer_modules = module_index.by_path.get(importer_path, set())
    if not importer_modules:
        return set()
    bases: set[str] = set()
    for importer_module in importer_modules:
        package_parts = importer_module.split(".")
        if not importer_path.endswith("/__init__.py"):
            package_parts = package_parts[:-1]
        keep = len(package_parts) - (occurrence.level - 1)
        if keep <= 0:
            return set()
        parts = [*package_parts[:keep], *[part for part in occurrence.module.split(".") if part]]
        if not parts:
            return set()
        bases.add(".".join(parts))
    return bases


def _resolve_python_from_target(
    base_module: str,
    imported_name: str,
    *,
    module_index: _PythonModuleIndex,
    entries_by_path: dict[str, CodeIndexEntry],
) -> tuple[str, PythonImportMatch]:
    base_candidates = module_index.by_name.get(base_module, set())
    if len(base_candidates) > 1:
        return "", ""
    if len(base_candidates) == 1:
        base_path = next(iter(base_candidates))
        entry = entries_by_path.get(base_path)
        if entry is None:
            return "", ""
        if entry.module_wildcard_import:
            return "", ""
        if imported_name in entry.module_certain_bindings:
            return base_path, "attribute_exact"
        if imported_name in entry.module_bindings:
            return "", ""
        if "__getattr__" in entry.module_bindings:
            return "", ""
        if not base_path.endswith("/__init__.py"):
            return "", ""
    submodule_path = _unique_python_module(f"{base_module}.{imported_name}", module_index)
    return (submodule_path, "submodule_exact") if submodule_path else ("", "")


def _python_import_roots(repo: Path | None) -> list[tuple[str, str]]:
    roots: set[tuple[str, str]] = {("", "")}
    if repo is None:
        return sorted(roots)
    pyproject = repo / "pyproject.toml"
    try:
        data = tomllib.loads(pyproject.read_text(encoding="utf-8"))
    except (OSError, UnicodeDecodeError, tomllib.TOMLDecodeError):
        return sorted(roots)
    tool = data.get("tool") if isinstance(data.get("tool"), dict) else {}
    setuptools = tool.get("setuptools") if isinstance(tool.get("setuptools"), dict) else {}
    package_dir = setuptools.get("package-dir") if isinstance(setuptools.get("package-dir"), dict) else {}
    for module_prefix, raw_path in package_dir.items():
        root = _normalized_import_root(raw_path)
        prefix = str(module_prefix).strip(".")
        if root is not None and all(part.isidentifier() for part in prefix.split(".") if part):
            roots.add((root, prefix))
    packages = setuptools.get("packages") if isinstance(setuptools.get("packages"), dict) else {}
    find = packages.get("find") if isinstance(packages.get("find"), dict) else {}
    where = find.get("where")
    raw_roots = where if isinstance(where, list) else [where] if isinstance(where, str) else []
    for raw_path in raw_roots:
        root = _normalized_import_root(raw_path)
        if root is not None:
            roots.add((root, ""))
    return sorted(roots)


def _normalized_import_root(raw_path: object) -> str | None:
    if not isinstance(raw_path, str):
        return None
    value = raw_path.strip().replace("\\", "/")
    normalized = normpath(value or ".")
    if normalized == ".":
        return ""
    if (
        normalized.startswith("/")
        or (len(normalized) > 1 and normalized[1] == ":")
        or normalized == ".."
        or normalized.startswith("../")
    ):
        return None
    return normalized.rstrip("/")


def _python_module_index(file_paths: set[str], *, import_roots: list[tuple[str, str]]) -> _PythonModuleIndex:
    modules: dict[str, set[str]] = {}
    paths: dict[str, set[str]] = {}
    for path in sorted(file_paths):
        if not path.endswith(".py"):
            continue
        for root, module_prefix in import_roots:
            relative = _relative_to_import_root(path, root)
            if relative is None:
                continue
            parts = relative.split("/")
            filename = parts[-1]
            module_parts = parts[:-1] if filename == "__init__.py" else [*parts[:-1], filename[:-3]]
            if module_prefix:
                module_parts = [*module_prefix.split("."), *module_parts]
            if not module_parts or not all(part.isidentifier() for part in module_parts):
                continue
            module = ".".join(module_parts)
            modules.setdefault(module, set()).add(path)
            paths.setdefault(path, set()).add(module)
    return _PythonModuleIndex(by_name=modules, by_path=paths)


def _relative_to_import_root(path: str, root: str) -> str | None:
    if not root:
        return path
    prefix = f"{root}/"
    if not path.startswith(prefix):
        return None
    return path.removeprefix(prefix)


def _resolve_js_ts_relative_import(raw_import: str, file_paths: set[str], *, importer_path: str) -> str:
    if not raw_import.startswith(("./", "../")):
        return ""
    importer_dir = "/".join(importer_path.split("/")[:-1])
    module_path = normpath(f"{importer_dir}/{raw_import}" if importer_dir else raw_import)
    if module_path == "." or module_path.startswith("../"):
        return ""
    candidates = [candidate for candidate in _js_ts_candidates(module_path) if candidate in file_paths]
    if len(candidates) == 1:
        return candidates[0]
    return ""


def _js_ts_candidates(module_path: str) -> list[str]:
    suffixes = (".ts", ".tsx", ".js", ".jsx", ".mjs", ".cjs")
    values = [module_path]
    values.extend(f"{module_path}{suffix}" for suffix in suffixes)
    values.extend(f"{module_path}/index{suffix}" for suffix in suffixes)
    return values


def _resolve_dart_import(raw_import: str, file_paths: set[str], *, importer_path: str, package_name: str) -> str:
    if raw_import.startswith(("./", "../")):
        importer_dir = "/".join(importer_path.split("/")[:-1])
        module_path = normpath(f"{importer_dir}/{raw_import}" if importer_dir else raw_import)
        if module_path == "." or module_path.startswith("../"):
            return ""
        return module_path if module_path in file_paths and module_path.endswith(".dart") else ""
    package_prefix = f"package:{package_name}/" if package_name else ""
    if package_prefix and raw_import.startswith(package_prefix):
        target = f"lib/{raw_import[len(package_prefix):]}"
        return target if target in file_paths and target.endswith(".dart") else ""
    return ""


def _dart_package_name(repo: Path | None) -> str:
    if repo is None:
        return ""
    path = repo / "pubspec.yaml"
    try:
        lines = path.read_text(encoding="utf-8").splitlines()
    except (OSError, UnicodeDecodeError):
        return ""
    for line in lines:
        stripped = line.strip()
        if stripped.startswith("name:"):
            return stripped.split(":", 1)[1].strip().strip("'\"")
    return ""

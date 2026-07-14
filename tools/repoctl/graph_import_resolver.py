from __future__ import annotations

import tomllib
from dataclasses import dataclass
from pathlib import Path
from posixpath import normpath

from .code_index import CodeIndexEntry


IMPORT_RESOLVER_LANGUAGES = frozenset({"python", "javascript", "typescript", "dart"})


@dataclass(frozen=True)
class ImportResolution:
    importer_path: str
    language: str
    raw_import: str
    target_path: str
    provider: str

    def to_dict(self) -> dict[str, str]:
        return {
            "importer_path": self.importer_path,
            "language": self.language,
            "raw_import": self.raw_import,
            "target_path": self.target_path,
            "provider": self.provider,
        }


def resolve_code_imports(entries: list[CodeIndexEntry], *, repo: Path | None = None) -> tuple[list[ImportResolution], dict[str, object]]:
    file_paths = {entry.path for entry in entries}
    python_import_roots = _python_import_roots(repo)
    python_modules = _python_module_index(file_paths, import_roots=python_import_roots)
    dart_package_name = _dart_package_name(repo) if repo is not None else ""
    resolutions: list[ImportResolution] = []
    for entry in entries:
        if entry.parse_status != "ok":
            continue
        for raw_import in entry.imports:
            if entry.language == "python":
                target_path = _resolve_repo_local_python_import(
                    raw_import,
                    file_paths,
                    importer_path=entry.path,
                    module_index=python_modules,
                )
                provider = "python_import_resolver"
            elif entry.language in {"javascript", "typescript"}:
                target_path = _resolve_js_ts_relative_import(raw_import, file_paths, importer_path=entry.path)
                provider = "js_ts_relative_import_resolver"
            elif entry.language == "dart":
                target_path = _resolve_dart_import(raw_import, file_paths, importer_path=entry.path, package_name=dart_package_name)
                provider = "dart_import_resolver"
            else:
                continue
            if target_path:
                resolutions.append(
                    ImportResolution(
                        importer_path=entry.path,
                        language=entry.language,
                        raw_import=raw_import,
                        target_path=target_path,
                        provider=provider,
                    )
                )
    return sorted(resolutions, key=lambda item: (item.importer_path, item.raw_import, item.target_path)), {
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
    raw_import: str,
    file_paths: set[str],
    *,
    importer_path: str,
    module_index: dict[str, set[str]],
) -> str:
    prefix_parts = _relative_prefix(raw_import, importer_path)
    if raw_import.startswith(".") and prefix_parts is None:
        return ""
    if not raw_import.startswith("."):
        parts = [part for part in raw_import.split(".") if part]
        for length in range(len(parts), 0, -1):
            candidates = module_index.get(".".join(parts[:length]), set())
            if len(candidates) == 1:
                return next(iter(candidates))
            if len(candidates) > 1:
                return ""
        return ""
    parts = [*(prefix_parts or []), *[part for part in raw_import.lstrip(".").split(".") if part]]
    for length in range(len(parts), 0, -1):
        module_path = "/".join(parts[:length])
        candidates = [candidate for candidate in (f"{module_path}.py", f"{module_path}/__init__.py") if candidate in file_paths]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return ""
    return ""


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


def _python_module_index(file_paths: set[str], *, import_roots: list[tuple[str, str]]) -> dict[str, set[str]]:
    modules: dict[str, set[str]] = {}
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
            modules.setdefault(".".join(module_parts), set()).add(path)
    return modules


def _relative_to_import_root(path: str, root: str) -> str | None:
    if not root:
        return path
    prefix = f"{root}/"
    if not path.startswith(prefix):
        return None
    return path.removeprefix(prefix)


def _relative_prefix(raw_import: str, importer_path: str) -> list[str] | None:
    if not raw_import.startswith("."):
        return []
    level = len(raw_import) - len(raw_import.lstrip("."))
    package_parts = importer_path.split("/")[:-1]
    base_length = len(package_parts) - (level - 1)
    if base_length < 0:
        return None
    return package_parts[:base_length]


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

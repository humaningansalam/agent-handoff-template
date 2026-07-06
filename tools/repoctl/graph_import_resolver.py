from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path
from posixpath import normpath

from .code_index import CodeIndexEntry


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


def resolve_code_imports(entries: list[CodeIndexEntry], *, repo: Path | None = None) -> list[ImportResolution]:
    file_paths = {entry.path for entry in entries}
    dart_package_name = _dart_package_name(repo) if repo is not None else ""
    resolutions: list[ImportResolution] = []
    for entry in entries:
        if entry.parse_status != "ok":
            continue
        for raw_import in entry.imports:
            if entry.language == "python":
                target_path = _resolve_repo_local_python_import(raw_import, file_paths, importer_path=entry.path)
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
    return sorted(resolutions, key=lambda item: (item.importer_path, item.raw_import, item.target_path))


def _resolve_repo_local_python_import(raw_import: str, file_paths: set[str], *, importer_path: str) -> str:
    prefix_parts = _relative_prefix(raw_import, importer_path)
    if raw_import.startswith(".") and prefix_parts is None:
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

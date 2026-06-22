from __future__ import annotations

from dataclasses import dataclass

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


def resolve_python_imports(entries: list[CodeIndexEntry]) -> list[ImportResolution]:
    file_paths = {entry.path for entry in entries}
    resolutions: list[ImportResolution] = []
    for entry in entries:
        if entry.language != "python" or entry.parse_status != "ok":
            continue
        for raw_import in entry.imports:
            target_path = _resolve_repo_local_python_import(raw_import, file_paths)
            if target_path:
                resolutions.append(
                    ImportResolution(
                        importer_path=entry.path,
                        language=entry.language,
                        raw_import=raw_import,
                        target_path=target_path,
                        provider="python_import_resolver",
                    )
                )
    return sorted(resolutions, key=lambda item: (item.importer_path, item.raw_import, item.target_path))


def _resolve_repo_local_python_import(raw_import: str, file_paths: set[str]) -> str:
    if raw_import.startswith("."):
        return ""
    parts = [part for part in raw_import.split(".") if part]
    for length in range(len(parts), 0, -1):
        module_path = "/".join(parts[:length])
        candidates = [candidate for candidate in (f"{module_path}.py", f"{module_path}/__init__.py") if candidate in file_paths]
        if len(candidates) == 1:
            return candidates[0]
        if len(candidates) > 1:
            return ""
    return ""

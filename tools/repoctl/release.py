from __future__ import annotations

import json
import sys
import tarfile
from pathlib import Path
from typing import Any

MANIFEST_REL = Path("repoctl-upgrade-manifest.json")


def _load_manifest(root: Path) -> dict[str, Any]:
    manifest = json.loads((root / MANIFEST_REL).read_text(encoding="utf-8"))
    for key in ("replace_paths", "create_paths"):
        value = manifest.get(key, [])
        if not isinstance(value, list) or not all(isinstance(path, str) for path in value):
            raise SystemExit(f"manifest {key} must be a list of strings")
    return manifest


def _safe_path(value: str) -> Path:
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts or value in {"", "."}:
        raise SystemExit(f"unsafe manifest path: {value}")
    return rel


def _literal_preserve_files(root: Path, manifest: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for value in manifest.get("preserve_paths", []):
        if not isinstance(value, str) or any(char in value for char in "*?["):
            continue
        rel = _safe_path(value)
        if (root / rel).is_file():
            paths.append(rel)
    return paths


def build_release_archive(root: Path, out_dir: Path) -> Path:
    manifest = _load_manifest(root)
    version = str(manifest.get("version") or "0.0.0")
    package = str(manifest.get("package") or "agent-workspace-control-plane")
    archive_name = f"{package}-{version}.tar.gz"
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / archive_name
    paths = [
        MANIFEST_REL,
        *[_safe_path(path) for path in manifest.get("replace_paths", [])],
        *[_safe_path(path) for path in manifest.get("create_paths", [])],
        *_literal_preserve_files(root, manifest),
    ]
    seen: set[str] = set()
    prefix = f"{package}-{version}"
    with tarfile.open(archive_path, "w:gz") as archive:
        for rel in paths:
            rel_posix = rel.as_posix()
            if rel_posix in seen:
                continue
            seen.add(rel_posix)
            source = root / rel
            if not source.is_file():
                raise SystemExit(f"release path missing: {rel_posix}")
            archive.add(source, arcname=f"{prefix}/{rel_posix}")
    return archive_path


def main(argv: list[str] | None = None) -> int:
    args = list(argv) if argv is not None else sys.argv[1:]
    out_dir = Path(args[0]) if args else Path("dist")
    archive_path = build_release_archive(Path.cwd(), out_dir)
    print(archive_path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

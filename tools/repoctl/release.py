from __future__ import annotations

import argparse
import gzip
import json
import os
import re
import tarfile
import tempfile
from pathlib import Path
from typing import Any

MANIFEST_REL = Path("repoctl-upgrade-manifest.json")
ARCHIVE_COMPONENT_RE = re.compile(r"[A-Za-z0-9][A-Za-z0-9._+-]*")


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


def _archive_component(manifest: dict[str, Any], key: str, default: str) -> str:
    value = manifest.get(key, default)
    if not isinstance(value, str) or ARCHIVE_COMPONENT_RE.fullmatch(value) is None:
        raise SystemExit(f"manifest {key} is not a safe archive component")
    return value


def _literal_preserve_files(root: Path, manifest: dict[str, Any]) -> list[Path]:
    paths: list[Path] = []
    for value in manifest.get("preserve_paths", []):
        if not isinstance(value, str) or any(char in value for char in "*?["):
            continue
        rel = _safe_path(value)
        if (root / rel).is_file():
            paths.append(rel)
    return paths


def _release_file(root: Path, rel: Path) -> Path:
    source = root / rel
    try:
        source.resolve().relative_to(root.resolve())
    except (OSError, ValueError) as exc:
        raise SystemExit(f"release path escapes source root: {rel.as_posix()}") from exc
    if source.is_symlink() or not source.is_file():
        raise SystemExit(f"release path missing or not a regular file: {rel.as_posix()}")
    return source


def _reproducible_tarinfo(info: tarfile.TarInfo) -> tarfile.TarInfo:
    info.uid = info.gid = 0
    info.uname = info.gname = ""
    info.mtime = 0
    info.pax_headers = {}
    return info


def build_release_archive(root: Path, out_dir: Path) -> Path:
    manifest = _load_manifest(root)
    version = _archive_component(manifest, "version", "0.0.0")
    package = _archive_component(manifest, "package", "agent-workspace-control-plane")
    archive_name = f"{package}-{version}.tar.gz"
    paths = [
        MANIFEST_REL,
        *[_safe_path(path) for path in manifest.get("replace_paths", [])],
        *[_safe_path(path) for path in manifest.get("create_paths", [])],
        *_literal_preserve_files(root, manifest),
    ]
    seen: set[str] = set()
    files: list[tuple[str, Path]] = []
    for rel in paths:
        rel_posix = rel.as_posix()
        if rel_posix in seen:
            continue
        seen.add(rel_posix)
        files.append((rel_posix, _release_file(root, rel)))
    out_dir.mkdir(parents=True, exist_ok=True)
    archive_path = out_dir / archive_name
    prefix = f"{package}-{version}"
    raw_archive = tempfile.NamedTemporaryFile(dir=out_dir, prefix=f".{archive_name}.", suffix=".tmp", delete=False)
    temporary_path = Path(raw_archive.name)
    try:
        with raw_archive:
            with gzip.GzipFile(filename="", mode="wb", fileobj=raw_archive, mtime=0) as compressed:
                with tarfile.open(fileobj=compressed, mode="w") as archive:
                    for rel_posix, source in files:
                        archive.add(source, arcname=f"{prefix}/{rel_posix}", filter=_reproducible_tarinfo)
        os.replace(temporary_path, archive_path)
    finally:
        temporary_path.unlink(missing_ok=True)
    return archive_path


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="python -m tools.repoctl.release",
        description="Build the repoctl adopter release archive.",
    )
    parser.add_argument("output_directory", nargs="?", default=Path("dist"), type=Path)
    args = parser.parse_args(argv)
    archive_path = build_release_archive(Path.cwd(), args.output_directory)
    print(archive_path.as_posix())
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

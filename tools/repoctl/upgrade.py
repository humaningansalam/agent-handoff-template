from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
import tomllib
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import RepoctlError, atomic_write, repoctl_lock
from .markdown import parse_frontmatter
from .tasks import (
    ID_RE,
    TASK_RE,
    LIVE,
    NON_LIVE,
    archive_locator_path,
    archive_locator_text,
    completion_receipt_artifact_for_task,
    parse_archive_locator,
)

MANIFEST_REL = Path("repoctl-upgrade-manifest.json")
UPGRADE_STATE_REL = Path("docs/tasks/.repoctl-state/upgrades")
UPGRADE_POSTFLIGHT_COMMAND = ["./scripts/repoctl", "upgrade", "postflight", "--json"]
ARCHIVE_LOCATOR_MIGRATION = "archive_locator_backfill"
ARCHIVE_LOCATOR_MIGRATION_VERSION = 1
UPGRADE_PLAN_SCHEMA_VERSION = 2


@dataclass(frozen=True)
class UpgradeOperation:
    path: str
    action: str
    source_hash: str
    target_hash: str
    size: int

    def to_dict(self) -> dict[str, Any]:
        return {
            "path": self.path,
            "action": self.action,
            "source_hash": self.source_hash,
            "target_hash": self.target_hash,
            "size": self.size,
        }


def _utc_id() -> str:
    return datetime.now(timezone.utc).strftime("%Y%m%d%H%M%SZ")


def _hash_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _hash_file(path: Path) -> str:
    return _hash_bytes(path.read_bytes())


def _sha256(value: str) -> str:
    return f"sha256:{value}"


def _is_sha256_digest(value: object) -> bool:
    return (
        isinstance(value, str)
        and len(value) == 71
        and value.startswith("sha256:")
        and all(character in "0123456789abcdef" for character in value[7:])
    )


def _canonical_entry_records(path: Path, *, relative_to: Path) -> list[dict[str, Any]]:
    paths = [path]
    if path.is_dir() and not path.is_symlink():
        paths.extend(sorted(path.rglob("*"), key=lambda item: item.relative_to(relative_to).as_posix()))
    records: list[dict[str, Any]] = []
    for item in paths:
        rel = item.relative_to(relative_to).as_posix()
        try:
            mode = item.lstat().st_mode & 0o7777
        except OSError as exc:
            raise RepoctlError(f"upgrade digest path cannot be inspected: {rel}", code="upgrade_digest_failed", path=rel) from exc
        if item.is_symlink():
            records.append({"path": rel, "kind": "symlink", "mode": mode, "target": os.readlink(item)})
        elif item.is_dir():
            records.append({"path": rel, "kind": "directory", "mode": mode})
        elif item.is_file():
            records.append({"path": rel, "kind": "file", "mode": mode, "content_sha256": _sha256(_hash_file(item))})
        else:
            records.append({"path": rel, "kind": "other", "mode": mode})
    return records


def _canonical_paths_digest(root: Path, paths: list[str]) -> str:
    records: list[dict[str, Any]] = []
    for rel in sorted(set(paths)):
        path = root / rel
        if path.exists() or path.is_symlink():
            records.extend(_canonical_entry_records(path, relative_to=root))
    encoded = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(_hash_bytes(encoded))


def _canonical_tree_snapshot(path: Path) -> tuple[str, list[dict[str, Any]]]:
    if not path.exists() and not path.is_symlink():
        return "", []
    records = _canonical_entry_records(path, relative_to=path.parent)
    encoded = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(_hash_bytes(encoded)), records


def _safe_rel(value: str) -> str:
    rel = Path(value)
    if rel.is_absolute() or ".." in rel.parts or value in {"", "."}:
        raise RepoctlError(f"invalid upgrade manifest path: {value}", code="invalid_upgrade_manifest", path=value)
    return rel.as_posix()


def _assert_contained_path(root: Path, rel: str, *, code: str, require_file: bool = False) -> Path:
    safe_rel = _safe_rel(rel)
    root_resolved = root.resolve()
    current = root
    parts = Path(safe_rel).parts
    for part in parts[:-1]:
        current = current / part
        if current.exists() or current.is_symlink():
            if current.is_symlink():
                raise RepoctlError(f"upgrade path parent must not be a symlink: {safe_rel}", code=code, path=safe_rel)
            try:
                current.resolve().relative_to(root_resolved)
            except ValueError as exc:
                raise RepoctlError(f"upgrade path escapes workspace: {safe_rel}", code=code, path=safe_rel) from exc
    target = root / safe_rel
    if target.exists() or target.is_symlink():
        if target.is_symlink():
            raise RepoctlError(f"upgrade path must not be a symlink: {safe_rel}", code=code, path=safe_rel)
        try:
            target.resolve().relative_to(root_resolved)
        except ValueError as exc:
            raise RepoctlError(f"upgrade path escapes workspace: {safe_rel}", code=code, path=safe_rel) from exc
    if require_file and not target.is_file():
        raise RepoctlError(f"upgrade path is not a file: {safe_rel}", code=code, path=safe_rel)
    return target


def _load_manifest(source_root: Path) -> dict[str, Any]:
    manifest_path = source_root / MANIFEST_REL
    if not manifest_path.is_file():
        raise RepoctlError(f"upgrade manifest not found: {manifest_path}", code="missing_upgrade_manifest", path=MANIFEST_REL.as_posix())
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RepoctlError(f"invalid upgrade manifest JSON: {error}", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix()) from error
    replace_paths = manifest.get("replace_paths")
    create_paths = manifest.get("create_paths", [])
    remove_paths = manifest.get("remove_paths", [])
    preserve_paths = manifest.get("preserve_paths")
    postflight_command = manifest.get("postflight_command", [])
    if not isinstance(replace_paths, list) or not all(isinstance(path, str) for path in replace_paths):
        raise RepoctlError("upgrade manifest replace_paths must be a list of strings", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    if not isinstance(create_paths, list) or not all(isinstance(path, str) for path in create_paths):
        raise RepoctlError("upgrade manifest create_paths must be a list of strings", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    if not isinstance(remove_paths, list) or not all(isinstance(path, str) for path in remove_paths):
        raise RepoctlError("upgrade manifest remove_paths must be a list of strings", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    if not isinstance(preserve_paths, list) or not all(isinstance(path, str) for path in preserve_paths):
        raise RepoctlError("upgrade manifest preserve_paths must be a list of strings", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    if postflight_command not in ([], UPGRADE_POSTFLIGHT_COMMAND):
        raise RepoctlError(
            "upgrade manifest postflight_command must be the canonical repoctl postflight command",
            code="invalid_upgrade_manifest",
            path=MANIFEST_REL.as_posix(),
        )
    manifest["replace_paths"] = sorted({_safe_rel(path) for path in replace_paths})
    manifest["create_paths"] = sorted({_safe_rel(path) for path in create_paths})
    manifest["remove_paths"] = sorted({_safe_rel(path) for path in remove_paths})
    manifest["preserve_paths"] = sorted({_safe_rel(path) for path in preserve_paths})
    manifest["postflight_command"] = list(postflight_command)
    managed = [*manifest["replace_paths"], *manifest["create_paths"], *manifest["remove_paths"]]
    if len(set(managed)) != len(managed):
        raise RepoctlError("upgrade manifest paths cannot appear in more than one managed path list", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    for path in managed:
        if _is_preserved(path, manifest["preserve_paths"]):
            raise RepoctlError(f"upgrade path is both managed and preserved: {path}", code="invalid_upgrade_manifest", path=path)
    return manifest


def _is_preserved(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _preserve_seed_paths(source_root: Path, manifest: dict[str, Any]) -> list[str]:
    seeds: list[str] = []
    for value in manifest["preserve_paths"]:
        if any(char in value for char in "*?["):
            continue
        rel = _safe_rel(value)
        if Path(rel).name != ".gitkeep":
            continue
        if (source_root / rel).is_file():
            seeds.append(rel)
    return sorted(set(seeds))


def _source_root(source: str | Path) -> Path:
    root = Path(source).expanduser().resolve()
    if not root.is_dir():
        raise RepoctlError(f"upgrade source must be a directory: {source}", code="invalid_upgrade_source", path=str(source))
    return root


def _reported_version(root: Path) -> str:
    versions: list[str] = []
    pyproject = root / "pyproject.toml"
    if pyproject.exists():
        if pyproject.is_symlink() or not pyproject.is_file():
            raise RepoctlError("target pyproject is not a regular file", code="invalid_upgrade_target", path="pyproject.toml")
        try:
            project = tomllib.loads(pyproject.read_text(encoding="utf-8")).get("project", {})
        except (OSError, UnicodeError, tomllib.TOMLDecodeError) as exc:
            raise RepoctlError("target pyproject version is unreadable", code="invalid_upgrade_target", path="pyproject.toml") from exc
        if isinstance(project, dict) and project.get("version"):
            versions.append(str(project["version"]))
    target_manifest = root / MANIFEST_REL
    if target_manifest.exists():
        if target_manifest.is_symlink() or not target_manifest.is_file():
            raise RepoctlError("target upgrade manifest is not a regular file", code="invalid_upgrade_target", path=MANIFEST_REL.as_posix())
        try:
            manifest = json.loads(target_manifest.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, json.JSONDecodeError) as exc:
            raise RepoctlError("target upgrade manifest version is unreadable", code="invalid_upgrade_target", path=MANIFEST_REL.as_posix()) from exc
        if isinstance(manifest, dict) and manifest.get("version"):
            versions.append(str(manifest["version"]))
    if len(set(versions)) > 1:
        raise RepoctlError("target repoctl versions disagree", code="upgrade_target_version_mismatch")
    return versions[0] if versions else ""


def _plan_payload(
    root: Path,
    source_root: Path,
    manifest: dict[str, Any],
    operations: list[UpgradeOperation],
    conflicts: list[dict[str, str]],
    migrations: list[dict[str, Any]],
) -> dict[str, Any]:
    source_paths = [*manifest["replace_paths"], *manifest["create_paths"], *_preserve_seed_paths(source_root, manifest)]
    data = {
        "schema_version": UPGRADE_PLAN_SCHEMA_VERSION,
        "package": manifest.get("package", "agent-workspace-control-plane"),
        "source_version": str(manifest.get("version", "")),
        "source_root": source_root.as_posix(),
        "source_content_digest": _canonical_paths_digest(source_root, source_paths),
        "workspace_root": root.as_posix(),
        "manifest_path": MANIFEST_REL.as_posix(),
        "replace_paths": manifest["replace_paths"],
        "create_paths": manifest["create_paths"],
        "remove_paths": manifest["remove_paths"],
        "preserve_paths": manifest["preserve_paths"],
        "postflight_command": manifest["postflight_command"],
        "operations": [operation.to_dict() for operation in operations],
        "migrations": migrations,
        "conflicts": conflicts,
    }
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    data["plan_sha256"] = _hash_bytes(encoded)
    return data


def _canonical_plan_hash(plan: dict[str, Any]) -> str:
    data = dict(plan)
    data.pop("plan_sha256", None)
    encoded = json.dumps(data, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _hash_bytes(encoded)


def plan_upgrade(root: Path, *, source: str | Path) -> dict[str, Any]:
    source_root = _source_root(source)
    manifest = _load_manifest(source_root)
    target_version = _reported_version(root)
    operations: list[UpgradeOperation] = []
    conflicts: list[dict[str, str]] = []
    for rel in manifest["replace_paths"]:
        source_path = _assert_contained_path(source_root, rel, code="invalid_upgrade_source")
        target_path = _assert_contained_path(root, rel, code="invalid_upgrade_target")
        if not source_path.is_file():
            conflicts.append({"code": "managed_source_missing", "path": rel, "message": "managed source file is missing"})
            continue
        if target_path.exists() and not target_path.is_file():
            conflicts.append({"code": "target_not_file", "path": rel, "message": "target path exists but is not a file"})
            continue
        source_bytes = source_path.read_bytes()
        source_hash = _hash_bytes(source_bytes)
        target_hash = _hash_file(target_path) if target_path.is_file() else ""
        if source_hash == target_hash:
            continue
        operations.append(
            UpgradeOperation(
                path=rel,
                action="create" if not target_path.exists() else "replace",
                source_hash=source_hash,
                target_hash=target_hash,
                size=len(source_bytes),
            )
        )
    for rel in manifest["create_paths"]:
        source_path = _assert_contained_path(source_root, rel, code="invalid_upgrade_source")
        target_path = _assert_contained_path(root, rel, code="invalid_upgrade_target")
        if not source_path.is_file():
            conflicts.append({"code": "managed_source_missing", "path": rel, "message": "managed source file is missing"})
            continue
        if target_path.exists():
            continue
        source_bytes = source_path.read_bytes()
        operations.append(
            UpgradeOperation(
                path=rel,
                action="create",
                source_hash=_hash_bytes(source_bytes),
                target_hash="",
                size=len(source_bytes),
            )
        )
    for rel in manifest["remove_paths"]:
        target_path = _assert_contained_path(root, rel, code="invalid_upgrade_target")
        if not target_path.exists():
            continue
        if not target_path.is_file():
            conflicts.append({"code": "remove_target_not_file", "path": rel, "message": "remove target exists but is not a file"})
            continue
        target_bytes = target_path.read_bytes()
        operations.append(
            UpgradeOperation(
                path=rel,
                action="remove",
                source_hash="",
                target_hash=_hash_bytes(target_bytes),
                size=len(target_bytes),
            )
        )
    for rel in _preserve_seed_paths(source_root, manifest):
        source_path = _assert_contained_path(source_root, rel, code="invalid_upgrade_source")
        target_path = _assert_contained_path(root, rel, code="invalid_upgrade_target")
        if target_path.exists():
            continue
        if not source_path.is_file():
            conflicts.append({"code": "managed_source_missing", "path": rel, "message": "preserve seed source file is missing"})
            continue
        source_bytes = source_path.read_bytes()
        operations.append(
            UpgradeOperation(
                path=rel,
                action="seed_preserve",
                source_hash=_hash_bytes(source_bytes),
                target_hash="",
                size=len(source_bytes),
            )
        )
    migrations, migration_conflicts = _plan_archive_locator_migration(root)
    if target_version == str(manifest.get("version") or "") and (
        operations or conflicts or migrations or migration_conflicts
    ):
        raise RepoctlError(
            "same-version repoctl content differs; publish and use a new release version",
            code="same_version_managed_content_drift",
        )
    return _plan_payload(root, source_root, manifest, operations, [*conflicts, *migration_conflicts], migrations)


def write_plan(path: Path, payload: dict[str, Any]) -> None:
    atomic_write(path, json.dumps(payload, ensure_ascii=False, indent=2) + "\n")


def _atomic_copy_file(source: Path, target: Path) -> None:
    target.parent.mkdir(parents=True, exist_ok=True)
    tmp = target.with_name(f".{target.name}.{os.getpid()}.tmp")
    try:
        with source.open("rb") as src, tmp.open("wb") as dst:
            shutil.copyfileobj(src, dst)
            dst.flush()
            os.fsync(dst.fileno())
        shutil.copystat(source, tmp)
        os.replace(tmp, target)
    except Exception:
        if tmp.exists() and not tmp.is_symlink():
            tmp.unlink()
        raise


def _migration_conflict(code: str, message: str, path: str) -> dict[str, str]:
    return {"code": code, "message": message, "path": path}


def _read_exact_regular_file(root: Path, rel: str, *, code: str) -> tuple[Path, bytes]:
    path = _assert_contained_path(root, rel, code=code, require_file=True)
    if path.is_symlink():
        raise RepoctlError(f"migration authority must not be a symlink: {rel}", code=code, path=rel)
    try:
        return path, path.read_bytes()
    except OSError as exc:
        raise RepoctlError(f"migration authority is unreadable: {rel}", code=code, path=rel) from exc


def _valid_live_follow_up(path: Path, root: Path) -> tuple[str, str]:
    rel = path.relative_to(root).as_posix()
    _path, raw = _read_exact_regular_file(root, rel, code="archive_locator_migration_conflict")
    try:
        frontmatter, _body = parse_frontmatter(raw.decode("utf-8"))
    except (UnicodeDecodeError, RepoctlError) as exc:
        raise RepoctlError("live follow-up task is invalid", code="archive_locator_migration_conflict", path=rel) from exc
    filename = TASK_RE.fullmatch(path.name)
    task_id = str(frontmatter.get("id") or "")
    follow_up_of = str(frontmatter.get("follow_up_of") or "")
    if frontmatter.get("status") not in LIVE or not follow_up_of:
        return "", _sha256(_hash_bytes(raw))
    if filename is None or filename.group(1) != task_id:
        raise RepoctlError("live follow-up task identity or status is invalid", code="archive_locator_migration_conflict", path=rel)
    if ID_RE.fullmatch(follow_up_of) is None:
        raise RepoctlError("live follow-up task has an invalid predecessor identity", code="archive_locator_migration_conflict", path=rel)
    return follow_up_of, _sha256(_hash_bytes(raw))


def _valid_archive_authority(path: Path, root: Path, task_id: str) -> tuple[str, str]:
    rel = path.relative_to(root).as_posix()
    _path, raw = _read_exact_regular_file(root, rel, code="archive_locator_migration_conflict")
    try:
        frontmatter, _body = parse_frontmatter(raw.decode("utf-8"))
    except (UnicodeDecodeError, RepoctlError) as exc:
        raise RepoctlError("archived task is invalid", code="archive_locator_migration_conflict", path=rel) from exc
    filename = TASK_RE.fullmatch(path.name)
    if (
        filename is None
        or filename.group(1) != task_id
        or frontmatter.get("id") != task_id
        or frontmatter.get("status") not in NON_LIVE
    ):
        raise RepoctlError("archived task identity or status is invalid", code="archive_locator_migration_conflict", path=rel)
    return rel, _sha256(_hash_bytes(raw))


def _existing_locator_binding(root: Path, task_id: str) -> tuple[str, str, str, str]:
    """Return the validated existing locator and its archived authority."""

    rel = archive_locator_path(root, task_id).relative_to(root).as_posix()
    path = _assert_contained_path(root, rel, code="archive_locator_migration_conflict")
    if not path.exists() and not path.is_symlink():
        return "missing", "", "", ""
    if path.is_symlink() or not path.is_file():
        raise RepoctlError("archive locator is not a regular file", code="archive_locator_migration_conflict", path=rel)
    try:
        text = path.read_text(encoding="utf-8")
        archive_rel = parse_archive_locator(text, task_id=task_id)
    except (OSError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise RepoctlError("archive locator is unreadable", code="archive_locator_migration_conflict", path=rel) from exc
    except ValueError as exc:
        raise RepoctlError("archive locator conflicts with archived task authority", code="archive_locator_migration_conflict", path=rel) from exc
    archive_path = _assert_contained_path(root, archive_rel, code="archive_locator_migration_conflict", require_file=True)
    validated_rel, archive_digest = _valid_archive_authority(archive_path, root, task_id)
    return "current", validated_rel, archive_digest, _sha256(_hash_bytes(text.encode("utf-8")))


def _plan_archive_locator_migration(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    """Plan the bounded legacy migration from exact live follow-up identities."""

    task_directory = root / "docs/tasks"
    archive_directory = root / "docs/archive/tasks"
    if not task_directory.is_dir() or not archive_directory.is_dir():
        return [], []
    conflicts: list[dict[str, str]] = []
    references: dict[str, list[dict[str, str]]] = {}
    for task_path in sorted(task_directory.glob("T-*.md")):
        try:
            follow_up_of, task_digest = _valid_live_follow_up(task_path, root)
        except RepoctlError as exc:
            conflicts.append(_migration_conflict(exc.code, str(exc), exc.path or task_path.relative_to(root).as_posix()))
            continue
        if follow_up_of:
            references.setdefault(follow_up_of, []).append(
                {"path": task_path.relative_to(root).as_posix(), "content_sha256": task_digest}
            )
    migrations: list[dict[str, Any]] = []
    for task_id, live_sources in sorted(references.items()):
        locator_rel = archive_locator_path(root, task_id).relative_to(root).as_posix()
        try:
            locator_state, _archive_rel, _archive_digest, _locator_digest = _existing_locator_binding(root, task_id)
        except RepoctlError as exc:
            conflicts.append(_migration_conflict(exc.code, str(exc), exc.path or task_id))
            continue
        if locator_state == "current":
            continue
        receipt, receipt_problems = completion_receipt_artifact_for_task(root, task_id=task_id)
        if receipt is not None and not receipt_problems:
            continue
        retained = sorted(task_directory.glob(f"{task_id}--*.md"))
        if retained:
            if len(retained) != 1:
                conflicts.append(
                    _migration_conflict(
                        "archive_locator_migration_unresolved",
                        f"live follow-up predecessor must resolve to exactly one retained task, found {len(retained)}",
                        f"docs/tasks/{task_id}--*.md",
                    )
                )
                continue
            try:
                _valid_archive_authority(retained[0], root, task_id)
            except RepoctlError as exc:
                conflicts.append(
                    _migration_conflict(
                        exc.code,
                        str(exc),
                        exc.path or retained[0].relative_to(root).as_posix(),
                    )
                )
            continue
        matches = sorted(archive_directory.glob(f"{task_id}--*.md"))
        if len(matches) != 1:
            conflicts.append(
                _migration_conflict(
                    "archive_locator_migration_unresolved",
                    f"live follow-up predecessor must resolve to exactly one archived task, found {len(matches)}",
                    f"docs/archive/tasks/{task_id}--*.md",
                )
            )
            continue
        try:
            archive_rel, archive_digest = _valid_archive_authority(matches[0], root, task_id)
            locator_text = archive_locator_text(task_id, archive_rel)
        except RepoctlError as exc:
            conflicts.append(_migration_conflict(exc.code, str(exc), exc.path or matches[0].relative_to(root).as_posix()))
            continue
        migrations.append(
            {
                "name": ARCHIVE_LOCATOR_MIGRATION,
                "version": ARCHIVE_LOCATOR_MIGRATION_VERSION,
                "task_id": task_id,
                "live_follow_ups": live_sources,
                "archive_path": archive_rel,
                "archive_content_sha256": archive_digest,
                "locator_path": locator_rel,
                "locator_state": "missing",
                "locator_content_sha256": _sha256(_hash_bytes(locator_text.encode("utf-8"))),
                "observed_locator_sha256": "",
            }
        )
    return migrations, conflicts


def _archive_locator_migration_writes(root: Path, planned: Any) -> list[tuple[Path, str, dict[str, Any]]]:
    if not isinstance(planned, list):
        raise RepoctlError("upgrade plan migrations must be a list", code="invalid_upgrade_plan")
    writes: list[tuple[Path, str, dict[str, Any]]] = []
    for migration in planned:
        if (
            not isinstance(migration, dict)
            or migration.get("name") != ARCHIVE_LOCATOR_MIGRATION
            or migration.get("version") != ARCHIVE_LOCATOR_MIGRATION_VERSION
        ):
            raise RepoctlError("upgrade plan contains an unsupported migration", code="invalid_upgrade_plan")
        if migration.get("locator_state") == "missing":
            task_id = str(migration.get("task_id") or "")
            archive_rel = str(migration.get("archive_path") or "")
            locator_rel = str(migration.get("locator_path") or "")
            if ID_RE.fullmatch(task_id) is None:
                raise RepoctlError("upgrade plan archive locator target is invalid", code="invalid_upgrade_plan", path=locator_rel)
            expected_rel = archive_locator_path(root, task_id).relative_to(root).as_posix()
            if locator_rel != expected_rel:
                raise RepoctlError("upgrade plan archive locator target is invalid", code="invalid_upgrade_plan", path=locator_rel)
            try:
                locator_text = archive_locator_text(task_id, archive_rel)
            except ValueError as exc:
                raise RepoctlError("upgrade plan archive locator identity is invalid", code="invalid_upgrade_plan", path=locator_rel) from exc
            if _sha256(_hash_bytes(locator_text.encode("utf-8"))) != migration.get("locator_content_sha256"):
                raise RepoctlError("upgrade plan archive locator digest is invalid", code="invalid_upgrade_plan", path=locator_rel)
            writes.append((_assert_contained_path(root, locator_rel, code="invalid_upgrade_target"), locator_text, migration))
    return writes


def _prune_empty_parents(root: Path, start: Path) -> None:
    root_resolved = root.resolve()
    current = start
    while current != root:
        try:
            current.resolve().relative_to(root_resolved)
        except ValueError as exc:
            raise RepoctlError(f"upgrade cleanup path escapes workspace: {current}", code="invalid_upgrade_target", path=str(current)) from exc
        try:
            current.rmdir()
        except OSError:
            return
        current = current.parent


def _rollback_applied(root: Path, applied: list[dict[str, str]], backups: list[dict[str, Any]]) -> list[dict[str, str]]:
    backup_by_path = {backup["path"]: backup["backup_path"] for backup in backups}
    rolled_back: list[dict[str, str]] = []
    for operation in reversed(applied):
        rel = _safe_rel(str(operation["path"]))
        target = _assert_contained_path(root, rel, code="upgrade_rollback_failed")
        backup_rel = backup_by_path.get(rel, "")
        if backup_rel:
            backup_path = _assert_contained_path(root, backup_rel, code="upgrade_rollback_failed", require_file=True)
            if not backup_path.is_file():
                raise RepoctlError(f"upgrade rollback backup is missing: {backup_rel}", code="upgrade_rollback_failed", path=backup_rel)
            _atomic_copy_file(backup_path, target)
            rolled_back.append({"path": rel, "action": "restore"})
        else:
            if target.exists():
                if target.is_file() or target.is_symlink():
                    target.unlink()
                else:
                    raise RepoctlError(f"upgrade rollback target is not a file: {rel}", code="upgrade_rollback_failed", path=rel)
            rolled_back.append({"path": rel, "action": "remove_created"})
    return rolled_back


def _load_plan(path: Path) -> dict[str, Any]:
    if not path.is_file():
        raise RepoctlError(f"upgrade plan file not found: {path}", code="missing_upgrade_plan", path=str(path))
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError as error:
        raise RepoctlError(f"invalid upgrade plan JSON: {error}", code="invalid_upgrade_plan", path=str(path)) from error
    if (
        not isinstance(payload, dict)
        or payload.get("schema_version") != UPGRADE_PLAN_SCHEMA_VERSION
        or not isinstance(payload.get("operations"), list)
        or not isinstance(payload.get("migrations"), list)
    ):
        raise RepoctlError("invalid upgrade plan shape", code="invalid_upgrade_plan", path=str(path))
    expected_digest = _canonical_plan_hash(payload)
    if str(payload.get("plan_sha256") or "") != expected_digest:
        raise RepoctlError("upgrade plan digest mismatch", code="invalid_upgrade_plan", path=str(path))
    return payload


def _operation_dicts(plan: dict[str, Any]) -> list[dict[str, Any]]:
    result: list[dict[str, Any]] = []
    for operation in plan.get("operations", []):
        result.append(
            {
                "path": _safe_rel(str(operation.get("path", ""))),
                "action": str(operation.get("action") or ""),
                "source_hash": str(operation.get("source_hash") or ""),
                "target_hash": str(operation.get("target_hash") or ""),
                "size": int(operation.get("size") or 0),
            }
        )
    return sorted(result, key=lambda item: (item["path"], item["action"], item["source_hash"], item["target_hash"], item["size"]))


def _verify_plan_bound_to_source(root: Path, source_root: Path, plan: dict[str, Any]) -> list[dict[str, Any]]:
    manifest = _load_manifest(source_root)
    if str(plan.get("package") or "") != str(manifest.get("package") or "agent-workspace-control-plane"):
        raise RepoctlError("upgrade plan package does not match source manifest", code="invalid_upgrade_plan")
    if str(plan.get("source_version") or "") != str(manifest.get("version") or ""):
        raise RepoctlError("upgrade plan version does not match source manifest", code="invalid_upgrade_plan")
    for key in ("replace_paths", "create_paths", "remove_paths", "preserve_paths"):
        if sorted(plan.get(key) or []) != manifest[key]:
            raise RepoctlError(f"upgrade plan {key} does not match source manifest", code="invalid_upgrade_plan")
    if list(plan.get("postflight_command") or []) != manifest["postflight_command"]:
        raise RepoctlError("upgrade plan postflight_command does not match source manifest", code="invalid_upgrade_plan")
    if not isinstance(plan.get("migrations"), list):
        raise RepoctlError("upgrade plan migrations must be a list", code="invalid_upgrade_plan")
    managed = set(manifest["replace_paths"]) | set(manifest["create_paths"]) | set(manifest["remove_paths"])
    preserve_seeds = set(_preserve_seed_paths(source_root, manifest))
    preserved = manifest["preserve_paths"]
    for operation in plan["operations"]:
        rel = _safe_rel(str(operation.get("path", "")))
        action = str(operation.get("action") or "")
        if action == "seed_preserve":
            if rel not in preserve_seeds:
                raise RepoctlError(f"upgrade plan contains unmanaged preserve seed path: {rel}", code="invalid_upgrade_plan", path=rel)
        elif rel not in managed:
            raise RepoctlError(f"upgrade plan contains unmanaged path: {rel}", code="invalid_upgrade_plan", path=rel)
        if _is_preserved(rel, preserved) and action != "seed_preserve":
            raise RepoctlError(f"upgrade plan attempts to modify preserved path: {rel}", code="invalid_upgrade_plan", path=rel)
    expected = plan_upgrade(root, source=source_root)
    if expected.get("conflicts"):
        raise RepoctlError("upgrade source has conflicts; recreate the plan", code="upgrade_plan_stale")
    if _operation_dicts(plan) != _operation_dicts(expected):
        raise RepoctlError("upgrade plan operations do not match current source manifest and workspace state", code="upgrade_plan_stale")
    if plan["migrations"] != expected["migrations"]:
        raise RepoctlError("archive locator migration inputs changed after plan", code="upgrade_plan_stale")
    if str(plan.get("source_content_digest") or "") != str(expected.get("source_content_digest") or ""):
        raise RepoctlError("upgrade plan source digest does not match current source", code="upgrade_plan_stale")
    return expected["migrations"]


def _verify_plan_fresh(root: Path, plan: dict[str, Any]) -> None:
    if Path(str(plan.get("workspace_root", ""))).resolve() != root.resolve():
        raise RepoctlError("upgrade plan belongs to a different workspace", code="upgrade_plan_workspace_mismatch")
    for operation in plan["operations"]:
        rel = _safe_rel(str(operation.get("path", "")))
        target = _assert_contained_path(root, rel, code="invalid_upgrade_target")
        expected = str(operation.get("target_hash") or "")
        current = _hash_file(target) if target.is_file() else ""
        if current != expected:
            raise RepoctlError(f"upgrade plan is stale for {rel}", code="upgrade_plan_stale", path=rel)


def apply_upgrade(root: Path, *, plan_file: str | Path) -> dict[str, Any]:
    plan_path = Path(plan_file).expanduser().resolve()
    plan = _load_plan(plan_path)
    if plan.get("conflicts"):
        raise RepoctlError("upgrade plan has unresolved conflicts", code="upgrade_plan_has_conflicts", path=str(plan_path))
    run_id = _utc_id()
    source_root = Path(str(plan.get("source_root", ""))).resolve()
    if not source_root.is_dir():
        raise RepoctlError("upgrade source from plan is unavailable", code="invalid_upgrade_source", path=str(source_root))
    applied: list[dict[str, str]] = []
    backups: list[dict[str, Any]] = []
    backup_root = root / UPGRADE_STATE_REL / run_id / "backup"
    applied_migrations: list[dict[str, Any]] = []

    def backup_target(rel: str, target_path: Path) -> None:
        if not target_path.is_file():
            return
        backup_path = backup_root / rel
        _assert_contained_path(root, backup_path.relative_to(root).as_posix(), code="invalid_upgrade_target")
        backup_path.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(target_path, backup_path)
        backups.append(
            {
                "path": rel,
                "backup_path": backup_path.relative_to(root).as_posix(),
            }
        )

    with repoctl_lock(root):
        _verify_plan_fresh(root, plan)
        current_migrations = _verify_plan_bound_to_source(root, source_root, plan)
        migration_writes = _archive_locator_migration_writes(root, current_migrations)
        try:
            for operation in plan["operations"]:
                rel = _safe_rel(str(operation["path"]))
                action = str(operation.get("action") or "replace")
                target_path = _assert_contained_path(root, rel, code="invalid_upgrade_target")
                backup_target(rel, target_path)
                if action == "remove":
                    if target_path.exists():
                        if not target_path.is_file():
                            raise RepoctlError(f"remove target is not a file: {rel}", code="invalid_upgrade_target", path=rel)
                        target_path.unlink()
                        _prune_empty_parents(root, target_path.parent)
                else:
                    source_path = _assert_contained_path(source_root, rel, code="invalid_upgrade_source", require_file=True)
                    if not source_path.is_file():
                        raise RepoctlError(f"managed source file disappeared: {rel}", code="managed_source_missing", path=rel)
                    source_hash = _hash_file(source_path)
                    if source_hash != operation.get("source_hash"):
                        raise RepoctlError(f"managed source changed after plan: {rel}", code="upgrade_plan_stale", path=rel)
                    _atomic_copy_file(source_path, target_path)
                applied.append({"path": rel, "action": action})
            for locator_path, locator_text, migration in migration_writes:
                atomic_write(locator_path, locator_text)
                locator_rel = locator_path.relative_to(root).as_posix()
                applied.append({"path": locator_rel, "action": "migration_create"})
                applied_migrations.append(
                    {
                        "name": migration["name"],
                        "version": migration["version"],
                        "task_id": migration["task_id"],
                        "path": locator_rel,
                        "action": "create",
                    }
                )
        except Exception as error:
            rolled_back = _rollback_applied(root, applied, backups)
            rollback_path = root / UPGRADE_STATE_REL / run_id / "rollback.json"
            atomic_write(
                rollback_path,
                json.dumps(
                    {
                        "run_id": run_id,
                        "plan_file": plan_path.as_posix(),
                        "plan_sha256": plan.get("plan_sha256", ""),
                        "applied": applied,
                        "backups": backups,
                        "rolled_back": rolled_back,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
            if isinstance(error, RepoctlError):
                raise
            raise
        backup_digest = ""
        if backups:
            backup_digest, backup_records = _canonical_tree_snapshot(backup_root)
            content_digests = {
                (backup_root.parent / str(record["path"])).relative_to(root).as_posix(): str(record["content_sha256"])
                for record in backup_records
                if record.get("kind") == "file"
            }
            for item in backups:
                item["backup_digest"] = content_digests[str(item["backup_path"])]
        backup = {
            "path": backup_root.relative_to(root).as_posix(),
            "recorded_digest": backup_digest,
            "retention_status_at_creation": "manual_retention" if backups else "not_required",
        }
        try:
            receipt = {
                "run_id": run_id,
                "plan_file": plan_path.as_posix(),
                "plan_sha256": plan.get("plan_sha256", ""),
                "source_content_digest": str(plan.get("source_content_digest") or ""),
                "applied": applied,
                "backups": backups,
                "backup": backup,
                "migrations": plan.get("migrations", []),
                "applied_migrations": applied_migrations,
            }
            receipt_path = root / UPGRADE_STATE_REL / run_id / "receipt.json"
            atomic_write(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
        except Exception as error:
            rolled_back = _rollback_applied(root, applied, backups)
            rollback_path = root / UPGRADE_STATE_REL / run_id / "rollback.json"
            atomic_write(
                rollback_path,
                json.dumps(
                    {
                        "run_id": run_id,
                        "plan_file": plan_path.as_posix(),
                        "plan_sha256": plan.get("plan_sha256", ""),
                        "applied": applied,
                        "backups": backups,
                        "rolled_back": rolled_back,
                        "error": str(error),
                    },
                    ensure_ascii=False,
                    indent=2,
                )
                + "\n",
            )
            raise
    return {
        "run_id": run_id,
        "applied": applied,
        "backups": backups,
        "backup": backup,
        "migrations": plan.get("migrations", []),
        "applied_migrations": applied_migrations,
        "receipt_path": (UPGRADE_STATE_REL / run_id / "receipt.json").as_posix(),
        "postflight_command": list(plan.get("postflight_command") or []),
        "verification_commands": [
            "./scripts/repoctl upgrade postflight --json",
            "./scripts/repoctl check --json",
            "./scripts/repoctl meta check --json",
        ],
    }


def _upgrade_backup_status(
    root: Path,
    *,
    run_id: str,
    backup: dict[str, Any] | None,
    backups: list[Any],
    check_contents: bool,
) -> dict[str, str]:
    expected_root_rel = (UPGRADE_STATE_REL / run_id / "backup").as_posix()
    if not backups:
        if backup is None:
            return {
                "path": "",
                "recorded_digest": "",
                "current_digest": "",
                "retention_status_at_creation": "",
                "availability": "not_required",
            }
        if (
            backup.get("path") != expected_root_rel
            or backup.get("recorded_digest") != ""
            or backup.get("retention_status_at_creation") != "not_required"
        ):
            raise RepoctlError(
                "upgrade receipt empty-backup metadata is invalid",
                code="upgrade_receipt_invalid",
                path=expected_root_rel,
            )
        return {
            "path": expected_root_rel,
            "recorded_digest": "",
            "current_digest": "",
            "retention_status_at_creation": "not_required",
            "availability": "not_required",
        }

    modern = backup is not None
    recorded_digest = backup.get("recorded_digest") if modern else ""
    if modern and (
        backup.get("path") != expected_root_rel
        or not _is_sha256_digest(recorded_digest)
        or backup.get("retention_status_at_creation") != "manual_retention"
    ):
        raise RepoctlError(
            "upgrade receipt backup metadata is incomplete",
            code="upgrade_receipt_invalid",
            path=expected_root_rel,
        )

    individual_paths: list[Path] = []
    expected_digests: dict[str, str] = {}
    targets: set[str] = set()
    for item in backups:
        if not isinstance(item, dict):
            raise RepoctlError(
                "upgrade backup entry is not an object",
                code="upgrade_receipt_invalid",
                path=expected_root_rel,
            )
        target_value = item.get("path")
        backup_path_value = item.get("backup_path")
        if not isinstance(target_value, str) or not isinstance(backup_path_value, str):
            raise RepoctlError(
                "upgrade backup entry path is invalid",
                code="upgrade_receipt_invalid",
                path=expected_root_rel,
            )
        target_rel = _safe_rel(target_value)
        if target_rel in targets:
            raise RepoctlError(
                "upgrade receipt contains duplicate backup targets",
                code="upgrade_receipt_invalid",
                path=target_rel,
            )
        targets.add(target_rel)
        expected_item_rel = (UPGRADE_STATE_REL / run_id / "backup" / target_rel).as_posix()
        if backup_path_value != expected_item_rel:
            raise RepoctlError(
                "upgrade backup path does not match its run and target",
                code="upgrade_receipt_invalid",
                path=backup_path_value or expected_root_rel,
            )
        backup_path = _assert_contained_path(
            root,
            backup_path_value,
            code="upgrade_receipt_invalid",
        )
        if backup_path.exists() and not backup_path.is_file():
            raise RepoctlError(
                "upgrade backup is not a regular file",
                code="upgrade_receipt_invalid",
                path=backup_path_value,
            )
        if modern:
            item_digest = item.get("backup_digest")
            if not _is_sha256_digest(item_digest):
                raise RepoctlError(
                    "upgrade backup entry digest is invalid",
                    code="upgrade_receipt_invalid",
                    path=backup_path_value,
                )
            expected_digests[backup_path_value] = str(item_digest)
        individual_paths.append(backup_path)

    missing = any(not path.exists() for path in individual_paths)
    if not modern:
        return {
            "path": "",
            "recorded_digest": "",
            "current_digest": "",
            "retention_status_at_creation": "",
            "availability": "missing" if missing else "digest_unavailable",
        }

    if not check_contents:
        return {
            "path": expected_root_rel,
            "recorded_digest": str(recorded_digest),
            "current_digest": "",
            "retention_status_at_creation": "manual_retention",
            "availability": "missing" if missing else "not_checked",
        }

    backup_root = _assert_contained_path(root, expected_root_rel, code="upgrade_receipt_invalid")
    if not backup_root.exists():
        availability = "missing"
        current_digest = ""
    elif not backup_root.is_dir():
        raise RepoctlError(
            "upgrade backup tree is not a directory",
            code="upgrade_receipt_invalid",
            path=expected_root_rel,
        )
    else:
        current_digest, records = _canonical_tree_snapshot(backup_root)
        actual_files = {
            (backup_root.parent / str(record["path"])).relative_to(root).as_posix()
            for record in records
            if record.get("kind") == "file"
        }
        current_digests = {
            (backup_root.parent / str(record["path"])).relative_to(root).as_posix(): str(record["content_sha256"])
            for record in records
            if record.get("kind") == "file"
        }
        expected_files = {item.relative_to(root).as_posix() for item in individual_paths}
        entry_digests_match = all(
            current_digests.get(path) == digest
            for path, digest in expected_digests.items()
        )
        if missing:
            availability = "missing"
        elif entry_digests_match and actual_files == expected_files and current_digest == recorded_digest:
            availability = "available"
        else:
            availability = "digest_mismatch"
    return {
        "path": expected_root_rel,
        "recorded_digest": str(recorded_digest),
        "current_digest": current_digest,
        "retention_status_at_creation": "manual_retention",
        "availability": availability,
    }


def upgrade_status(
    root: Path,
    *,
    _check_backup_contents: bool = True,
) -> tuple[dict[str, Any], list[dict[str, str]]]:
    receipts: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    state_root = root / UPGRADE_STATE_REL
    if state_root.is_dir():
        for path in sorted(state_root.glob("*/receipt.json")):
            rel = path.relative_to(root).as_posix()

            def add_invalid(message: str, problem_path: str = rel) -> None:
                problems.append(
                    {
                        "severity": "error",
                        "code": "upgrade_receipt_invalid",
                        "message": message,
                        "path": problem_path,
                    }
                )

            try:
                receipt_path = _assert_contained_path(
                    root,
                    rel,
                    code="upgrade_receipt_invalid",
                    require_file=True,
                )
            except RepoctlError as exc:
                add_invalid(str(exc), exc.path or rel)
                continue
            try:
                receipt = json.loads(receipt_path.read_text(encoding="utf-8"))
            except (OSError, UnicodeError, json.JSONDecodeError) as exc:
                add_invalid(str(exc))
                continue
            if not isinstance(receipt, dict):
                add_invalid("upgrade receipt is not an object")
                continue
            declared_run_id = receipt.get("run_id")
            raw_backup = receipt.get("backup")
            raw_backups = receipt.get("backups")
            try:
                parsed_run_id = datetime.strptime(declared_run_id, "%Y%m%d%H%M%SZ") if isinstance(declared_run_id, str) else None
            except ValueError:
                parsed_run_id = None
            if (
                parsed_run_id is None
                or parsed_run_id.strftime("%Y%m%d%H%M%SZ") != declared_run_id
                or declared_run_id != path.parent.name
                or (raw_backup is not None and not isinstance(raw_backup, dict))
                or not isinstance(raw_backups, list)
            ):
                add_invalid("upgrade receipt identity or backup shape is invalid")
                continue
            backup = raw_backup if isinstance(raw_backup, dict) else {}
            backups = raw_backups
            try:
                backup_status = _upgrade_backup_status(
                    root,
                    run_id=declared_run_id,
                    backup=raw_backup,
                    backups=backups,
                    check_contents=_check_backup_contents,
                )
            except RepoctlError as exc:
                add_invalid(str(exc), exc.path or rel)
                backup_status = {
                    "path": str(backup.get("path") or ""),
                    "recorded_digest": str(backup.get("recorded_digest") or ""),
                    "current_digest": "",
                    "retention_status_at_creation": str(backup.get("retention_status_at_creation") or ""),
                    "availability": "invalid",
                }
            receipts.append(
                {
                    "run_id": declared_run_id,
                    "receipt_path": rel,
                    "source_content_digest": str(receipt.get("source_content_digest") or ""),
                    "backup": backup_status,
                }
            )
    latest = receipts[-1] if receipts else None
    return {
        "status": "receipts_available" if receipts else "no_upgrade_receipts",
        "receipt_count": len(receipts),
        "latest": latest,
        "receipts": receipts,
    }, problems

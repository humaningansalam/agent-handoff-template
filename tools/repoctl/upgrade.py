from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import shutil
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from .io import RepoctlError, atomic_write, repoctl_lock
from .tasks import TASK_STATE_SCHEMA_VERSION

MANIFEST_REL = Path("repoctl-upgrade-manifest.json")
UPGRADE_STATE_REL = Path("docs/tasks/.repoctl-state/upgrades")


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


def _canonical_tree_digest(path: Path) -> str:
    if not path.exists() and not path.is_symlink():
        return ""
    records = _canonical_entry_records(path, relative_to=path.parent)
    encoded = json.dumps(records, ensure_ascii=False, sort_keys=True, separators=(",", ":")).encode("utf-8")
    return _sha256(_hash_bytes(encoded))


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
    if not isinstance(replace_paths, list) or not all(isinstance(path, str) for path in replace_paths):
        raise RepoctlError("upgrade manifest replace_paths must be a list of strings", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    if not isinstance(create_paths, list) or not all(isinstance(path, str) for path in create_paths):
        raise RepoctlError("upgrade manifest create_paths must be a list of strings", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    if not isinstance(remove_paths, list) or not all(isinstance(path, str) for path in remove_paths):
        raise RepoctlError("upgrade manifest remove_paths must be a list of strings", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    if not isinstance(preserve_paths, list) or not all(isinstance(path, str) for path in preserve_paths):
        raise RepoctlError("upgrade manifest preserve_paths must be a list of strings", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    manifest["replace_paths"] = sorted({_safe_rel(path) for path in replace_paths})
    manifest["create_paths"] = sorted({_safe_rel(path) for path in create_paths})
    manifest["remove_paths"] = sorted({_safe_rel(path) for path in remove_paths})
    manifest["preserve_paths"] = sorted({_safe_rel(path) for path in preserve_paths})
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


def _task_state_text(payload: dict[str, Any]) -> str:
    return json.dumps(payload, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _migrate_task_state_payload(data: dict[str, Any], *, task_id: str, rel: str) -> dict[str, Any] | None:
    if data.get("schema") == "repoctl.task.state" and data.get("schema_version") == TASK_STATE_SCHEMA_VERSION:
        return None
    if data.get("schema_version") != 2 or str(data.get("task_id") or "") != task_id:
        raise RepoctlError("task state schema cannot be migrated without inference", code="task_state_migration_failed", path=rel)

    def convert_record(record: dict[str, Any]) -> dict[str, Any]:
        raw_entries = record.get("dirty_entries", record.get("repo_changes", []))
        raw_fingerprints = record.get("dirty_path_fingerprints")
        if not isinstance(raw_entries, list):
            raise RepoctlError("task state dirty entries are invalid", code="task_state_migration_failed", path=rel)
        if raw_entries and not isinstance(raw_fingerprints, dict):
            raise RepoctlError("task state dirty baseline cannot be migrated without path fingerprints", code="task_state_migration_failed", path=rel)
        path_fingerprints = dict(raw_fingerprints or {})
        if any(not isinstance(path, str) or not isinstance(value, str) or not value.startswith("sha256:") for path, value in path_fingerprints.items()):
            raise RepoctlError("task state path fingerprints are invalid", code="task_state_migration_failed", path=rel)
        start_head = str(record.get("start_head") or record.get("head") or "")
        if not start_head:
            raise RepoctlError("task state has no recorded initial HEAD", code="task_state_migration_failed", path=rel)
        return {
            "repo_id": str(record.get("repo_id") or ""),
            "repo_path": str(record.get("repo_path") or ""),
            "git_toplevel": str(record.get("git_toplevel") or ""),
            "start_head": start_head,
            "dirty_entries": raw_entries,
            "dirty_path_fingerprints": path_fingerprints,
        }

    raw_repositories = data.get("repositories", [])
    if raw_repositories:
        if not isinstance(raw_repositories, list) or not all(isinstance(item, dict) for item in raw_repositories):
            raise RepoctlError("task state repository baselines are invalid", code="task_state_migration_failed", path=rel)
        initial = {"created": str(data.get("created") or ""), "repositories": [convert_record(item) for item in raw_repositories]}
    else:
        initial = {"created": str(data.get("created") or ""), **convert_record(data)}
    ownership = data.get("ownership", {})
    if not isinstance(ownership, dict):
        raise RepoctlError("task state ownership is invalid", code="task_state_migration_failed", path=rel)
    return {
        "schema": "repoctl.task.state",
        "schema_version": TASK_STATE_SCHEMA_VERSION,
        "task_id": task_id,
        "initial": initial,
        "ownership": dict(ownership),
    }


def _plan_task_state_migrations(root: Path) -> tuple[list[dict[str, Any]], list[dict[str, str]]]:
    migrations: list[dict[str, Any]] = []
    conflicts: list[dict[str, str]] = []
    state_dir = root / "docs/tasks/.repoctl-state"
    if not state_dir.is_dir():
        return migrations, conflicts
    for path in sorted(state_dir.glob("T-*.json")):
        rel = path.relative_to(root).as_posix()
        try:
            source_bytes = path.read_bytes()
            data = json.loads(source_bytes.decode("utf-8"))
            if not isinstance(data, dict):
                raise RepoctlError("task state is not an object", code="task_state_migration_failed", path=rel)
            migrated = _migrate_task_state_payload(data, task_id=path.stem, rel=rel)
            if migrated is None:
                continue
            target_bytes = _task_state_text(migrated).encode("utf-8")
            migrations.append(
                {
                    "path": rel,
                    "action": "migrate_task_state",
                    "source_hash": _hash_bytes(source_bytes),
                    "target_hash": _hash_bytes(target_bytes),
                    "schema_from": int(data.get("schema_version") or 0),
                    "schema_to": TASK_STATE_SCHEMA_VERSION,
                }
            )
        except (OSError, UnicodeDecodeError, json.JSONDecodeError, RepoctlError) as exc:
            conflicts.append({"code": "task_state_migration_failed", "path": rel, "message": str(exc)})
    return migrations, conflicts


def _plan_payload(
    root: Path,
    source_root: Path,
    manifest: dict[str, Any],
    operations: list[UpgradeOperation],
    state_migrations: list[dict[str, Any]],
    conflicts: list[dict[str, str]],
) -> dict[str, Any]:
    source_paths = [*manifest["replace_paths"], *manifest["create_paths"], *_preserve_seed_paths(source_root, manifest)]
    data = {
        "schema_version": 1,
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
        "operations": [operation.to_dict() for operation in operations],
        "state_migrations": state_migrations,
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
    state_migrations, migration_conflicts = _plan_task_state_migrations(root)
    conflicts.extend(migration_conflicts)
    return _plan_payload(root, source_root, manifest, operations, state_migrations, conflicts)


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
    if not isinstance(payload, dict) or not isinstance(payload.get("operations"), list) or not isinstance(payload.get("state_migrations", []), list):
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


def _verify_plan_bound_to_source(root: Path, source_root: Path, plan: dict[str, Any]) -> None:
    manifest = _load_manifest(source_root)
    if str(plan.get("package") or "") != str(manifest.get("package") or "agent-workspace-control-plane"):
        raise RepoctlError("upgrade plan package does not match source manifest", code="invalid_upgrade_plan")
    if str(plan.get("source_version") or "") != str(manifest.get("version") or ""):
        raise RepoctlError("upgrade plan version does not match source manifest", code="invalid_upgrade_plan")
    for key in ("replace_paths", "create_paths", "remove_paths", "preserve_paths"):
        if sorted(plan.get(key) or []) != manifest[key]:
            raise RepoctlError(f"upgrade plan {key} does not match source manifest", code="invalid_upgrade_plan")
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
    if str(plan.get("source_content_digest") or "") != str(expected.get("source_content_digest") or ""):
        raise RepoctlError("upgrade plan source digest does not match current source", code="upgrade_plan_stale")
    if plan.get("state_migrations", []) != expected.get("state_migrations", []):
        raise RepoctlError("upgrade plan task state migrations do not match current workspace state", code="upgrade_plan_stale")


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
    for migration in plan.get("state_migrations", []):
        rel = _safe_rel(str(migration.get("path", "")))
        target = _assert_contained_path(root, rel, code="invalid_upgrade_target", require_file=True)
        current = _hash_file(target)
        if current != str(migration.get("source_hash") or ""):
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
                "backup_digest": _sha256(_hash_file(backup_path)),
            }
        )

    with repoctl_lock(root):
        _verify_plan_fresh(root, plan)
        _verify_plan_bound_to_source(root, source_root, plan)
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
            for migration in plan.get("state_migrations", []):
                rel = _safe_rel(str(migration.get("path", "")))
                target_path = _assert_contained_path(root, rel, code="invalid_upgrade_target", require_file=True)
                try:
                    data = json.loads(target_path.read_text(encoding="utf-8"))
                except (OSError, json.JSONDecodeError) as exc:
                    raise RepoctlError("task state changed after upgrade plan", code="upgrade_plan_stale", path=rel) from exc
                if not isinstance(data, dict):
                    raise RepoctlError("task state changed after upgrade plan", code="upgrade_plan_stale", path=rel)
                migrated = _migrate_task_state_payload(data, task_id=target_path.stem, rel=rel)
                if migrated is None:
                    raise RepoctlError("task state no longer requires the planned migration", code="upgrade_plan_stale", path=rel)
                migrated_text = _task_state_text(migrated)
                if _hash_bytes(migrated_text.encode("utf-8")) != str(migration.get("target_hash") or ""):
                    raise RepoctlError("task state migration output changed after upgrade plan", code="upgrade_plan_stale", path=rel)
                backup_target(rel, target_path)
                atomic_write(target_path, migrated_text)
                applied.append({"path": rel, "action": "migrate_task_state"})
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
        backup = {
            "path": backup_root.relative_to(root).as_posix(),
            "recorded_digest": _canonical_tree_digest(backup_root) if backups else "",
            "retention_status_at_creation": "manual_retention" if backups else "not_required",
        }
        receipt = {
            "run_id": run_id,
            "plan_file": plan_path.as_posix(),
            "plan_sha256": plan.get("plan_sha256", ""),
            "source_content_digest": str(plan.get("source_content_digest") or ""),
            "applied": applied,
            "backups": backups,
            "backup": backup,
        }
        receipt_path = root / UPGRADE_STATE_REL / run_id / "receipt.json"
        atomic_write(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return {
        "run_id": run_id,
        "applied": applied,
        "backups": backups,
        "backup": backup,
        "receipt_path": (UPGRADE_STATE_REL / run_id / "receipt.json").as_posix(),
        "verification_commands": [
            "./scripts/repoctl check --json",
            "./scripts/repoctl meta check --json",
        ],
    }


def upgrade_status(root: Path) -> tuple[dict[str, Any], list[dict[str, str]]]:
    receipts: list[dict[str, Any]] = []
    problems: list[dict[str, str]] = []
    state_root = root / UPGRADE_STATE_REL
    if state_root.is_dir():
        for path in sorted(state_root.glob("*/receipt.json")):
            rel = path.relative_to(root).as_posix()
            try:
                receipt = json.loads(path.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError) as exc:
                problems.append({"severity": "error", "code": "upgrade_receipt_invalid", "message": str(exc), "path": rel})
                continue
            if not isinstance(receipt, dict):
                problems.append({"severity": "error", "code": "upgrade_receipt_invalid", "message": "upgrade receipt is not an object", "path": rel})
                continue
            backup = receipt.get("backup") if isinstance(receipt.get("backup"), dict) else {}
            backups = receipt.get("backups") if isinstance(receipt.get("backups"), list) else []
            backup_rel = str(backup.get("path") or "")
            recorded_digest = str(backup.get("recorded_digest") or "")
            retention_status = str(backup.get("retention_status_at_creation") or "")
            availability = "not_required"
            current_digest = ""
            if backups:
                try:
                    backup_path = _assert_contained_path(root, backup_rel, code="upgrade_receipt_invalid")
                    if not backup_path.exists():
                        availability = "missing"
                    else:
                        current_digest = _canonical_tree_digest(backup_path)
                        availability = "available" if recorded_digest and current_digest == recorded_digest else "digest_mismatch"
                except RepoctlError as exc:
                    problems.append({"severity": "error", "code": exc.code, "message": str(exc), "path": exc.path or rel})
                    availability = "invalid"
            receipts.append(
                {
                    "run_id": str(receipt.get("run_id") or path.parent.name),
                    "receipt_path": rel,
                    "source_content_digest": str(receipt.get("source_content_digest") or ""),
                    "backup": {
                        "path": backup_rel,
                        "recorded_digest": recorded_digest,
                        "current_digest": current_digest,
                        "retention_status_at_creation": retention_status,
                        "availability": availability,
                    },
                }
            )
    latest = receipts[-1] if receipts else None
    return {
        "status": "receipts_available" if receipts else "no_upgrade_receipts",
        "receipt_count": len(receipts),
        "latest": latest,
        "receipts": receipts,
    }, problems

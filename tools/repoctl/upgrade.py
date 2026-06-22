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
    preserve_paths = manifest.get("preserve_paths")
    if not isinstance(replace_paths, list) or not all(isinstance(path, str) for path in replace_paths):
        raise RepoctlError("upgrade manifest replace_paths must be a list of strings", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    if not isinstance(create_paths, list) or not all(isinstance(path, str) for path in create_paths):
        raise RepoctlError("upgrade manifest create_paths must be a list of strings", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    if not isinstance(preserve_paths, list) or not all(isinstance(path, str) for path in preserve_paths):
        raise RepoctlError("upgrade manifest preserve_paths must be a list of strings", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    manifest["replace_paths"] = sorted({_safe_rel(path) for path in replace_paths})
    manifest["create_paths"] = sorted({_safe_rel(path) for path in create_paths})
    manifest["preserve_paths"] = sorted({_safe_rel(path) for path in preserve_paths})
    managed = [*manifest["replace_paths"], *manifest["create_paths"]]
    if len(set(managed)) != len(managed):
        raise RepoctlError("upgrade manifest paths cannot appear in both replace_paths and create_paths", code="invalid_upgrade_manifest", path=MANIFEST_REL.as_posix())
    for path in managed:
        if _is_preserved(path, manifest["preserve_paths"]):
            raise RepoctlError(f"upgrade path is both managed and preserved: {path}", code="invalid_upgrade_manifest", path=path)
    return manifest


def _is_preserved(path: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(path, pattern) for pattern in patterns)


def _source_root(source: str | Path) -> Path:
    root = Path(source).expanduser().resolve()
    if not root.is_dir():
        raise RepoctlError(f"upgrade source must be a directory: {source}", code="invalid_upgrade_source", path=str(source))
    return root


def _plan_payload(root: Path, source_root: Path, manifest: dict[str, Any], operations: list[UpgradeOperation], conflicts: list[dict[str, str]]) -> dict[str, Any]:
    data = {
        "schema_version": 1,
        "package": manifest.get("package", "agent-workspace-control-plane"),
        "source_version": str(manifest.get("version", "")),
        "source_root": source_root.as_posix(),
        "workspace_root": root.as_posix(),
        "manifest_path": MANIFEST_REL.as_posix(),
        "replace_paths": manifest["replace_paths"],
        "create_paths": manifest["create_paths"],
        "preserve_paths": manifest["preserve_paths"],
        "operations": [operation.to_dict() for operation in operations],
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
    return _plan_payload(root, source_root, manifest, operations, conflicts)


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


def _rollback_applied(root: Path, applied: list[dict[str, str]], backups: list[dict[str, str]]) -> list[dict[str, str]]:
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
    if not isinstance(payload, dict) or not isinstance(payload.get("operations"), list):
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
    for key in ("replace_paths", "create_paths", "preserve_paths"):
        if sorted(plan.get(key) or []) != manifest[key]:
            raise RepoctlError(f"upgrade plan {key} does not match source manifest", code="invalid_upgrade_plan")
    managed = set(manifest["replace_paths"]) | set(manifest["create_paths"])
    preserved = manifest["preserve_paths"]
    for operation in plan["operations"]:
        rel = _safe_rel(str(operation.get("path", "")))
        if rel not in managed:
            raise RepoctlError(f"upgrade plan contains unmanaged path: {rel}", code="invalid_upgrade_plan", path=rel)
        if _is_preserved(rel, preserved):
            raise RepoctlError(f"upgrade plan attempts to modify preserved path: {rel}", code="invalid_upgrade_plan", path=rel)
    expected = plan_upgrade(root, source=source_root)
    if expected.get("conflicts"):
        raise RepoctlError("upgrade source has conflicts; recreate the plan", code="upgrade_plan_stale")
    if _operation_dicts(plan) != _operation_dicts(expected):
        raise RepoctlError("upgrade plan operations do not match current source manifest and workspace state", code="upgrade_plan_stale")


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
    backups: list[dict[str, str]] = []
    backup_root = root / UPGRADE_STATE_REL / run_id / "backup"
    with repoctl_lock(root):
        _verify_plan_fresh(root, plan)
        _verify_plan_bound_to_source(root, source_root, plan)
        try:
            for operation in plan["operations"]:
                rel = _safe_rel(str(operation["path"]))
                source_path = _assert_contained_path(source_root, rel, code="invalid_upgrade_source", require_file=True)
                target_path = _assert_contained_path(root, rel, code="invalid_upgrade_target")
                if not source_path.is_file():
                    raise RepoctlError(f"managed source file disappeared: {rel}", code="managed_source_missing", path=rel)
                source_hash = _hash_file(source_path)
                if source_hash != operation.get("source_hash"):
                    raise RepoctlError(f"managed source changed after plan: {rel}", code="upgrade_plan_stale", path=rel)
                if target_path.is_file():
                    backup_path = backup_root / rel
                    _assert_contained_path(root, backup_path.relative_to(root).as_posix(), code="invalid_upgrade_target")
                    backup_path.parent.mkdir(parents=True, exist_ok=True)
                    shutil.copy2(target_path, backup_path)
                    backups.append({"path": rel, "backup_path": backup_path.relative_to(root).as_posix()})
                _atomic_copy_file(source_path, target_path)
                applied.append({"path": rel, "action": str(operation.get("action") or "replace")})
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
        receipt = {
            "run_id": run_id,
            "plan_file": plan_path.as_posix(),
            "plan_sha256": plan.get("plan_sha256", ""),
            "applied": applied,
            "backups": backups,
        }
        receipt_path = root / UPGRADE_STATE_REL / run_id / "receipt.json"
        atomic_write(receipt_path, json.dumps(receipt, ensure_ascii=False, indent=2) + "\n")
    return {
        "run_id": run_id,
        "applied": applied,
        "backups": backups,
        "receipt_path": (UPGRADE_STATE_REL / run_id / "receipt.json").as_posix(),
        "verification_commands": [
            "UV_CACHE_DIR=/tmp/uv-cache uv run pytest -q tests/repoctl tests/maintenance",
            "./scripts/repoctl check --json",
            "./scripts/repoctl meta check --json",
        ],
    }

"""One-time Task presentation migration with immutable completion evidence.

The finished Task's old bytes remain an auditable completion-time witness in
ignored state; the receipt itself and append-only completion catalogue are
not rewritten or relabeled as proof of the later text cleanup.
"""
from __future__ import annotations

import hashlib
import json
import re
from dataclasses import dataclass
from pathlib import Path
from typing import Any

SCHEMA = "repoctl.task.history-presentation"
SCHEMA_VERSION = 1
TASK_ID = re.compile(r"T-[0-9]{14}Z\Z")
DIGEST = re.compile(r"sha256:[a-f0-9]{64}\Z")


def digest_bytes(value: bytes) -> str:
    return "sha256:" + hashlib.sha256(value).hexdigest()


def proof_paths(root: Path, task_id: str) -> tuple[Path, Path]:
    if TASK_ID.fullmatch(task_id) is None:
        raise ValueError("invalid Task ID for history migration")
    base = root / "docs/tasks/.repoctl-state/history-migrations" / task_id
    return base / "original.md", base / "manifest.json"


def _regular(path: Path, root: Path) -> bool:
    try:
        base = root.resolve(strict=True)
        resolved = path.resolve(strict=True)
        if resolved != base and base not in resolved.parents:
            return False
        node = root
        for part in path.relative_to(root).parts:
            node /= part
            if node.is_symlink():
                return False
        return path.is_file()
    except (OSError, ValueError, RuntimeError):
        return False


@dataclass(frozen=True)
class HistoryWitness:
    status: str
    path: Path | None = None
    problem: str = ""


def receipt_witness(root: Path, task_id: str, receipt_path: str = "") -> HistoryWitness:
    """Only a matching, unaltered migration manifest can redirect an audit.

    It does not redirect ordinary Task reads: they use the cleaned, current
    Markdown. Failed provenance verification cannot silently bless a receipt.
    """
    try:
        original_path, manifest_path = proof_paths(root, task_id)
    except ValueError:
        return HistoryWitness("absent")
    if not manifest_path.exists() and not manifest_path.is_symlink():
        return HistoryWitness("absent")
    if not _regular(manifest_path, root) or not _regular(original_path, root):
        return HistoryWitness("invalid", problem="migration witness file is missing or unsafe")
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or set(manifest) != {
            "schema", "schema_version", "task_id", "task_path", "original_sha256",
            "migrated_sha256", "receipt_sha256", "original_witness_path", "kind",
        }:
            raise ValueError("invalid migration provenance contract")
        task_path = str(manifest["task_path"])
        if (manifest["schema"] != SCHEMA or manifest["schema_version"] != SCHEMA_VERSION
            or manifest["task_id"] != task_id
            or not (task_path.startswith("docs/tasks/") or task_path.startswith("docs/archive/tasks/"))
            or Path(task_path).name.split("--",1)[0] != task_id
            or manifest["original_witness_path"] != original_path.relative_to(root).as_posix()
            or not all(isinstance(manifest[x],str) and DIGEST.fullmatch(manifest[x]) for x in ("original_sha256","migrated_sha256"))
            or manifest["kind"] not in {"completed", "canceled"}):
            raise ValueError("migration identity or hashes are invalid")
        current_path = root / task_path
        if not _regular(current_path, root):
            raise ValueError("migrated Task is missing or unsafe")
        if digest_bytes(current_path.read_bytes()) != manifest["migrated_sha256"]:
            raise ValueError("Task changed after the historical migration")
        if digest_bytes(original_path.read_bytes()) != manifest["original_sha256"]:
            raise ValueError("original completion-time witness changed")
        claimed_receipt_sha = str(manifest["receipt_sha256"])
        if receipt_path:
            receipt = root / receipt_path
            if not _regular(receipt, root) or digest_bytes(receipt.read_bytes()) != claimed_receipt_sha:
                raise ValueError("completion receipt no longer matches the historical witness")
        elif claimed_receipt_sha:
            raise ValueError("receipt reference is required for a completed migration")
        return HistoryWitness("valid", path=original_path)
    except (OSError, UnicodeError, TypeError, KeyError, ValueError, json.JSONDecodeError) as exc:
        return HistoryWitness("invalid", problem=str(exc))


def prepare_history_witness(
    root: Path, *, task_id: str, task_path: Path, original: bytes,
    migrated: str, status: str, receipt: Path | None,
) -> list[tuple[Path, bytes]]:
    """Prepare evidence-only writes; caller owns lock, collision and rollback."""
    if status not in {"done", "canceled"}:
        raise ValueError("only terminal Task status may have history witness")
    original_path, manifest_path = proof_paths(root, task_id)
    if original_path.exists() or manifest_path.exists() or original_path.is_symlink() or manifest_path.is_symlink():
        raise ValueError("historical migration proof already exists")
    rel_task = task_path.relative_to(root).as_posix()
    if not (rel_task.startswith("docs/tasks/") or rel_task.startswith("docs/archive/tasks/")) or Path(rel_task).name.split("--",1)[0] != task_id:
        raise ValueError("Task source location is invalid for closed migration")
    receipt_sha = digest_bytes(receipt.read_bytes()) if receipt is not None else ""
    manifest: dict[str, Any] = {
        "schema": SCHEMA,
        "schema_version": SCHEMA_VERSION,
        "task_id": task_id,
        "task_path": rel_task,
        "original_sha256": digest_bytes(original),
        "migrated_sha256": digest_bytes(migrated.encode("utf-8")),
        "original_witness_path": original_path.relative_to(root).as_posix(),
        "receipt_sha256": receipt_sha,
        "kind": "completed" if status == "done" else "canceled",
    }
    return [(original_path, original), (manifest_path, (json.dumps(manifest,ensure_ascii=False,sort_keys=True,indent=2)+'\n').encode('utf-8'))]

"""Explicit one-time Knowledge v1 migration with immutable original provenance.

Only current v2 records enter query/Graph. An historical v1 file which is
referenced elsewhere must retain its exact original path and bytes. Instead
of rewriting/removing it, an explicit, digest-verified proof connects that
historical source to an equivalent or newly saved v2 record.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .io import RepoctlError, atomic_write, repoctl_lock
from .knowledge_records import _record_dir, prepare_knowledge_record
from .knowledge_projection import KNOWLEDGE_RECORD_SCHEMA_VERSION, rebuild_knowledge_projection

_ID = re.compile(r"K-[0-9]{14}Z--[a-z0-9]+(?:-[a-z0-9]+)*\Z")
_PROOF_SCHEMA = "repoctl.knowledge.one-time-preservation"
_PROOF_VERSION = 1


def _sha(raw: bytes) -> str:
    return "sha256:" + hashlib.sha256(raw).hexdigest()


def _source_paths(raw: dict[str, Any]) -> list[str]:
    refs = raw.get("source_refs")
    if isinstance(refs, list):
        paths = [str(item.get("path") or "") for item in refs if isinstance(item, dict)]
        if paths:
            return sorted({path for path in paths if path})
    if isinstance(raw.get("sources"), list):
        return sorted({item for item in raw["sources"] if isinstance(item, str) and item})
    return [raw["source"]] if isinstance(raw.get("source"), str) and raw["source"] else []


def _proof_paths(root: Path, legacy_id: str) -> tuple[Path, Path]:
    if _ID.fullmatch(legacy_id) is None:
        raise ValueError("invalid legacy record id")
    base = root / "docs/tasks/.repoctl-state/knowledge-migrations" / legacy_id
    return base / "original.json", base / "migration.json"


def _regular_path_under(root: Path, path: Path) -> bool:
    try:
        if root.resolve(strict=True) not in (path.resolve(strict=True), *path.resolve(strict=True).parents):
            return False
        node = root
        for component in path.relative_to(root).parts:
            node /= component
            if node.is_symlink():
                return False
        return path.is_file()
    except (OSError, RuntimeError, ValueError):
        return False


def preserved_legacy_migration_witness(root: Path, legacy: dict[str, Any]) -> bool:
    """Verify the original v1 and current v2 bytes against an explicit proof.

    A missing, corrupt or tampered proof is never an implicit migration. This
    function is used by the normal Knowledge status/check to avoid repeatedly
    treating deliberately preserved provenance as a pending v1 conversion.
    """
    legacy_id = str(legacy.get("id") or "")
    try:
        old_path = _record_dir(root) / f"{legacy_id}.json"
        before_path, manifest_path = _proof_paths(root, legacy_id)
        if not all(_regular_path_under(root, path) for path in (old_path, before_path, manifest_path)):
            return False
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if not isinstance(manifest, dict) or set(manifest) != {
            "schema", "schema_version", "legacy_id", "repo_id", "destination_id",
            "legacy_sha256", "destination_sha256", "original_witness_path", "kind",
        }:
            return False
        destination_id = manifest["destination_id"]
        if (
            manifest["schema"] != _PROOF_SCHEMA
            or type(manifest["schema_version"]) is not int
            or manifest["schema_version"] != _PROOF_VERSION
            or manifest["legacy_id"] != legacy_id
            or str(manifest["repo_id"]) != str(legacy.get("repo_id") or "")
            or not isinstance(destination_id, str)
            or _ID.fullmatch(destination_id) is None
            or destination_id == legacy_id
            or manifest["original_witness_path"] != before_path.relative_to(root).as_posix()
            or manifest["kind"] not in {"reuse_existing_v2", "create_v2"}
        ):
            return False
        destination = _record_dir(root) / f"{destination_id}.json"
        if not _regular_path_under(root, destination):
            return False
        if _sha(old_path.read_bytes()) != manifest["legacy_sha256"]:
            return False
        if _sha(before_path.read_bytes()) != manifest["legacy_sha256"]:
            return False
        if _sha(destination.read_bytes()) != manifest["destination_sha256"]:
            return False
        actual_v2 = json.loads(destination.read_text(encoding="utf-8"))
        return (
            isinstance(actual_v2, dict)
            and actual_v2.get("schema") == "repoctl.knowledge.record"
            and actual_v2.get("schema_version") == KNOWLEDGE_RECORD_SCHEMA_VERSION
            and actual_v2.get("id") == destination_id
            and str(actual_v2.get("repo_id") or "") == str(legacy.get("repo_id") or "")
        )
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        return False



def verified_preserved_legacy_ids(root: Path, *, repo_id: str, ids: set[str]) -> set[str]:
    """Historical v1 IDs may be replacement references only with valid proofs.

    A v1 original is never inserted as a query/projection head. Its historical
    identity is referenced only from an intact v2 migration's checked witness.
    """
    result: set[str] = set()
    for record_id in ids:
        if _ID.fullmatch(record_id) is None:
            continue
        path = _record_dir(root) / f"{record_id}.json"
        if not _regular_path_under(root, path):
            continue
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError):
            continue
        if (
            isinstance(record, dict)
            and record.get("schema") == "repoctl.knowledge.record"
            and record.get("schema_version") == 1
            and record.get("id") == record_id
            and str(record.get("repo_id") or "") == repo_id
            and preserved_legacy_migration_witness(root, record)
        ):
            result.add(record_id)
    return result


def _record_identity(record: dict[str, Any]) -> tuple[Any, ...]:
    return (
        record.get("repo_id"), record.get("kind"),
        " ".join(str(record.get("claim") or "").split()).casefold(),
        " ".join(str(record.get("reason") or "").split()).casefold(),
        tuple(sorted((ref.get("path"), ref.get("content_sha256")) for ref in record.get("source_refs", []) if isinstance(ref, dict))),
    )


def migrate_legacy_knowledge(
    root: Path, *, repo_id: str, record_id: str = "", apply: bool = False,
    reason_override: str = "", sources_override: list[str] | None = None,
) -> dict[str, Any]:
    """Inspect all legacy v1 sources or migrate one ID, without touching v1 bytes."""
    if apply and not record_id:
        raise RepoctlError("apply exactly one --record-id", code="knowledge_migration_requires_record_id")
    if (reason_override or sources_override) and not record_id:
        raise RepoctlError("reason/source overrides need one --record-id", code="knowledge_migration_record_id_required")
    if record_id and _ID.fullmatch(record_id) is None:
        raise RepoctlError("invalid Knowledge record ID", code="knowledge_migration_id_invalid")

    rows: list[dict[str, Any]] = []
    committed = 0
    with repoctl_lock(root) if apply else nullcontext():
        directory = _record_dir(root)
        paths = sorted(directory.glob("K-*.json"))
        if record_id and not (directory / f"{record_id}.json").is_file():
            raise RepoctlError("Knowledge record not found", code="knowledge_migration_record_missing")
        current: list[dict[str, Any]] = []
        legacy: list[tuple[Path, bytes, dict[str, Any]]] = []
        for path in paths:
            if not _regular_path_under(root, path):
                raise RepoctlError("unsafe Knowledge file", code="knowledge_migration_path_unsafe", path=str(path))
            try:
                raw = path.read_bytes()
                record = json.loads(raw.decode("utf-8"))
            except (OSError, ValueError, UnicodeError) as exc:
                raise RepoctlError("Knowledge JSON invalid", code="knowledge_migration_invalid_json", path=str(path)) from exc
            if not isinstance(record, dict):
                raise RepoctlError("Knowledge source must be an object", code="knowledge_migration_invalid_record", path=str(path))
            if record.get("schema_version") == KNOWLEDGE_RECORD_SCHEMA_VERSION:
                current.append(record)
            elif (
                record.get("schema_version") == 1
                and str(record.get("repo_id") or "") == repo_id
                and (not record_id or record.get("id") == record_id)
            ):
                if record.get("schema") != "repoctl.knowledge.record":
                    raise RepoctlError("legacy source schema is invalid", code="knowledge_migration_invalid_schema", path=str(path))
                legacy.append((path, raw, record))
        if record_id and not legacy:
            raise RepoctlError("selected record is not v1 Knowledge", code="knowledge_migration_not_legacy")

        for path, raw, record in legacy:
            legacy_id = str(record.get("id") or "")
            if _ID.fullmatch(legacy_id) is None or path.stem != legacy_id:
                raise RepoctlError("v1 identity does not match its filename", code="knowledge_migration_id_mismatch", path=str(path))
            row: dict[str, Any] = {
                "id": legacy_id, "path": path.relative_to(root).as_posix(),
                "original_sha256": _sha(raw),
            }
            if preserved_legacy_migration_witness(root, record):
                row["status"] = "already_preserved"
                rows.append(row)
                continue
            before_path, manifest_path = _proof_paths(root, legacy_id)
            if before_path.exists() or before_path.is_symlink() or manifest_path.exists() or manifest_path.is_symlink():
                row.update(status="needs_proof_review", explanation="existing Knowledge migration witness is invalid or incomplete")
                rows.append(row)
                continue
            reason = reason_override.strip() or str(record.get("reason") or record.get("summary") or "").strip()
            sources = list(sources_override or _source_paths(record))
            if not reason:
                row.update(status="needs_reason", explanation="legacy record has no technical reason; supply --reason for this ID")
                rows.append(row)
                continue
            if not sources:
                row.update(status="needs_source", explanation="legacy record has no source identity; supply --source for this ID")
                rows.append(row)
                continue
            prepared, problems = prepare_knowledge_record(
                root, repo_id=repo_id, kind=str(record.get("kind") or ""),
                claim=str(record.get("claim") or ""), reason=reason, sources=sources,
                applies_to=(record.get("applies_to", {}).get("paths") if isinstance(record.get("applies_to"), dict) else []),
            )
            if prepared is None or problems:
                row.update(status="needs_source_review", explanation="; ".join(p.message for p in problems[:3]))
                rows.append(row)
                continue
            historical_digests = {
                str(ref.get("path")): str(ref.get("content_sha256"))
                for ref in record.get("source_refs", []) if isinstance(ref, dict) and ref.get("path") and ref.get("content_sha256")
            }
            new_digests = {
                str(ref.get("path")): str(ref.get("content_sha256"))
                for ref in prepared.data.get("source_refs", []) if isinstance(ref, dict)
            }
            mismatch = [path for path, sha in historical_digests.items() if new_digests.get(path) != sha]
            if mismatch and not sources_override:
                row.update(status="needs_source_review", explanation="original source digest differs: " + ", ".join(mismatch[:4]))
                rows.append(row)
                continue
            equivalents = [candidate for candidate in current if _record_identity(candidate) == _record_identity(prepared.data)]
            if len(equivalents) > 1:
                row.update(status="needs_equivalence_review", explanation="multiple current v2 records match the same claim/reason/provenance")
                rows.append(row)
                continue
            reuse = bool(equivalents)
            destination = equivalents[0] if reuse else prepared.data
            destination_path = directory / f"{destination['id']}.json"
            destination_raw = destination_path.read_bytes() if reuse else prepared.text.encode("utf-8")
            if not reuse and (destination_path.exists() or destination_path.is_symlink()):
                raise RepoctlError("v2 destination appeared concurrently", code="knowledge_migration_destination_collision", path=str(destination_path))
            row.update(
                status="reuse_existing_v2" if reuse else "create_v2",
                destination_id=destination["id"],
                destination_path=destination_path.relative_to(root).as_posix(),
                legacy_source_unchanged=True,
            )
            if apply:
                if path.read_bytes() != raw:
                    raise RepoctlError("v1 Knowledge changed since inspection", code="knowledge_migration_concurrent_edit", path=str(path))
                if reuse and destination_path.read_bytes() != destination_raw:
                    raise RepoctlError("v2 Knowledge changed since inspection", code="knowledge_migration_concurrent_edit", path=str(destination_path))
                proof = {
                    "schema": _PROOF_SCHEMA, "schema_version": _PROOF_VERSION,
                    "legacy_id": legacy_id, "repo_id": repo_id,
                    "destination_id": destination["id"],
                    "legacy_sha256": _sha(raw), "destination_sha256": _sha(destination_raw),
                    "original_witness_path": before_path.relative_to(root).as_posix(),
                    "kind": row["status"],
                }
                written: list[Path] = []
                try:
                    atomic_write(before_path, raw.decode("utf-8"))
                    written.append(before_path)
                    if not reuse:
                        atomic_write(destination_path, prepared.text)
                        written.append(destination_path)
                    # Publish proof last: unfinished source-only writes are
                    # not allowed to suppress a future legacy migration warning.
                    atomic_write(manifest_path, json.dumps(proof, indent=2, sort_keys=True) + "\n")
                    written.append(manifest_path)
                    if not preserved_legacy_migration_witness(root, record):
                        raise RepoctlError("cannot verify published Knowledge migration", code="knowledge_migration_proof_invalid")
                except Exception:
                    for item in reversed(written):
                        if item.exists() and not item.is_symlink():
                            item.unlink()
                    raise
                committed += 1
                row["status"] = "applied_" + row["status"]
            rows.append(row)
    projection_errors: list[str] = []
    if apply and committed:
        _, problems = rebuild_knowledge_projection(root, repo_id=repo_id)
        projection_errors = [item.code for item in problems]
    return {
        "mode": "apply" if apply else "dry_run", "record_count": len(rows),
        "applied_count": committed, "records": rows,
        "projection_problems": projection_errors,
    }

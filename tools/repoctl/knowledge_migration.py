"""Explicit one-time Knowledge v1 conversion preserving original evidence.

Staging verifies the current v2 successor, then finalization moves unmodified
v1 bytes outside active storage and retargets exact historical citations.
"""
from __future__ import annotations

import hashlib
import json
import re
from contextlib import nullcontext
from pathlib import Path
from typing import Any

from .io import RepoctlError, atomic_write, repoctl_lock
from .knowledge_records import (
    _record_dir, prepare_knowledge_record, _knowledge_record_digest,
    _knowledge_record_contract_problems,
)
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


def _assert_safe_proof_path(root: Path, path: Path) -> None:
    """Never create a supposedly ignored witness through a symlink parent."""
    try:
        components = path.relative_to(root).parts
        ancestor = root
        for part in components:
            ancestor = ancestor / part
            if ancestor.is_symlink():
                raise RepoctlError(
                    "Knowledge migration proof path contains a symlink",
                    code="knowledge_migration_proof_path_unsafe", path=str(ancestor),
                )
    except ValueError as exc:
        raise RepoctlError(
            "Knowledge migration proof path escapes workspace",
            code="knowledge_migration_proof_path_unsafe", path=str(path),
        ) from exc


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
    """Validate the explicit *staging-only* v1/v2 conversion witness."""
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
            or manifest["kind"] not in {"reuse_existing_v2", "create_v2", "link_cited_current_v2"}
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
        valid = (
            isinstance(actual_v2, dict)
            and actual_v2.get("schema") == "repoctl.knowledge.record"
            and actual_v2.get("schema_version") == KNOWLEDGE_RECORD_SCHEMA_VERSION
            and actual_v2.get("id") == destination_id
            and str(actual_v2.get("repo_id") or "") == str(legacy.get("repo_id") or "")
        )
        if not valid:
            return False
        if manifest["kind"] == "link_cited_current_v2":
            return _is_verified_cited_successor(
                actual_v2, legacy, root=root, original_sha256=manifest["legacy_sha256"],
            )
        return True
    except (OSError, UnicodeError, json.JSONDecodeError, TypeError, ValueError, KeyError):
        return False



def _scope_paths(record: dict[str, Any]) -> list[str]:
    applies_to = record.get("applies_to")
    if not isinstance(applies_to, dict):
        return []
    raw = applies_to.get("paths")
    return sorted({path for path in raw if isinstance(path, str) and path}) if isinstance(raw, list) else []


def _is_verified_cited_successor(
    current: dict[str, Any], legacy: dict[str, Any], *, root: Path,
    original_sha256: str,
) -> bool:
    """An existing current record must explicitly cite this exact old JSON.

    Claim/kind/repo identity and a cryptographic citation are all required.
    Changed earlier Task sources are *not* rebound; their provenance remains
    on the immutable v1 file for separate review. Existing v2 reason is used
    only if it already exists, not invented by this converter.
    """
    if (
        current.get("schema") != "repoctl.knowledge.record"
        or current.get("schema_version") != KNOWLEDGE_RECORD_SCHEMA_VERSION
        or current.get("repo_id") != legacy.get("repo_id")
        or current.get("kind") != legacy.get("kind")
        or " ".join(str(current.get("claim") or "").split()).casefold()
        != " ".join(str(legacy.get("claim") or "").split()).casefold()
        or not str(current.get("reason") or "").strip()
    ):
        return False
    if _knowledge_record_contract_problems(
        current, record_id=str(current.get("id") or ""),
        repo_id=str(legacy.get("repo_id") or ""),
    ):
        return False
    # A current successor may intentionally retire a historical file. Its
    # current applicability must not silently expand the v1 invariant's
    # scope, however: expansion requires an independently grounded review.
    legacy_scope = set(_scope_paths(legacy))
    current_scope = set(_scope_paths(current))
    if not current_scope.issubset(legacy_scope):
        return False
    legacy_path = f"docs/knowledge/records/{legacy.get('id')}.json"
    return any(
        isinstance(ref, dict)
        and ref.get("path") == legacy_path
        and ref.get("content_sha256") == original_sha256
        for ref in current.get("source_refs", [])
    )


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
            _assert_safe_proof_path(root, before_path)
            _assert_safe_proof_path(root, manifest_path)
            if before_path.exists() or before_path.is_symlink() or manifest_path.exists() or manifest_path.is_symlink():
                row.update(status="needs_proof_review", explanation="existing Knowledge migration witness is invalid or incomplete")
                rows.append(row)
                continue
            # A trustworthy v2 successor can already exist and cite the
            # original v1 as exact SHA-256 evidence. In that case, a stale
            # historical Task or vanished applicability path is NOT a reason
            # to re-hash original sources or fabricate another current claim.
            # The proof explicitly records only the verified existing link.
            cited_successors = [
                candidate for candidate in current
                if not reason_override and not sources_override
                and _is_verified_cited_successor(
                    candidate, record, root=root, original_sha256=_sha(raw),
                )
            ]
            if len(cited_successors) > 1:
                row.update(
                    status="needs_equivalence_review",
                    explanation="multiple current records cite this exact v1 claim; review successor identity",
                )
                rows.append(row)
                continue
            if len(cited_successors) == 1:
                successor = cited_successors[0]
                destination_path = directory / f"{successor['id']}.json"
                destination_raw = destination_path.read_bytes()
                row.update(
                    status="link_cited_current_v2", destination_id=successor["id"],
                    destination_path=destination_path.relative_to(root).as_posix(),
                    legacy_source_unchanged=True,
                    explanation="current v2 cites this exact old claim; historical scope and changed Task sources are retained, not rewritten",
                    removed_historical_scope_paths=sorted(
                        set(_scope_paths(record)) - set(_scope_paths(successor))
                    ),
                )
                if apply:
                    if path.read_bytes() != raw or destination_path.read_bytes() != destination_raw:
                        raise RepoctlError(
                            "Knowledge changed during preserved citation validation",
                            code="knowledge_migration_concurrent_edit",
                        )
                    proof = {
                        "schema": _PROOF_SCHEMA, "schema_version": _PROOF_VERSION,
                        "legacy_id": legacy_id, "repo_id": repo_id,
                        "destination_id": successor["id"],
                        "legacy_sha256": _sha(raw), "destination_sha256": _sha(destination_raw),
                        "original_witness_path": before_path.relative_to(root).as_posix(),
                        "kind": "link_cited_current_v2",
                    }
                    written: list[Path] = []
                    try:
                        atomic_write(before_path, raw.decode("utf-8"))
                        written.append(before_path)
                        atomic_write(manifest_path, json.dumps(proof, indent=2, sort_keys=True) + "\n")
                        written.append(manifest_path)
                        if not preserved_legacy_migration_witness(root, record):
                            raise RepoctlError(
                                "cannot verify preserved current source citation",
                                code="knowledge_migration_proof_invalid",
                            )
                    except Exception:
                        for item in reversed(written):
                            if item.exists() and not item.is_symlink():
                                item.unlink()
                        raise
                    committed += 1
                    row["status"] = "applied_link_cited_current_v2"
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
def _authored_markdown_mentions(root: Path, record_id: str) -> list[dict[str, Any]]:
    """Locate authored references without editing code fences or user prose."""
    docs = root / "docs"
    if not docs.is_dir():
        return []
    matches: list[dict[str, Any]] = []
    for path in sorted(docs.rglob("*.md")):
        relative = path.relative_to(docs)
        if relative.parts and relative.parts[0] == "archive":
            continue  # historical sources must not be rewritten
        if not _regular_path_under(root, path):
            raise RepoctlError("authored Knowledge reference cannot be scanned safely",
                               code="knowledge_migration_reference_unreadable",
                               path=path.relative_to(root).as_posix())
        try:
            lines = path.read_text(encoding="utf-8").splitlines()
        except (OSError, UnicodeError) as exc:
            raise RepoctlError("authored Knowledge reference is unreadable",
                               code="knowledge_migration_reference_unreadable",
                               path=path.relative_to(root).as_posix()) from exc
        for number, line in enumerate(lines, 1):
            if record_id in line:
                matches.append({"path": path.relative_to(root).as_posix(), "line": number})
    return matches


def finalize_legacy_knowledge(
    root: Path, *, repo_id: str, record_id: str, apply: bool = False,
) -> dict[str, Any]:
    """One-time removal of a converted v1 from the active record directory.

    The original bytes move to historical evidence. Exact v2 citations are
    retargeted to that same historical file with their original digest; no
    claim, reason, recorded_at or source content is fabricated. A live v1
    reference that cannot be safely rewritten blocks finalization.
    """
    if _ID.fullmatch(record_id) is None:
        raise RepoctlError("finalize requires one valid --record-id", code="knowledge_migration_id_invalid")
    old_path = _record_dir(root) / f"{record_id}.json"
    archive = root / "docs/archive/knowledge" / f"{record_id}.json"
    before_path, manifest_path = _proof_paths(root, record_id)
    for item in (old_path, archive, before_path, manifest_path):
        _assert_safe_proof_path(root, item)
    with repoctl_lock(root) if apply else nullcontext():
        if not old_path.is_file():
            if archive.is_file() and manifest_path.is_file():
                try:
                    proof = json.loads(manifest_path.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError):
                    proof = {}
                if (proof.get("schema_version") == 2
                        and proof.get("finalized_archive_path") == archive.relative_to(root).as_posix()
                        and proof.get("finalized_archive_sha256") == _sha(archive.read_bytes())):
                    return {"mode": "apply" if apply else "dry_run", "record_count": 1,
                            "applied_count": 0, "records": [{"id": record_id, "status": "already_finalized",
                            "archive_path": archive.relative_to(root).as_posix()}],
                            "projection_problems": []}
            raise RepoctlError("v1 record not found for finalization", code="knowledge_migration_record_missing")
        original = old_path.read_bytes()
        try:
            legacy = json.loads(original.decode("utf-8"))
        except (ValueError, UnicodeError) as exc:
            raise RepoctlError("v1 Knowledge JSON is invalid", code="knowledge_migration_invalid_json") from exc
        if (not isinstance(legacy, dict) or legacy.get("id") != record_id
                or legacy.get("schema_version") != 1 or legacy.get("repo_id") != repo_id
                or not preserved_legacy_migration_witness(root, legacy)):
            return {"mode": "apply" if apply else "dry_run", "record_count": 1,
                    "applied_count": 0, "records": [{"id": record_id, "status": "needs_prior_migration",
                    "path": old_path.relative_to(root).as_posix(),
                    "explanation": "first verify an equivalent v2 and its original-source preservation witness"}],
                    "projection_problems": []}
        if archive.exists() or archive.is_symlink():
            raise RepoctlError("historical destination already exists", code="knowledge_migration_archive_collision",
                               path=archive.relative_to(root).as_posix())
        original_proof = manifest_path.read_bytes()
        proof = json.loads(original_proof.decode("utf-8"))
        destination_id = str(proof["destination_id"])
        old_rel = old_path.relative_to(root).as_posix()
        archive_rel = archive.relative_to(root).as_posix()
        rewrites: list[tuple[Path, bytes, str]] = []
        current_files: list[tuple[Path, dict[str, Any]]] = []
        for item in sorted(_record_dir(root).glob("K-*.json")):
            if item == old_path:
                continue
            if not _regular_path_under(root, item):
                raise RepoctlError("unsafe current Knowledge path", code="knowledge_migration_path_unsafe", path=str(item))
            data = json.loads(item.read_text(encoding="utf-8"))
            if data.get("schema_version") != KNOWLEDGE_RECORD_SCHEMA_VERSION:
                continue
            if _knowledge_record_contract_problems(data, record_id=item.stem,
                                                   repo_id=str(data.get("repo_id") or "")):
                raise RepoctlError("current Knowledge contract is invalid", code="knowledge_migration_current_invalid",
                                   path=item.relative_to(root).as_posix())
            current_files.append((item, data))
        for item, data in current_files:
            modified = json.loads(json.dumps(data))
            changed = False
            for ref in modified.get("source_refs", []):
                if not isinstance(ref, dict) or ref.get("path") != old_rel:
                    continue
                if ref.get("content_sha256") != _sha(original):
                    raise RepoctlError("v2 cites a different historical Knowledge digest",
                                       code="knowledge_migration_source_mismatch",
                                       path=item.relative_to(root).as_posix())
                ref["path"] = archive_rel
                changed = True
            if record_id in modified.get("replaces", []):
                if item.stem != destination_id:
                    raise RepoctlError("another v2 replaces this v1; review that lineage before finalization",
                                       code="knowledge_migration_replacement_review",
                                       path=item.relative_to(root).as_posix())
                modified["replaces"] = [value for value in modified["replaces"] if value != record_id]
                changed = True
            if changed:
                modified["record_digest"] = _knowledge_record_digest(modified)
                if _knowledge_record_contract_problems(modified, record_id=item.stem,
                                                       repo_id=str(modified.get("repo_id") or "")):
                    raise RepoctlError("rewritten v2 contract would be invalid",
                                       code="knowledge_migration_rewrite_invalid",
                                       path=item.relative_to(root).as_posix())
                raw = item.read_bytes()
                rewritten = json.dumps(modified, ensure_ascii=False, indent=2, sort_keys=True) + chr(10)
                rewrites.append((item, raw, rewritten))
        # Rewriting a cited current v2 would invalidate another source digest.
        changed_paths = {item.relative_to(root).as_posix() for item, _, _ in rewrites}
        for item, data in current_files:
            if any(isinstance(ref, dict) and ref.get("path") in changed_paths
                   for ref in data.get("source_refs", [])):
                raise RepoctlError("rewritten v2 is itself cited; review transitive provenance",
                                   code="knowledge_migration_dependent_citation",
                                   path=item.relative_to(root).as_posix())
        row: dict[str, Any] = {
            "id": record_id, "status": "ready_to_finalize",
            "path": old_rel, "archive_path": archive_rel, "original_sha256": _sha(original),
            "destination_id": destination_id,
            "rewritten_current_records": [item.relative_to(root).as_posix() for item, _, _ in rewrites],
            "authored_markdown_links": _authored_markdown_mentions(root, record_id),
        }
        if not apply:
            return {"mode": "dry_run", "record_count": 1, "applied_count": 0,
                    "records": [row], "projection_problems": []}
        if old_path.read_bytes() != original or manifest_path.read_bytes() != original_proof:
            raise RepoctlError("historical source or witness changed", code="knowledge_migration_concurrent_edit")
        # A successor modified since staging is not verified historical evidence.
        destination = _record_dir(root) / f"{destination_id}.json"
        if (not _regular_path_under(root, destination)
                or _sha(destination.read_bytes()) != proof["destination_sha256"]):
            raise RepoctlError("verified current Knowledge successor changed",
                               code="knowledge_migration_concurrent_edit")
        # Journal expected bytes before each write: a failing writer may
        # already have replaced the file when it raises.
        rewritten: list[tuple[Path, bytes, bytes]] = []
        moved = False
        expected_proof: bytes | None = None
        try:
            archive.parent.mkdir(parents=True, exist_ok=True)
            old_path.rename(archive)
            moved = True
            for item, previous, updated in rewrites:
                # External writers may ignore repoctl's lock: never overwrite
                # a newer current record with an earlier prepared rewrite.
                if item.read_bytes() != previous:
                    raise RepoctlError("current Knowledge changed during finalization",
                                       code="knowledge_migration_concurrent_edit")
                rewritten.append((item, previous, updated.encode("utf-8")))
                atomic_write(item, updated)
            new_proof = dict(proof)
            new_proof["schema_version"] = 2
            new_proof["destination_sha256"] = _sha(destination.read_bytes())
            new_proof["finalized_archive_path"] = archive_rel
            new_proof["finalized_archive_sha256"] = _sha(original)
            new_proof["rewritten_current_records"] = [
                {"path": item.relative_to(root).as_posix(), "before_sha256": _sha(previous),
                 "after_sha256": _sha(updated.encode("utf-8"))}
                for item, previous, updated in rewrites
            ]
            expected_proof = (json.dumps(new_proof, ensure_ascii=False, indent=2, sort_keys=True) + chr(10)).encode("utf-8")
            atomic_write(manifest_path, expected_proof.decode("utf-8"))
            # The archive is now the sole byte-preserved original; remove the
            # temporary duplicate witness only after all current refs resolve.
            before_path.unlink()
        except Exception as failure:
            conflicts: list[str] = []
            if expected_proof is not None:
                try:
                    if not _regular_path_under(root, manifest_path):
                        conflicts.append(manifest_path.relative_to(root).as_posix())
                    else:
                        current_proof = manifest_path.read_bytes()
                        if current_proof == expected_proof:
                            atomic_write(manifest_path, original_proof.decode("utf-8"))
                        elif current_proof != original_proof:
                            conflicts.append(manifest_path.relative_to(root).as_posix())
                except Exception:
                    conflicts.append(manifest_path.relative_to(root).as_posix())
            for item, previous, updated in reversed(rewritten):
                try:
                    if not _regular_path_under(root, item):
                        conflicts.append(item.relative_to(root).as_posix())
                    else:
                        current = item.read_bytes()
                        if current == updated:
                            atomic_write(item, previous.decode("utf-8"))
                        elif current != previous:
                            # This is now somebody else's edit, not our write.
                            conflicts.append(item.relative_to(root).as_posix())
                except Exception:
                    conflicts.append(item.relative_to(root).as_posix())
            if moved:
                try:
                    if not _regular_path_under(root, archive) or archive.read_bytes() != original or old_path.exists() or old_path.is_symlink():
                        conflicts.append(archive_rel)
                    elif conflicts:
                        # Keep the historical path for externally edited v2
                        # citations while restoring the original active v1.
                        atomic_write(old_path, original.decode("utf-8"))
                    else:
                        archive.rename(old_path)
                except Exception:
                    conflicts.append(archive_rel)
            if conflicts:
                raise RepoctlError(
                    "finalization failed; concurrent edits prevented a complete rollback; "
                    "original evidence was retained for explicit reconciliation",
                    code="knowledge_migration_rollback_incomplete",
                    path=", ".join(sorted(set(conflicts))),
                ) from failure
            raise
        row["status"] = "applied_finalized"
    _, problems = rebuild_knowledge_projection(root, repo_id=repo_id)
    return {"mode": "apply", "record_count": 1, "applied_count": 1,
            "records": [row], "projection_problems": [item.code for item in problems]}

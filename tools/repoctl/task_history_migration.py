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

def _receipt_transition_paths(root: Path, task_id: str) -> tuple[Path, Path, Path]:
    if TASK_ID.fullmatch(task_id) is None:
        raise ValueError("invalid Task ID for receipt transition")
    receipt = root / "docs/tasks/.repoctl-state/completions" / f"{task_id}.json"
    archive_dir = root / "docs/archive/task-evidence/receipts"
    return receipt, archive_dir / f"{task_id}.json", archive_dir / f"{task_id}.migration.json"


def _safe_transition_path(root: Path, path: Path, *, must_exist: bool) -> bool:
    """Reject symlink ancestors, escaped paths, and special files."""
    try:
        root_resolved = root.resolve(strict=True)
        if root_resolved not in path.resolve(strict=False).parents:
            return False
        node = root
        for part in path.relative_to(root).parts:
            node /= part
            if node.is_symlink():
                return False
        if must_exist:
            return path.is_file()
        return not path.exists()
    except (OSError, RuntimeError, ValueError):
        return False


def _receipt_knowledge_citations(root: Path) -> dict[str, list[str]]:
    """Never rewrite current Knowledge records behind their recorded digests."""
    refs: dict[str, list[str]] = {}
    directory = root / "docs/knowledge/records"
    if not directory.is_dir():
        return refs
    for path in sorted(directory.glob("K-*.json")):
        if not _safe_transition_path(root, path, must_exist=True):
            raise ValueError(f"unsafe Knowledge source: {path}")
        try:
            record = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, UnicodeError, ValueError) as exc:
            raise ValueError(f"unreadable Knowledge source: {path}") from exc
        if not isinstance(record, dict):
            raise ValueError(f"invalid Knowledge source: {path}")
        citation_paths: set[str] = set()
        for ref in record.get("source_refs", []):
            if isinstance(ref, dict) and isinstance(ref.get("path"), str):
                citation_paths.add(ref["path"])
        if isinstance(record.get("sources"), list):
            citation_paths.update(source for source in record["sources"] if isinstance(source, str))
        if isinstance(record.get("source"), str):
            citation_paths.add(record["source"])
        for cited_path in citation_paths:
            refs.setdefault(cited_path, []).append(path.relative_to(root).as_posix())
    return refs


def _plan_receipt_knowledge_relinks(
    root: Path, *, receipt_rel: str, archive_rel: str, original_sha256: str,
    cited: dict[str, list[str]],
) -> list[tuple[Path, bytes, str]]:
    """Relink only verified current v2 citations for one reviewed receipt transition."""
    from .io import RepoctlError
    from .knowledge_records import _knowledge_record_contract_problems, _knowledge_record_digest

    changes: list[tuple[Path, bytes, str]] = []
    for rel in sorted(set(cited.get(receipt_rel, []))):
        path = root / rel
        if not _safe_transition_path(root, path, must_exist=True):
            raise RepoctlError("citing Knowledge source is unsafe",
                               code="receipt_transition_knowledge_unsafe", path=rel)
        original = path.read_bytes()
        try:
            data = json.loads(original.decode("utf-8"))
        except (UnicodeError, ValueError) as exc:
            raise RepoctlError("citing Knowledge record is invalid JSON",
                               code="receipt_transition_knowledge_invalid", path=rel) from exc
        if not isinstance(data, dict) or data.get("schema_version") != 2:
            raise RepoctlError("first finalize active v1 Knowledge; only current v2 may be relinked",
                               code="receipt_transition_knowledge_v1", path=rel)
        if _knowledge_record_contract_problems(
            data, record_id=path.stem, repo_id=str(data.get("repo_id") or ""),
        ):
            raise RepoctlError("citing current Knowledge record is not valid",
                               code="receipt_transition_knowledge_invalid", path=rel)
        if cited.get(rel):
            raise RepoctlError("another Knowledge record cites this record's digest; review transitive provenance",
                               code="receipt_transition_knowledge_dependent", path=rel)
        updated = json.loads(json.dumps(data))
        matched = False
        for ref in updated["source_refs"]:
            if not isinstance(ref, dict) or ref.get("path") != receipt_rel:
                continue
            if ref.get("content_sha256") != original_sha256:
                raise RepoctlError("Knowledge citation differs from actual old receipt bytes",
                                   code="receipt_transition_knowledge_digest", path=rel)
            if ref.get("kind") not in {"workspace_file", "completion_receipt", "task_artifact"}:
                raise RepoctlError("Knowledge citation kind cannot follow the archived receipt",
                                   code="receipt_transition_knowledge_kind", path=rel)
            if ref["kind"] == "task_artifact":
                ref["kind"] = "workspace_file"  # old docs/tasks/ prefix classified this JSON as a Task
            ref["path"] = archive_rel
            matched = True
        if not matched:
            raise RepoctlError("Knowledge citation changed while preparing transition",
                               code="receipt_transition_knowledge_concurrent_edit", path=rel)
        updated["record_digest"] = _knowledge_record_digest(updated)
        if _knowledge_record_contract_problems(
            updated, record_id=path.stem, repo_id=str(updated.get("repo_id") or ""),
        ):
            raise RepoctlError("relinked Knowledge record would violate its contract",
                               code="receipt_transition_knowledge_invalid", path=rel)
        changes.append((path, original, json.dumps(updated, ensure_ascii=False, sort_keys=True, indent=2) + "\n"))
    return changes


def migrate_completion_receipts(
    root: Path, *, task_id: str = "", apply: bool = False, relink_knowledge: bool = False,
) -> dict[str, Any]:
    """Explicit, one-time v2-v4 -> v5 conversion, never a normal legacy reader.

    Validates the old and new schemas without inferring changed Task bytes,
    saves its byte-identical historical receipt once, and changes only its
    presentation. Existing artifact drift is separately reported, never fixed
    by rewriting the recorded completion hash. Verification and Discovery outcome remain in the old source, not
    copied as current completion authority. Unrecorded v2 start/transition
    events remain explicitly incomplete in v5, never fabricated.
    """
    from contextlib import nullcontext

    from .io import RepoctlError, atomic_write, repoctl_lock
    from .tasks import _validate_completion_receipt

    if task_id and TASK_ID.fullmatch(task_id) is None:
        raise RepoctlError("receipt transition requires a valid Task ID",
                           code="receipt_transition_invalid_id")
    if relink_knowledge and not task_id:
        raise RepoctlError("Knowledge relinking requires one reviewed Task ID",
                           code="receipt_transition_knowledge_requires_id")
    root = root.resolve()
    directory = root / "docs/tasks/.repoctl-state/completions"
    if not _safe_transition_path(root, directory, must_exist=False) and (
        not directory.is_dir() or directory.is_symlink()
    ):
        raise RepoctlError("completion authority directory is unsafe",
                           code="receipt_transition_unsafe", path="docs/tasks/.repoctl-state/completions")
    if task_id:
        paths = [directory / f"{task_id}.json"]
        if not paths[0].is_file():
            raise RepoctlError("completion receipt is missing", code="receipt_transition_missing",
                               path=paths[0].relative_to(root).as_posix())
    else:
        paths = sorted(directory.glob("T-*.json")) if directory.is_dir() else []

    records: list[dict[str, Any]] = []
    prepared: list[tuple[Path, bytes, str, Path, Path, str, str, list[tuple[Path, bytes, str]]]] = []
    with repoctl_lock(root) if apply else nullcontext():
        cited = _receipt_knowledge_citations(root)
        for path in paths:
            rel = path.relative_to(root).as_posix()
            row: dict[str, Any] = {"task_id": path.stem, "path": rel}
            records.append(row)
            if not _safe_transition_path(root, path, must_exist=True):
                row.update(status="unresolved", reason="completion receipt path is not a safe regular file")
                continue
            raw = path.read_bytes()
            try:
                data = json.loads(raw.decode("utf-8"))
            except (ValueError, UnicodeError):
                row.update(status="unresolved", reason="completion receipt is not valid UTF-8 JSON")
                continue
            if not isinstance(data, dict) or data.get("task_id") != path.stem:
                row.update(status="unresolved", reason="receipt identity does not match filename")
                continue
            version = data.get("schema_version")
            row["schema_version"] = version
            row["repo_id"] = data.get("repo_id", "")
            _original, archive, proof = _receipt_transition_paths(root, path.stem)
            if version == 5:
                if proof.exists() or proof.is_symlink() or archive.exists() or archive.is_symlink():
                    okay = False
                    if (_safe_transition_path(root, archive, must_exist=True)
                            and _safe_transition_path(root, proof, must_exist=True)):
                        try:
                            manifest = json.loads(proof.read_text(encoding="utf-8"))
                            okay = (
                                isinstance(manifest, dict)
                                and manifest.get("schema") == "repoctl.task.receipt-transition"
                                and manifest.get("schema_version") == 1
                                and manifest.get("task_id") == path.stem
                                and manifest.get("kind") == "presentation_only"
                                and manifest.get("original_schema_version") in {2, 3, 4}
                                and manifest.get("current_sha256") == digest_bytes(raw)
                                and manifest.get("original_sha256") == digest_bytes(archive.read_bytes())
                                and manifest.get("original_path") == archive.relative_to(root).as_posix()
                                and manifest.get("receipt_path") == rel
                                and manifest.get("task_content_sha256") == data.get("content_sha256")
                            )
                        except (OSError, ValueError, TypeError):
                            okay = False
                    row.update(status="already_converted" if okay else "unresolved",
                               reason="" if okay else "original completion proof is missing or changed")
                else:
                    row.update(status="already_current")
                if row["status"] != "unresolved":
                    try:
                        _validate_completion_receipt(path, root, data, receipt_text=raw.decode("utf-8"))
                    except (RepoctlError, UnicodeError) as exc:
                        row["source_problem"] = f"{getattr(exc, 'code', 'invalid_receipt_encoding')}: {exc}"
                continue
            if type(version) is not int or version not in {2, 3, 4}:
                row.update(status="unresolved", reason="unsupported receipt schema")
                continue
            complete_events = (
                isinstance(data.get("started_at"), str)
                and isinstance(data.get("completed_event_at"), str)
                and isinstance(data.get("repo_evidence"), dict)
                and isinstance(data["repo_evidence"].get("path_transitions"), list)
            )
            # v2 did not record start events or exact before/after transitions.
            # Their absence is an evidence limitation, not damaged source.
            # A v5 historical marker can retain this fact without invention.
            historical_gap = (
                version == 2
                and isinstance(data.get("repo_evidence"), dict)
                and "started_at" not in data
                and "completed_event_at" not in data
                and "path_transitions" not in data["repo_evidence"]
            )
            if not complete_events and not historical_gap:
                row.update(status="unresolved", reason="partial or inconsistent start/completion event or path transition evidence")
                continue
            if cited.get(rel) and not relink_knowledge:
                row.update(status="unresolved", reason="current Knowledge cites the original receipt digest",
                           cited_by=sorted(cited[rel]))
                continue
            if receipt_witness(root, path.stem, rel).status != "absent":
                row.update(status="unresolved", reason="Task presentation witness binds the original receipt; review before conversion")
                continue
            if not _safe_transition_path(root, archive, must_exist=False) or not _safe_transition_path(root, proof, must_exist=False):
                row.update(status="unresolved", reason="historical proof destination exists or is unsafe",
                           archive_path=archive.relative_to(root).as_posix())
                continue
            knowledge_relinks: list[tuple[Path, bytes, str]] = []
            if cited.get(rel):
                try:
                    knowledge_relinks = _plan_receipt_knowledge_relinks(
                        root, receipt_rel=rel, archive_rel=archive.relative_to(root).as_posix(),
                        original_sha256=digest_bytes(raw), cited=cited,
                    )
                except RepoctlError as exc:
                    row.update(status="unresolved", reason=f"{exc.code}: {exc}",
                               cited_by=sorted(cited[rel]))
                    continue
            try:
                _validate_completion_receipt(path, root, data, receipt_text=raw.decode("utf-8"),
                                             validate_artifact=False, migration_only=True)
                converted = dict(data)
                converted["schema_version"] = 5
                converted.pop("verification", None)
                converted.pop("discovery_outcome", None)
                if historical_gap:
                    converted["historical_transition_evidence"] = {
                        "status": "not_recorded",
                        "original_receipt_sha256": digest_bytes(raw),
                    }
                replacement = json.dumps(converted, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
                _validate_completion_receipt(path, root, converted, receipt_text=replacement,
                                             validate_artifact=False, migration_only=True)
            except RepoctlError as exc:
                row.update(status="unresolved", reason=f"{exc.code}: {exc}")
                continue
            try:
                _validate_completion_receipt(path, root, data, receipt_text=raw.decode("utf-8"),
                                             migration_only=True)
            except RepoctlError as exc:
                row["source_problem"] = f"{exc.code}: {exc}"
            manifest = {
                "schema": "repoctl.task.receipt-transition", "schema_version": 1,
                "task_id": path.stem, "receipt_path": rel,
                "original_path": archive.relative_to(root).as_posix(),
                "original_schema_version": version, "original_sha256": digest_bytes(raw),
                "current_sha256": digest_bytes(replacement.encode("utf-8")),
                "task_content_sha256": converted["content_sha256"],
                "kind": "presentation_only",
                "source_integrity_at_conversion": "unverified" if row.get("source_problem") else "verified",
            }
            if knowledge_relinks:
                manifest["relinked_current_knowledge_records"] = [
                    {"path": item.relative_to(root).as_posix(),
                     "before_sha256": digest_bytes(before),
                     "after_sha256": digest_bytes(after.encode("utf-8"))}
                    for item, before, after in knowledge_relinks
                ]
            manifest_text = json.dumps(manifest, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            prepared.append((path, raw, replacement, archive, proof, manifest_text, rel, knowledge_relinks))
            row.update(status="ready", archive_path=manifest["original_path"],
                       original_sha256=manifest["original_sha256"],
                       current_sha256=manifest["current_sha256"])
            if knowledge_relinks:
                row["relinked_current_knowledge_records"] = manifest["relinked_current_knowledge_records"]
            if historical_gap:
                row["historical_transition_evidence"] = "not_recorded"
                row["evidence_limitation"] = "v2 never recorded start/completion events or path transitions"
        if apply and prepared:
            written: list[tuple[Path, bytes | None, bytes]] = []
            try:
                for path, original, replacement, archive, proof, manifest_text, rel, knowledge_relinks in prepared:
                    if path.read_bytes() != original:
                        raise RepoctlError("receipt changed during explicit conversion",
                                           code="receipt_transition_concurrent_edit", path=rel)
                    if not _safe_transition_path(root, archive, must_exist=False) or not _safe_transition_path(root, proof, must_exist=False):
                        raise RepoctlError("historical destination changed during conversion",
                                           code="receipt_transition_concurrent_edit", path=rel)
                    archive.parent.mkdir(parents=True, exist_ok=True)
                    written.append((archive, None, original))
                    atomic_write(archive, original.decode("utf-8"))
                    if path.read_bytes() != original:
                        raise RepoctlError("receipt changed after archiving the original",
                                           code="receipt_transition_concurrent_edit", path=rel)
                    written.append((path, original, replacement.encode("utf-8")))
                    atomic_write(path, replacement)
                    for item, old, new in knowledge_relinks:
                        if not _safe_transition_path(root, item, must_exist=True) or item.read_bytes() != old:
                            raise RepoctlError("Knowledge record changed during receipt conversion",
                                               code="receipt_transition_knowledge_concurrent_edit",
                                               path=item.relative_to(root).as_posix())
                        written.append((item, old, new.encode("utf-8")))
                        atomic_write(item, new)
                    if not _safe_transition_path(root, proof, must_exist=False):
                        raise RepoctlError("receipt proof destination changed during conversion",
                                           code="receipt_transition_concurrent_edit", path=rel)
                    written.append((proof, None, manifest_text.encode("utf-8")))
                    atomic_write(proof, manifest_text)
                    for row in records:
                        if row["path"] == rel:
                            row["status"] = "applied"
                            break
            except Exception:
                rollback_errors: list[str] = []
                relinked_paths = {
                    item.relative_to(root).as_posix()
                    for _, _, _, _, _, _, _, relinks in prepared
                    for item, _, _ in relinks
                }
                protected_archives = {
                    historical_archive
                    for _, _, _, historical_archive, _, _, _, relinks in prepared
                    if relinks
                }
                for item, original_bytes, expected_current in reversed(written):
                    try:
                        if (item in protected_archives
                                and any(problem in relinked_paths for problem in rollback_errors)):
                            # A concurrent writer may cite this exact archived original.
                            rollback_errors.append(item.relative_to(root).as_posix())
                            continue
                        if not item.exists() and original_bytes is None:
                            continue
                        if not item.is_file() or item.is_symlink():
                            rollback_errors.append(item.relative_to(root).as_posix())
                            continue
                        present = item.read_bytes()
                        if original_bytes is not None and present == original_bytes:
                            continue  # failed write never replaced the prior source
                        if present != expected_current:
                            rollback_errors.append(item.relative_to(root).as_posix())
                            continue
                        if original_bytes is None:
                            item.unlink()
                        else:
                            atomic_write(item, original_bytes.decode("utf-8"))
                    except (OSError, UnicodeError):
                        rollback_errors.append(item.relative_to(root).as_posix())
                if rollback_errors:
                    raise RepoctlError("conversion failed and concurrent writes blocked safe rollback",
                                       code="receipt_transition_rollback_incomplete",
                                       path=", ".join(rollback_errors))
                raise
    unresolved = [row for row in records if row["status"] == "unresolved"]
    applied = [row for row in records if row["status"] == "applied"]
    repo_ids = sorted({row["repo_id"] for row in applied if isinstance(row.get("repo_id"), str)})
    rebuild_commands = [
        "./scripts/repoctl history rebuild "
        + (f"--repo-id {repo_id}" if repo_id else "--workspace") + " --json"
        for repo_id in repo_ids
    ]
    return {
        "mode": "apply" if apply else "dry_run",
        "record_count": len(records),
        "ready_count": sum(row["status"] == "ready" for row in records),
        "applied_count": len(applied),
        "unresolved_count": len(unresolved),
        "source_problem_count": sum(bool(row.get("source_problem")) for row in records),
        "historical_evidence_gap_count": sum(row.get("historical_transition_evidence") == "not_recorded" for row in records),
        "records": records,
        "history_rebuild_required": bool(applied),
        "history_rebuild_commands": rebuild_commands,
    }


def migrate_legacy_task_states(
    root: Path, *, task_id: str = "", apply: bool = False,
) -> dict[str, Any]:
    """Retire closed v3 baseline files without inventing a v4 start snapshot.

    A live v3 Task cannot be resumed safely using a guessed started_at or
    dirty_path_states: it must be reviewed separately. Original bytes of
    terminal states are preserved under docs/archive/task-evidence/.
    """
    from contextlib import nullcontext

    from .io import RepoctlError, atomic_write, repoctl_lock
    from .tasks import _read_repo_baseline, resolve_task

    root = root.resolve()
    if task_id and TASK_ID.fullmatch(task_id) is None:
        raise RepoctlError("state migration requires a valid Task ID", code="state_transition_invalid_id")
    state_dir = root / "docs/tasks/.repoctl-state"
    paths = (
        [state_dir / f"{task_id}.json"]
        if task_id else sorted(state_dir.glob("T-*.json"))
    )
    records: list[dict[str, Any]] = []
    prepared: list[tuple[Path, bytes, Path, Path, str]] = []
    with repoctl_lock(root) if apply else nullcontext():
        citations = _receipt_knowledge_citations(root)
        for path in paths:
            tid = path.stem
            rel = path.relative_to(root).as_posix()
            archive_dir = root / "docs/archive/task-evidence/start-baselines"
            archived = archive_dir / f"{tid}.json"
            proof = archive_dir / f"{tid}.migration.json"
            row: dict[str, Any] = {"task_id": tid, "path": rel}
            records.append(row)
            if not path.exists() and not path.is_symlink():
                okay = False
                if (_safe_transition_path(root, archived, must_exist=True)
                        and _safe_transition_path(root, proof, must_exist=True)):
                    try:
                        original = archived.read_bytes()
                        meta = json.loads(proof.read_text(encoding="utf-8"))
                        okay = (
                            isinstance(meta, dict)
                            and meta.get("schema") == "repoctl.task.state-retirement"
                            and meta.get("schema_version") == 1
                            and meta.get("task_id") == tid
                            and meta.get("original_path") == rel
                            and meta.get("archived_path") == archived.relative_to(root).as_posix()
                            and meta.get("original_sha256") == digest_bytes(original)
                            and meta.get("kind") == "closed_state_retirement"
                        )
                    except (OSError, ValueError, UnicodeError):
                        okay = False
                row.update(status="already_retired" if okay else "unresolved",
                           reason="" if okay else "start-baseline state and proof are missing or changed")
                continue
            if not _safe_transition_path(root, path, must_exist=True):
                row.update(status="unresolved", reason="unsafe start-baseline state path")
                continue
            original = path.read_bytes()
            try:
                state = json.loads(original.decode("utf-8"))
            except (UnicodeError, ValueError):
                row.update(status="unresolved", reason="invalid UTF-8 state JSON")
                continue
            if not isinstance(state, dict) or state.get("task_id") != tid or state.get("schema") != "repoctl.task.state":
                row.update(status="unresolved", reason="state identity or schema is invalid")
                continue
            version = state.get("schema_version")
            row["schema_version"] = version
            if version == 4:
                try:
                    _read_repo_baseline(root, tid)
                except RepoctlError as exc:
                    row.update(status="unresolved", reason=f"{exc.code}: {exc}")
                    continue
                row.update(status="already_current")
                continue
            if version != 3:
                row.update(status="unresolved", reason="unsupported start-baseline schema")
                continue
            try:
                _read_repo_baseline(root, tid, migration_only=True)
                task = resolve_task(root, tid)
            except RepoctlError as exc:
                row.update(status="unresolved", reason=f"{exc.code}: {exc}")
                continue
            if task.status not in {"done", "canceled"}:
                row.update(status="unresolved", reason="live v3 baseline has no provable v4 started_at/stable dirty states; review owner-owned Task without resetting dirty")
                continue
            if task.status == "done":
                receipt = root / "docs/tasks/.repoctl-state/completions" / f"{tid}.json"
                if not _safe_transition_path(root, receipt, must_exist=True):
                    row.update(status="unresolved", reason="closed Task completion authority is missing")
                    continue
                try:
                    completion = json.loads(receipt.read_text(encoding="utf-8"))
                except (OSError, UnicodeError, ValueError):
                    row.update(status="unresolved", reason="closed Task completion authority is unreadable")
                    continue
                if not isinstance(completion, dict) or completion.get("task_id") != tid or completion.get("schema_version") != 5:
                    row.update(status="unresolved", reason="convert the original completion receipt to v5 before retiring its state")
                    continue
            if citations.get(rel):
                row.update(status="unresolved", reason="current Knowledge source cites the original Task state", cited_by=citations[rel])
                continue
            if not (_safe_transition_path(root, archived, must_exist=False)
                    and _safe_transition_path(root, proof, must_exist=False)):
                row.update(status="unresolved", reason="state archive destination is occupied or unsafe")
                continue
            meta = {
                "schema": "repoctl.task.state-retirement",
                "schema_version": 1,
                "task_id": tid,
                "original_path": rel,
                "archived_path": archived.relative_to(root).as_posix(),
                "original_sha256": digest_bytes(original),
                "task_path": task.rel_path,
                "terminal_status": task.status,
                "kind": "closed_state_retirement",
            }
            proof_text = json.dumps(meta, ensure_ascii=False, indent=2, sort_keys=True) + "\n"
            prepared.append((path, original, archived, proof, proof_text))
            row.update(status="ready", archived_path=meta["archived_path"],
                       original_sha256=meta["original_sha256"])
        if apply and prepared:
            written: list[tuple[str, Path, bytes | None, bytes | None]] = []
            try:
                for path, original, archived, proof, proof_text in prepared:
                    rel = path.relative_to(root).as_posix()
                    if path.read_bytes() != original:
                        raise RepoctlError("Task state changed during migration",
                                           code="state_transition_concurrent_edit", path=rel)
                    if not (_safe_transition_path(root, archived, must_exist=False)
                            and _safe_transition_path(root, proof, must_exist=False)):
                        raise RepoctlError("Task state history destination changed",
                                           code="state_transition_concurrent_edit", path=rel)
                    archived.parent.mkdir(parents=True, exist_ok=True)
                    written.append(("create", archived, None, original))
                    atomic_write(archived, original.decode("utf-8"))
                    if path.read_bytes() != original or not _safe_transition_path(root, proof, must_exist=False):
                        raise RepoctlError("Task state changed while archiving",
                                           code="state_transition_concurrent_edit", path=rel)
                    written.append(("create", proof, None, proof_text.encode("utf-8")))
                    atomic_write(proof, proof_text)
                    if path.read_bytes() != original:
                        raise RepoctlError("Task state changed before retirement",
                                           code="state_transition_concurrent_edit", path=rel)
                    written.append(("retire", path, original, None))
                    path.unlink()
                    for row in records:
                        if row["path"] == rel:
                            row["status"] = "applied"
                            break
            except Exception:
                failures: list[str] = []
                for kind, path, before, after in reversed(written):
                    try:
                        if kind == "retire":
                            if not path.exists() and not path.is_symlink():
                                atomic_write(path, before.decode("utf-8"))
                            elif not path.is_file() or path.is_symlink() or path.read_bytes() != before:
                                failures.append(path.relative_to(root).as_posix())
                        else:
                            if not path.exists() and not path.is_symlink():
                                continue
                            if not path.is_file() or path.is_symlink() or path.read_bytes() != after:
                                failures.append(path.relative_to(root).as_posix())
                            else:
                                path.unlink()
                    except (OSError, UnicodeError):
                        failures.append(path.relative_to(root).as_posix())
                if failures:
                    raise RepoctlError("state retirement failure and unsafe rollback",
                                       code="state_transition_rollback_incomplete", path=", ".join(failures))
                raise
    unresolved = [row for row in records if row["status"] == "unresolved"]
    return {
        "mode": "apply" if apply else "dry_run",
        "record_count": len(records),
        "ready_count": sum(row["status"] == "ready" for row in records),
        "applied_count": sum(row["status"] == "applied" for row in records),
        "unresolved_count": len(unresolved),
        "records": records,
    }

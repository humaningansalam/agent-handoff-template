"""Explicit one-time v1 Knowledge conversion; never used by ordinary queries.

Preserves historical bytes outside normal sources, requires human-supplied
missing provenance, and never guesses a different decision from a shared claim.
"""
from __future__ import annotations

import hashlib
import json
import re
from pathlib import Path
from typing import Any

from .io import RepoctlError, atomic_write, repoctl_lock
from .knowledge_records import (
    _knowledge_record_digest, _record_dir, prepare_knowledge_record,
)
from .knowledge_projection import rebuild_knowledge_projection


def _sha(raw: bytes) -> str:
    return 'sha256:' + hashlib.sha256(raw).hexdigest()


def _source_paths(raw: dict[str, Any]) -> list[str]:
    refs = raw.get('source_refs')
    if isinstance(refs, list):
        paths = [str(row.get('path') or '') for row in refs if isinstance(row, dict)]
        if paths:
            return sorted(set(path for path in paths if path))
    if isinstance(raw.get('sources'), list):
        return sorted(set(str(x) for x in raw['sources'] if isinstance(x, str) and x))
    return [raw['source']] if isinstance(raw.get('source'), str) and raw['source'] else []


def _references_to_id(root: Path, legacy_id: str) -> list[str]:
    found: list[str] = []
    for folder in (root / 'docs',):
        if not folder.is_dir():
            continue
        for path in sorted(p for p in folder.rglob('*') if p.suffix in {'.md', '.json'})[:4000]:
            if path.is_symlink() or '.repoctl-state' in path.parts or path.name == f'{legacy_id}.json':
                continue
            try:
                if path.stat().st_size <= 512 * 1024 and legacy_id in path.read_text(encoding='utf-8'):
                    found.append(path.relative_to(root).as_posix())
            except (OSError, UnicodeError, ValueError):
                continue
            if len(found) >= 12:
                return found
    return found


def migrate_legacy_knowledge(
    root: Path, *, repo_id: str, record_id: str = '', apply: bool = False,
    reason_override: str = '', sources_override: list[str] | None = None,
) -> dict[str, Any]:
    # One record per explicit apply makes the proof/source change transactional
    # without a multi-record partial-commit state. Bulk audit remains dry-run.
    if apply and not record_id:
        raise RepoctlError('apply one --record-id at a time; review each record before conversion', code='knowledge_migration_requires_record_id')
    if (reason_override or sources_override) and not record_id:
        raise RepoctlError('reason/source overrides need one --record-id', code='knowledge_migration_record_id_required')
    items: list[dict[str, Any]] = []
    changed = 0
    with repoctl_lock(root) if apply else __import__('contextlib').nullcontext():
        paths = sorted(_record_dir(root).glob('K-*.json'))
        if record_id:
            if not re.fullmatch(r'K-[A-Za-z0-9-]+', record_id):
                raise RepoctlError('invalid Knowledge record ID', code='knowledge_migration_id_invalid')
            paths = [_record_dir(root) / f'{record_id}.json']
            if not paths[0].is_file():
                raise RepoctlError('Knowledge record not found', code='knowledge_migration_record_missing')
        current: list[dict[str, Any]] = []
        legacy: list[tuple[Path, bytes, dict[str, Any]]] = []
        for path in paths if not record_id else sorted(_record_dir(root).glob('K-*.json')):
            if path.is_symlink() or not path.is_file():
                raise RepoctlError('Knowledge path is unsafe', code='knowledge_migration_path_unsafe', path=str(path))
            try:
                raw_bytes = path.read_bytes()
                obj = json.loads(raw_bytes.decode('utf-8'))
            except (OSError, UnicodeError, ValueError) as exc:
                raise RepoctlError('Cannot validate a Knowledge source during migration', code='knowledge_migration_invalid_json', path=str(path)) from exc
            if not isinstance(obj, dict):
                raise RepoctlError('Knowledge source must be an object', code='knowledge_migration_invalid_record', path=str(path))
            if obj.get('schema_version') == 2:
                current.append(obj)
            elif obj.get('schema_version') == 1 and str(obj.get('repo_id') or '') == repo_id and (not record_id or obj.get('id') == record_id):
                if obj.get('schema') != 'repoctl.knowledge.record':
                    raise RepoctlError('Legacy source schema is invalid', code='knowledge_migration_invalid_schema', path=str(path))
                legacy.append((path, raw_bytes, obj))
        if record_id and not legacy:
            raise RepoctlError('Selected record is not a v1 Knowledge source', code='knowledge_migration_not_legacy')
        for path, raw_bytes, obj in legacy:
            old_id = str(obj.get('id') or '')
            if old_id != path.stem or not re.fullmatch(r'K-[A-Za-z0-9-]+', old_id):
                raise RepoctlError('Legacy filename and ID differ', code='knowledge_migration_id_mismatch', path=str(path))
            reason = reason_override.strip() or str(obj.get('reason') or obj.get('summary') or '').strip()
            sources = list(sources_override or _source_paths(obj))
            row: dict[str, Any] = {'id': old_id, 'path': path.relative_to(root).as_posix(),
                                   'source_count': len(sources), 'original_sha256': _sha(raw_bytes)}
            if not reason:
                row.update(status='needs_reason', explanation='legacy record has no technical reason; supply --reason for this ID')
            elif not sources:
                row.update(status='needs_source', explanation='legacy record has no source identity; supply --source for this ID')
            else:
                prepared, problems = prepare_knowledge_record(
                    root, repo_id=repo_id, kind=str(obj.get('kind') or ''),
                    claim=str(obj.get('claim') or ''), reason=reason,
                    sources=sources,
                    applies_to=(obj.get('applies_to', {}).get('paths') if isinstance(obj.get('applies_to'), dict) else []),
                )
                if prepared is None or problems:
                    row.update(status='needs_source_review', explanation='; '.join(p.message for p in problems[:3]))
                else:
                    new = dict(prepared.data)
                    # A previously saved source digest must not be silently
                    # re-bound to different bytes by the one-time converter.
                    old_refs = obj.get('source_refs') if isinstance(obj.get('source_refs'), list) else []
                    expected_digests = {
                        str(ref.get('path')): str(ref.get('content_sha256'))
                        for ref in old_refs if isinstance(ref, dict) and ref.get('path') and ref.get('content_sha256')
                    }
                    converted_digests = {
                        str(ref.get('path')): str(ref.get('content_sha256'))
                        for ref in new.get('source_refs', []) if isinstance(ref, dict)
                    }
                    mismatch = [path for path, sha in expected_digests.items()
                                if converted_digests.get(path) != sha]
                    if mismatch and not sources_override:
                        row.update(status='needs_source_review', explanation='source digest differs from the saved legacy identity: '+', '.join(mismatch[:4]))
                        items.append(row)
                        continue
                    def identity(x: dict[str, Any]) -> tuple[Any, ...]:
                        return (
                            x.get('repo_id'), x.get('kind'),
                            ' '.join(str(x.get('claim') or '').split()).casefold(),
                            ' '.join(str(x.get('reason') or '').split()).casefold(),
                            tuple(sorted((v.get('path'),v.get('content_sha256')) for v in x.get('source_refs',[]) if isinstance(v,dict))),
                        )
                    equivalents = [record for record in current if identity(record) == identity(new)]
                    refs = _references_to_id(root, old_id) if equivalents else []
                    if len(equivalents) > 1 or refs:
                        row.update(status='needs_reference_review', explanation='duplicate current record with historical references or ambiguous matches',
                                   references=refs)
                    else:
                        duplicate = equivalents[0] if equivalents else None
                        if duplicate:
                            row.update(status='merge_equivalent', destination_id=duplicate['id'])
                        else:
                            new['id'] = old_id
                            new['record_digest'] = _knowledge_record_digest(new)
                            row.update(status='convert_in_place', destination_id=old_id)
                        if apply:
                            proof_dir = root / 'docs/tasks/.repoctl-state/knowledge-migrations' / old_id
                            before_file = proof_dir / 'original.json'
                            receipt_file = proof_dir / 'migration.json'
                            if before_file.exists() or receipt_file.exists() or before_file.is_symlink() or receipt_file.is_symlink():
                                raise RepoctlError('Knowledge migration proof already exists', code='knowledge_migration_existing_proof', path=str(proof_dir))
                            if path.read_bytes() != raw_bytes:
                                raise RepoctlError('Knowledge changed during migration', code='knowledge_migration_concurrent_edit', path=str(path))
                            receipt = {'schema':'repoctl.knowledge.one-time-migration', 'schema_version':1,
                                       'legacy_id':old_id, 'destination_id':row['destination_id'],
                                       'original_sha256':_sha(raw_bytes), 'kind':row['status']}
                            original_text = raw_bytes.decode('utf-8')
                            try:
                                atomic_write(before_file, original_text)
                                atomic_write(receipt_file, json.dumps(receipt,indent=2,sort_keys=True)+'\n')
                                if duplicate:
                                    path.unlink()
                                else:
                                    atomic_write(path, json.dumps(new,indent=2,sort_keys=True,ensure_ascii=False)+'\n')
                            except Exception:
                                if not path.exists() or path.read_bytes() != raw_bytes:
                                    atomic_write(path, original_text)
                                for proof in (receipt_file,before_file):
                                    if proof.exists() and not proof.is_symlink():proof.unlink()
                                raise
                            row['status'] = 'applied_'+row['status']
                            changed += 1
            items.append(row)
    # Rebuild derived state only after source mutation has succeeded; ordinary
    # readers have validated-direct fallback if a projection cannot be built.
    rebuild_problem_codes: list[str] = []
    if apply and changed:
        _, problems = rebuild_knowledge_projection(root, repo_id=repo_id)
        rebuild_problem_codes = [p.code for p in problems]
    return {'mode':'apply' if apply else 'dry_run', 'record_count':len(items),
            'applied_count':changed, 'records':items,
            'projection_problems':rebuild_problem_codes}

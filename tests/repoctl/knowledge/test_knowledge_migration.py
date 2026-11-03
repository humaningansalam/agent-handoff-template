from __future__ import annotations

import json
from pathlib import Path

import pytest

from tests.repoctl.knowledge_test_helpers import _setup_knowledge_workspace, _add_knowledge_source
from tools.repoctl.cli import main


def _legacy(root: Path, record_id: str, *, reason: str = '', sources: list[str] | None = None) -> Path:
    record = {'schema':'repoctl.knowledge.record', 'schema_version':1,
              'id':record_id, 'repo_id':'main', 'kind':'decision',
              'claim':'Keep durable keys on every retry.'}
    if reason: record['reason'] = reason
    if sources is not None:record['sources'] = sources
    path = root / 'docs/knowledge/records' / f'{record_id}.json'
    path.parent.mkdir(parents=True,exist_ok=True)
    path.write_text(json.dumps(record,indent=2,sort_keys=True)+'\n')
    return path


def _out(capsys):
    return json.loads(capsys.readouterr().out)


def test_one_time_migration_preserves_original_source_and_creates_v2(tmp_path, monkeypatch, capsys):
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name = 'K-20260609184045Z--idempotence'
    source = 'docs/contracts/repoctl-evidence-contract.md'
    path = _legacy(tmp_path,name,reason='Duplicate notification would charge the account twice.',sources=[source])
    original = path.read_bytes()
    assert main(['knowledge','migrate','--repo-id','main','--json'])==0
    dry=_out(capsys)
    assert dry['data']['records'][0]['status']=='create_v2'
    assert path.read_bytes()==original
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json'])==0
    data=_out(capsys)
    assert data['data']['applied_count']==1
    assert path.read_bytes()==original
    destination=data['data']['records'][0]['destination_id']
    assert destination != name
    record=json.loads((tmp_path/f'docs/knowledge/records/{destination}.json').read_text())
    assert record['id']==destination and record['schema_version']==2
    assert record['reason']=='Duplicate notification would charge the account twice.'
    assert record['source_refs'][0]['path']==source
    witness=tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'/name
    assert (witness/'original.json').read_bytes()==original
    assert main(['knowledge','query','durable keys','--repo-id','main','--json'])==0
    assert _out(capsys)['data']['results'][0]['record']['id']==destination
    assert main(['knowledge','migrate','--repo-id','main','--json'])==0
    assert _out(capsys)['data']['records'][0]['status']=='already_preserved'
    assert main(['knowledge','check','--repo-id','main','--json'])==0
    assert _out(capsys)['data']['legacy_record_count']==1


def test_knowledge_stage_rollback_preserves_external_v2_edit(
    tmp_path: Path, monkeypatch, capsys,
) -> None:
    from tools.repoctl import knowledge_migration as migration
    from tools.repoctl.io import RepoctlError

    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name = 'K-20260609184045Z--stage-concurrency'
    old = _legacy(tmp_path, name,
                  reason='A real retry test found stable durable keys.',
                  sources=['docs/contracts/repoctl-evidence-contract.md'])
    original = old.read_bytes()
    preview = migration.migrate_legacy_knowledge(
        tmp_path, repo_id='main', record_id=name,
    )['records'][0]
    assert preview['status'] == 'create_v2'
    destination = tmp_path / preview['destination_path']
    real_write = migration.atomic_write
    external = b'{"external":"independent owner edit after v2 write"}\n'

    def fail_proof_after_external_edit(path, content):
        if path.name == 'migration.json':
            destination.write_bytes(external)
            raise OSError('later witness failure')
        real_write(path, content)

    monkeypatch.setattr(migration, 'atomic_write', fail_proof_after_external_edit)
    with pytest.raises(RepoctlError) as caught:
        migration.migrate_legacy_knowledge(
            tmp_path, repo_id='main', record_id=name, apply=True,
        )
    assert caught.value.code == 'knowledge_migration_rollback_incomplete'
    assert destination.read_bytes() == external
    assert old.read_bytes() == original


def test_finalization_relocates_v1_and_preserves_exact_v2_citation(tmp_path, monkeypatch, capsys):
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name = 'K-20260609184045Z--archived-origin'
    original_path = _legacy(tmp_path, name)
    original = original_path.read_bytes()
    authored = tmp_path / 'docs/manual-links.md'
    authored.write_text(f'# Source links\n\n- docs/knowledge/records/{name}.json\n')
    authored_before = authored.read_bytes()
    assert main(['knowledge','add','--repo-id','main','--kind','decision',
                 '--claim','Keep durable keys on every retry.',
                 '--reason','A later observed retry proves the same invariant.',
                 '--source',f'docs/knowledge/records/{name}.json','--json'])==0
    current = _out(capsys)['data']['record']
    old_ref = current['source_refs'][0]
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json'])==0
    assert _out(capsys)['data']['records'][0]['status']=='applied_link_cited_current_v2'
    before_current = (tmp_path/f"docs/knowledge/records/{current['id']}.json").read_bytes()
    args = ['knowledge','migrate','--repo-id','main','--record-id',name,'--finalize','--json']
    assert main(args)==0
    preview = _out(capsys)['data']['records'][0]
    assert preview['status']=='ready_to_finalize'
    assert preview['authored_markdown_links']==[{'path':'docs/manual-links.md','line':3}]
    assert original_path.read_bytes()==original
    assert (tmp_path/f"docs/knowledge/records/{current['id']}.json").read_bytes()==before_current
    assert main([*args[:-1],'--apply','--json'])==0
    applied = _out(capsys)['data']['records'][0]
    assert applied['status']=='applied_finalized'
    archive = tmp_path/f'docs/archive/knowledge/{name}.json'
    assert not original_path.exists() and archive.read_bytes()==original
    assert authored.read_bytes()==authored_before  # authored links require owner review
    updated = json.loads((tmp_path/f"docs/knowledge/records/{current['id']}.json").read_text())
    assert updated['source_refs'][0]['path']==f'docs/archive/knowledge/{name}.json'
    assert updated['source_refs'][0]['content_sha256']==old_ref['content_sha256']
    assert updated['reason']==current['reason'] and updated['recorded_at']==current['recorded_at']
    assert not (tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'/name/'original.json').exists()
    assert main(['knowledge','check','--repo-id','main','--json'])==0
    checked=_out(capsys)
    assert checked['data']['legacy_record_count']==0
    assert not any(p['code']=='knowledge_source_missing' for p in checked['problems'])
    assert main(['knowledge','migrate','--repo-id','main','--json'])==0
    assert _out(capsys)['data']['record_count']==0
    assert main([*args[:-1],'--apply','--json'])==0
    assert _out(capsys)['data']['records'][0]['status']=='already_finalized'


def test_finalization_partial_write_rolls_back_original_and_citations(tmp_path, monkeypatch, capsys):
    from tools.repoctl import knowledge_migration as migration
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name = 'K-20260609184045Z--finalize-rollback'
    old = _legacy(tmp_path, name)
    assert main(['knowledge','add','--repo-id','main','--kind','decision',
                 '--claim','Keep durable keys on every retry.',
                 '--reason','An actual retry test demonstrated stable token reuse.',
                 '--source',f'docs/knowledge/records/{name}.json','--json'])==0
    current = _out(capsys)['data']['record']
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json'])==0
    _out(capsys)
    current_path = tmp_path/f"docs/knowledge/records/{current['id']}.json"
    manifest = tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'/name/'migration.json'
    old_bytes, current_bytes, manifest_bytes = old.read_bytes(),current_path.read_bytes(),manifest.read_bytes()
    original_write = migration.atomic_write
    writes = 0
    def fail_second_write(path, body):
        nonlocal writes
        writes += 1
        if writes == 2:
            raise OSError('simulated finalization failure')
        return original_write(path, body)
    monkeypatch.setattr(migration,'atomic_write',fail_second_write)
    with pytest.raises(OSError,match='simulated finalization failure'):
        migration.finalize_legacy_knowledge(tmp_path,repo_id='main',record_id=name,apply=True)
    assert old.read_bytes()==old_bytes
    assert current_path.read_bytes()==current_bytes
    assert manifest.read_bytes()==manifest_bytes
    assert not (tmp_path/f'docs/archive/knowledge/{name}.json').exists()


def test_finalization_preserves_unlocked_concurrent_current_edit(tmp_path, monkeypatch, capsys):
    """If another writer changes a later v2, roll back only our own writes."""
    from tools.repoctl import knowledge_migration as migration
    from tools.repoctl.io import RepoctlError
    from tools.repoctl.knowledge_records import _knowledge_record_digest

    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name = 'K-20260609184045Z--concurrent-finalize'
    old = _legacy(tmp_path, name)
    current = []
    for claim in ('Keep durable keys on every retry.',
                  'Historical durable-key rationale is still useful.'):
        assert main(['knowledge', 'add', '--repo-id', 'main', '--kind', 'decision',
                     '--claim', claim, '--reason', 'A verified historical record is the original source.',
                     '--source', f'docs/knowledge/records/{name}.json', '--json']) == 0
        current.append(_out(capsys)['data']['record']['id'])
    assert main(['knowledge', 'migrate', '--repo-id', 'main', '--record-id', name, '--apply', '--json']) == 0
    _out(capsys)
    first, second = [tmp_path / 'docs/knowledge/records' / f'{id}.json' for id in sorted(current)]
    prior_first = first.read_bytes()
    original = old.read_bytes()
    proof = tmp_path / 'docs/tasks/.repoctl-state/knowledge-migrations' / name / 'migration.json'
    prior_proof = proof.read_bytes()
    write_original = migration.atomic_write
    changed = False

    def external_edit_during_first_write(path, body):
        nonlocal changed
        if path == first and not changed:
            changed = True
            newer = json.loads(second.read_text())
            newer['reason'] = 'An independent writer revised this reason.'
            newer['record_digest'] = _knowledge_record_digest(newer)
            second.write_text(json.dumps(newer, indent=2, sort_keys=True) + '\n')
        return write_original(path, body)

    monkeypatch.setattr(migration, 'atomic_write', external_edit_during_first_write)
    with pytest.raises(RepoctlError, match='current Knowledge changed during finalization'):
        migration.finalize_legacy_knowledge(tmp_path, repo_id='main', record_id=name, apply=True)
    assert changed
    assert old.read_bytes() == original
    assert first.read_bytes() == prior_first
    assert json.loads(second.read_text())['reason'] == 'An independent writer revised this reason.'
    assert proof.read_bytes() == prior_proof
    assert not (tmp_path / 'docs/archive/knowledge' / f'{name}.json').exists()


def test_finalization_failure_keeps_external_edit_after_own_write(tmp_path, monkeypatch, capsys):
    from tools.repoctl import knowledge_migration as migration
    from tools.repoctl.io import RepoctlError
    from tools.repoctl.knowledge_records import _knowledge_record_digest

    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name = 'K-20260609184045Z--rollback-external-edit'
    old = _legacy(tmp_path, name)
    original = old.read_bytes()
    assert main(['knowledge', 'add', '--repo-id', 'main', '--kind', 'decision',
                 '--claim', 'Keep durable keys on every retry.',
                 '--reason', 'The verified retry test shows stable keys.',
                 '--source', f'docs/knowledge/records/{name}.json', '--json']) == 0
    current_id = _out(capsys)['data']['record']['id']
    assert main(['knowledge', 'migrate', '--repo-id', 'main', '--record-id', name,
                 '--apply', '--json']) == 0
    _out(capsys)
    current_path = tmp_path / 'docs/knowledge/records' / f'{current_id}.json'
    manifest = tmp_path / 'docs/tasks/.repoctl-state/knowledge-migrations' / name / 'migration.json'
    original_proof = manifest.read_bytes()
    original_write = migration.atomic_write
    external_bytes = None

    def fail_manifest_after_external_edit(path, body):
        nonlocal external_bytes
        if path == manifest:
            record = json.loads(current_path.read_text())
            record['reason'] = 'Externally edited after finalizer wrote this v2.'
            record['record_digest'] = _knowledge_record_digest(record)
            external_bytes = (json.dumps(record, indent=2, sort_keys=True) + '\n').encode()
            current_path.write_bytes(external_bytes)
            raise OSError('manifest write failed after external v2 edit')
        return original_write(path, body)

    monkeypatch.setattr(migration, 'atomic_write', fail_manifest_after_external_edit)
    with pytest.raises(RepoctlError) as caught:
        migration.finalize_legacy_knowledge(tmp_path, repo_id='main', record_id=name, apply=True)
    assert caught.value.code == 'knowledge_migration_rollback_incomplete'
    assert current_path.read_bytes() == external_bytes
    assert old.read_bytes() == original
    assert (tmp_path / 'docs/archive/knowledge' / f'{name}.json').read_bytes() == original
    assert manifest.read_bytes() == original_proof


def test_missing_reason_or_source_requires_real_verified_input(tmp_path,monkeypatch,capsys):
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name='K-20260609184045Z--needs-reason'
    path=_legacy(tmp_path,name)
    original=path.read_bytes()
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json'])==1
    out=_out(capsys)
    assert out['data']['records'][0]['status']=='needs_reason'
    assert path.read_bytes()==original
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,
                 '--reason','Retries can repeat side effects.',
                 '--source','docs/contracts/repoctl-evidence-contract.md','--apply','--json'])==0
    assert _out(capsys)['data']['applied_count']==1
    assert path.read_bytes()==original
    assert any(json.loads(f.read_text()).get('schema_version')==2 for f in (tmp_path/'docs/knowledge/records').glob('K-*.json'))


def test_equivalent_current_keeps_legacy_original_as_historical_source(tmp_path,monkeypatch,capsys):
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    current=_add_knowledge_source(capsys, claim='Keep durable keys on every retry.')['data']['record']
    name='K-20260609184045Z--equivalent'
    path=_legacy(tmp_path,name,reason=current['reason'],sources=[current['source_refs'][0]['path']])
    original=path.read_bytes()
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json'])==0
    data=_out(capsys)
    assert data['data']['records'][0]['destination_id']==current['id']
    assert path.read_bytes()==original
    assert (tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'/name/'original.json').read_bytes()==original


def test_failure_while_writing_migration_leaves_original_and_no_claimed_proof(tmp_path,monkeypatch):
    from tools.repoctl import knowledge_migration as migration
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name='K-20260609184045Z--write-fails'
    path=_legacy(tmp_path,name,reason='Retries can repeat side effects.',sources=['docs/contracts/repoctl-evidence-contract.md'])
    before=path.read_bytes()
    original=migration.atomic_write
    calls=0
    def failed_write(path,body):
        nonlocal calls
        calls+=1
        if calls==2:raise OSError('simulated interrupted witness write')
        return original(path,body)
    monkeypatch.setattr(migration,'atomic_write',failed_write)
    with pytest.raises(OSError,match='interrupted'):
        migration.migrate_legacy_knowledge(tmp_path,repo_id='main',record_id=name,apply=True)
    assert path.read_bytes()==before
    proof=tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'/name
    assert not (proof/'original.json').exists() and not (proof/'migration.json').exists()


def test_equivalent_current_record_referencing_legacy_id_needs_review(tmp_path, monkeypatch, capsys) -> None:
    from tools.repoctl.knowledge_records import _knowledge_record_digest
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    current=_add_knowledge_source(capsys, claim='Keep durable keys on every retry.')['data']['record']
    name='K-20260609184045Z--referenced-by-current'
    legacy=_legacy(tmp_path,name,reason=current['reason'],sources=[current['source_refs'][0]['path']])
    curpath=tmp_path/'docs/knowledge/records'/f"{current['id']}.json"
    body=json.loads(curpath.read_text())
    body['replaces']=[name]
    body['record_digest']=_knowledge_record_digest(body)
    curpath.write_text(json.dumps(body,sort_keys=True,indent=2)+'\n')
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--json'])==0
    output=_out(capsys)
    assert output['data']['records'][0]['status']=='reuse_existing_v2'
    # A staged v1 replacement is not a supported normal schema: the partial
    # migration must not silently restore runtime legacy-id compatibility.
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json'])==1
    applied=_out(capsys)
    assert applied['data']['records'][0]['status']=='applied_reuse_existing_v2'
    assert applied['data']['projection_problems']==['knowledge_replaces_missing']
    assert legacy.is_file()
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--finalize','--apply','--json'])==0
    assert _out(capsys)['data']['records'][0]['status']=='applied_finalized'
    assert not legacy.exists()
    assert (tmp_path/f'docs/archive/knowledge/{name}.json').is_file()
    after=json.loads(curpath.read_text())
    assert name not in after['replaces']
    assert main(['knowledge','check','--repo-id','main','--json'])==0
    assert _out(capsys)['data']['legacy_record_count']==0


def test_different_current_claim_citing_legacy_original_blocks_in_place_mutation(tmp_path, monkeypatch, capsys):
    """Real adopter regression: a v2 conclusion cites a v1 JSON by digest.

    v1/v2 claims can differ while v2 explicitly treats v1's immutable bytes as
    its source; a successful-looking in-place rewrite invalidates that proof.
    """
    from tools.repoctl.knowledge_records import _sha256_text
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name = 'K-20260609184045Z--original-evidence'
    legacy = _legacy(
        tmp_path, name,
        reason='The old runtime retries at least once.',
        sources=['docs/contracts/repoctl-evidence-contract.md'],
    )
    before = legacy.read_bytes()
    assert main([
        'knowledge','add','--repo-id','main','--kind','decision',
        '--claim','The new retry architecture must retain one idempotency key.',
        '--reason','This decision depends on an original recorded invariant.',
        '--source',f'docs/knowledge/records/{name}.json','--json',
    ]) == 0
    existing = _out(capsys)['data']['record']
    source_ref = next(x for x in existing['source_refs'] if x['path'].endswith(name + '.json'))
    assert source_ref['content_sha256'] == _sha256_text(before.decode('utf-8'))
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--json']) == 0
    preview = _out(capsys)['data']['records'][0]
    assert preview['status'] == 'create_v2'
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json']) == 0
    applied = _out(capsys)
    assert applied['data']['applied_count'] == 1
    assert applied['data']['records'][0]['destination_id'] != name
    assert legacy.read_bytes() == before
    assert source_ref['content_sha256'] == _sha256_text(legacy.read_text())
    assert (tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'/name/'original.json').read_bytes() == before
    assert main(['knowledge','check','--repo-id','main','--json'])==0
    checked=_out(capsys)
    assert checked['data']['legacy_record_count'] == 1
    assert not any(p['code']=='knowledge_source_changed' for p in checked['problems'])


def test_tampered_preservation_proof_restores_migration_warning(tmp_path, monkeypatch, capsys) -> None:
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name = 'K-20260609184045Z--proof-tamper'
    path = _legacy(tmp_path, name, reason='Actual retry loop was observed.',
                   sources=['docs/contracts/repoctl-evidence-contract.md'])
    original = path.read_bytes()
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json'])==0
    _out(capsys)
    proof=tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'/name/'migration.json'
    before=json.loads(proof.read_text())
    before['legacy_sha256']='sha256:'+'0'*64
    proof.write_text(json.dumps(before)+'\n')
    assert main(['knowledge','check','--repo-id','main','--json'])==0
    result=_out(capsys)
    assert result['data']['legacy_record_count']==1
    assert any(p['code']=='knowledge_record_migration_required' for p in result['problems'])
    assert path.read_bytes()==original
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json'])==1
    assert _out(capsys)['data']['records'][0]['status']=='needs_proof_review'


def test_migration_projection_rebuild_failure_is_not_reported_as_success(tmp_path, monkeypatch, capsys) -> None:
    from tools.repoctl.tasks import Problem
    from tools.repoctl import knowledge_migration

    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name = 'K-20260609184045Z--projection-failure'
    path = _legacy(tmp_path, name, reason='A concrete saved explanation.',
                   sources=['docs/contracts/repoctl-evidence-contract.md'])
    original = path.read_bytes()
    monkeypatch.setattr(
        knowledge_migration, 'rebuild_knowledge_projection',
        lambda *a, **k: ({}, [Problem('error','knowledge_projection_unavailable','simulated rebuild failure')]),
    )
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json']) == 1
    output = _out(capsys)
    assert output['ok'] is False
    assert output['data']['applied_count'] == 1
    assert any(problem['code']=='knowledge_migration_projection_rebuild_failed' for problem in output['problems'])
    assert path.read_bytes() == original
    assert main(['knowledge','check','--repo-id','main','--json']) == 0
    assert _out(capsys)['data']['legacy_record_count'] == 1


def test_existing_v2_exactly_citing_v1_claim_can_preserve_stale_historical_source(tmp_path, monkeypatch, capsys) -> None:
    """Real Bible Simple shape: old archived Task digest drift; v2 cites intact v1.

    This validates a pre-existing successor's own human-recorded reason rather
    than rebinding v1 provenance or inventing a reason for an old artifact.
    """
    from tools.repoctl.knowledge_migration import preserved_legacy_migration_witness
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    old_id='K-20260609184045Z--legacy-source-changed'
    old=_legacy(tmp_path,old_id)
    body=json.loads(old.read_text())
    body['summary']='Old invariant was recorded before archive reorganization.'
    body['source_refs']=[{
        'path':'docs/archive/tasks/T-20260609120000Z--historical-task.md',
        'content_sha256':'sha256:'+'0'*64,
    }]
    old.write_text(json.dumps(body,indent=2,sort_keys=True)+'\n')
    unchanged=old.read_bytes()
    assert main(['knowledge','add','--repo-id','main','--kind','decision',
        '--claim',body['claim'],'--reason','A later verified incident makes this rule explicit.',
        '--source',f'docs/knowledge/records/{old_id}.json','--json'])==0
    current=_out(capsys)['data']['record']
    assert current['id']!=old_id
    assert main(['knowledge','migrate','--repo-id','main','--record-id',old_id,'--json'])==0
    preview=_out(capsys)['data']['records'][0]
    assert preview['status']=='link_cited_current_v2'
    assert preview['destination_id']==current['id']
    assert main(['knowledge','migrate','--repo-id','main','--record-id',old_id,'--apply','--json'])==0
    applied=_out(capsys)['data']
    assert applied['records'][0]['status']=='applied_link_cited_current_v2'
    assert applied['projection_problems']==[]
    assert old.read_bytes()==unchanged
    assert preserved_legacy_migration_witness(tmp_path,json.loads(old.read_text()))
    assert main(['knowledge','check','--repo-id','main','--json'])==0
    checked=_out(capsys)['data']
    assert checked['legacy_record_count']==1
    assert main(['knowledge','migrate','--repo-id','main','--record-id',old_id,'--json'])==0
    assert _out(capsys)['data']['records'][0]['status']=='already_preserved'
    witness=tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'/old_id/'original.json'
    assert witness.read_bytes()==unchanged


def test_multiple_current_v2_citing_old_claim_does_not_pick_arbitrary_successor(tmp_path,monkeypatch,capsys) -> None:
    _setup_knowledge_workspace(tmp_path,monkeypatch)
    old_id='K-20260609184045Z--ambiguous-history'
    old=_legacy(tmp_path,old_id)
    for index in (1,2):
        assert main(['knowledge','add','--repo-id','main','--kind','decision',
            '--claim','Keep durable keys on every retry.',
            '--reason',f'Distinct evidence-backed reason {index}.',
            '--source',f'docs/knowledge/records/{old_id}.json','--json'])==0
        _out(capsys)
    unchanged=old.read_bytes()
    assert main(['knowledge','migrate','--repo-id','main','--record-id',old_id,'--apply','--json'])==1
    result=_out(capsys)['data']
    assert result['records'][0]['status']=='needs_equivalence_review'
    assert result['applied_count']==0
    assert old.read_bytes()==unchanged
    assert not (tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'/old_id/'migration.json').exists()


def test_linked_v2_source_digest_mismatch_never_suppresses_legacy_warning(tmp_path,monkeypatch,capsys) -> None:
    from tools.repoctl.knowledge_records import _knowledge_record_digest
    _setup_knowledge_workspace(tmp_path,monkeypatch)
    old_id='K-20260609184045Z--tampered-successor-source'
    old=_legacy(tmp_path,old_id)
    assert main(['knowledge','add','--repo-id','main','--kind','decision',
        '--claim','Keep durable keys on every retry.',
        '--reason','Saved actual technical reason.',
        '--source',f'docs/knowledge/records/{old_id}.json','--json'])==0
    current=_out(capsys)['data']['record']
    current_path=tmp_path/'docs/knowledge/records'/f'{current["id"]}.json'
    data=json.loads(current_path.read_text())
    data['source_refs'][0]['content_sha256']='sha256:'+'f'*64
    data['record_digest']=_knowledge_record_digest(data)
    current_path.write_text(json.dumps(data,sort_keys=True,indent=2)+'\n')
    assert main(['knowledge','migrate','--repo-id','main','--record-id',old_id,'--apply','--json'])==1
    result=_out(capsys)['data']
    assert result['records'][0]['status'].startswith('needs_')
    assert result['applied_count']==0
    assert old.is_file()


def test_existing_v2_can_narrow_scope_but_migration_reports_original_paths(tmp_path,monkeypatch,capsys) -> None:
    _setup_knowledge_workspace(tmp_path,monkeypatch)
    (tmp_path/'repos/app.py').write_text('print("current product file")\n',encoding='utf-8')
    old_id='K-20260609184045Z--retired-file-scope'
    old=_legacy(tmp_path,old_id)
    body=json.loads(old.read_text())
    body['applies_to']={'paths':['app.py','old_deleted.py']}
    old.write_text(json.dumps(body,sort_keys=True,indent=2)+'\n')
    original=old.read_bytes()
    assert main(['knowledge','add','--repo-id','main','--kind','decision',
        '--claim',body['claim'],'--reason','The existing application file is the current owned scope.',
        '--source',f'docs/knowledge/records/{old_id}.json',
        '--applies-to','app.py','--json'])==0
    _out(capsys)
    assert main(['knowledge','migrate','--repo-id','main','--record-id',old_id,'--json'])==0
    row=_out(capsys)['data']['records'][0]
    assert row['status']=='link_cited_current_v2'
    assert row['removed_historical_scope_paths']==['old_deleted.py']
    assert main(['knowledge','migrate','--repo-id','main','--record-id',old_id,'--apply','--json'])==0
    _out(capsys)
    assert old.read_bytes()==original
    assert main(['knowledge','check','--repo-id','main','--json'])==0
    assert _out(capsys)['data']['legacy_record_count']==1


def test_existing_v2_broadening_scope_requires_separate_review(tmp_path,monkeypatch,capsys) -> None:
    _setup_knowledge_workspace(tmp_path,monkeypatch)
    (tmp_path/'repos/app.py').write_text('print("current product file")\n',encoding='utf-8')
    old_id='K-20260609184045Z--scope-broadening'
    old=_legacy(tmp_path,old_id)
    before=old.read_bytes()
    assert main(['knowledge','add','--repo-id','main','--kind','decision',
        '--claim','Keep durable keys on every retry.',
        '--reason','A separate current application now participates.',
        '--source',f'docs/knowledge/records/{old_id}.json',
        '--applies-to','app.py','--json'])==0
    _out(capsys)
    assert main(['knowledge','migrate','--repo-id','main','--record-id',old_id,'--apply','--json'])==1
    row=_out(capsys)['data']['records'][0]
    assert row['status'].startswith('needs_')
    assert old.read_bytes()==before
    assert not (tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'/old_id/'migration.json').exists()


def test_link_does_not_swallow_explicit_reason_override(tmp_path,monkeypatch,capsys) -> None:
    _setup_knowledge_workspace(tmp_path,monkeypatch)
    old_id='K-20260609184045Z--override-not-ignored'
    old=_legacy(tmp_path,old_id)
    assert main(['knowledge','add','--repo-id','main','--kind','decision',
        '--claim','Keep durable keys on every retry.',
        '--reason','Previously authored reviewed reason.',
        '--source',f'docs/knowledge/records/{old_id}.json','--json'])==0
    _out(capsys)
    assert main(['knowledge','migrate','--repo-id','main','--record-id',old_id,
        '--reason','Different independently supplied reason.',
        '--source','docs/contracts/repoctl-evidence-contract.md','--json'])==0
    rows=_out(capsys)['data']['records']
    assert rows[0]['status']!='link_cited_current_v2'
    assert rows[0]['status']=='create_v2'
    assert old.is_file()


def test_knowledge_migration_proof_parent_symlink_never_writes_external(tmp_path,monkeypatch,capsys) -> None:
    from tools.repoctl.io import RepoctlError
    from tools.repoctl.knowledge_migration import migrate_legacy_knowledge
    _setup_knowledge_workspace(tmp_path,monkeypatch)
    old_id='K-20260609184045Z--proof-symlink'
    old=_legacy(tmp_path,old_id,reason='Actual technical evidence.',sources=['docs/contracts/repoctl-evidence-contract.md'])
    raw=old.read_bytes()
    outside=tmp_path.parent/(tmp_path.name+'-external-migration-proof-target')
    outside.mkdir(exist_ok=True)
    parent=tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'
    parent.parent.mkdir(parents=True,exist_ok=True)
    parent.symlink_to(outside,target_is_directory=True)
    with pytest.raises(RepoctlError,match='symlink'):
        migrate_legacy_knowledge(tmp_path,repo_id='main',record_id=old_id,apply=True)
    assert old.read_bytes()==raw
    assert not list(outside.glob('K-*'))

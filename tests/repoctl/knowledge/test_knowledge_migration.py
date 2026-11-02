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
    assert _out(capsys)['data']['legacy_record_count']==0


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
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json'])==0
    applied=_out(capsys)
    assert applied['data']['records'][0]['status']=='applied_reuse_existing_v2'
    assert legacy.is_file()
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
    assert checked['data']['legacy_record_count'] == 0
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
    assert _out(capsys)['data']['legacy_record_count'] == 0

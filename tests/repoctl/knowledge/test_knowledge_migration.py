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


def test_one_time_migration_in_place_preserves_id_original_and_source(tmp_path, monkeypatch, capsys):
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    name = 'K-20260609184045Z--idempotence'
    source = 'docs/contracts/repoctl-evidence-contract.md'
    path = _legacy(tmp_path,name,reason='Duplicate notification would charge the account twice.',sources=[source])
    original = path.read_bytes()
    assert main(['knowledge','migrate','--repo-id','main','--json'])==0
    dry=_out(capsys)
    assert dry['data']['records'][0]['status']=='convert_in_place'
    assert path.read_bytes()==original
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json'])==0
    data=_out(capsys)
    assert data['data']['applied_count']==1
    record=json.loads(path.read_text())
    assert record['id']==name and record['schema_version']==2
    assert record['reason']=='Duplicate notification would charge the account twice.'
    assert record['source_refs'][0]['path']==source
    witness=tmp_path/'docs/tasks/.repoctl-state/knowledge-migrations'/name
    assert (witness/'original.json').read_bytes()==original
    assert main(['knowledge','query','durable keys','--repo-id','main','--json'])==0
    assert _out(capsys)['data']['results'][0]['record']['id']==name
    assert main(['knowledge','migrate','--repo-id','main','--json'])==0
    assert _out(capsys)['data']['record_count']==0


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
    assert json.loads(path.read_text())['schema_version']==2


def test_equal_existing_current_collapses_only_without_historical_references(tmp_path,monkeypatch,capsys):
    _setup_knowledge_workspace(tmp_path, monkeypatch)
    current=_add_knowledge_source(capsys, claim='Keep durable keys on every retry.')['data']['record']
    name='K-20260609184045Z--equivalent'
    path=_legacy(tmp_path,name,reason=current['reason'],sources=[current['source_refs'][0]['path']])
    original=path.read_bytes()
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--apply','--json'])==0
    data=_out(capsys)
    assert data['data']['records'][0]['destination_id']==current['id']
    assert not path.exists()
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
    assert main(['knowledge','migrate','--repo-id','main','--record-id',name,'--json'])==1
    output=_out(capsys)
    assert output['data']['records'][0]['status']=='needs_reference_review'
    assert curpath.relative_to(tmp_path).as_posix() in output['data']['records'][0]['references']
    assert legacy.is_file()

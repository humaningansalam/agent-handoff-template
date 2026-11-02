from __future__ import annotations

import json
from pathlib import Path

from tests.repoctl.graph.test_graph_build import _materialize
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo
from tools.repoctl.cli import main


def _workspace(tmp_path: Path, monkeypatch) -> Path:
    write_workspace(tmp_path)
    repo = tmp_path / 'repos'
    init_repo(repo)
    write_repometa(repo)
    monkeypatch.setattr('tools.repoctl.cli.find_workspace_root', lambda: tmp_path)
    return repo


def _payload(capsys) -> dict:
    return json.loads(capsys.readouterr().out)


def _write(repo: Path, path: str, text: str) -> None:
    dest = repo / path
    dest.parent.mkdir(parents=True, exist_ok=True)
    dest.write_text(text, encoding='utf-8')


def _python_route_fixture(repo: Path) -> None:
    _write(repo, 'src/api/model_routes.py', '''from src.core.model_manager import ModelManager
class Router:
    def route(self, *a, **kw):
        return lambda f: f
bp = Router()
@bp.route('/predict', methods=['POST'])
def predict():
    manager = ModelManager()
    return manager.predict()
''')
    _write(repo, 'src/core/model_manager.py', '''class ModelManager:
    def predict(self):
        return 42
''')
    _write(repo, 'tests/test_predict.py', '''def test_predict_route(client):
    response = client.post('/predict', json={})
    assert response.status_code == 200
''')
    _write(repo, 'tests/test_model_manager_concurrency.py', '''from src.core.model_manager import ModelManager

def test_predict_concurrency():
    assert ModelManager().predict() == 42
''')


def test_http_route_workflow_links_source_candidate_and_confirmed_test(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _python_route_fixture(repo)
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--route', '/predict', '--json']) == 0
    data = _payload(capsys)
    result = data['data']['result']
    assert result['status'] == 'partial'
    assert [x['path'] for x in result['stages']['entrypoints']] == ['src/api/model_routes.py']
    assert any(x['path'] == 'src/core/model_manager.py' and x['relation'] == 'candidate' for x in result['stages']['implementation'])
    assert any(x['path'] == 'tests/test_predict.py' and x['relation'] == 'candidate' for x in result['stages']['tests'])
    assert any(x['path'] == 'tests/test_model_manager_concurrency.py' and x['relation'] == 'confirmed' for x in result['stages']['tests'])
    assert any(x['to'] == 'src/core/model_manager.py' and x['relation'] == 'candidate' for x in result['links'])
    assert any('not provider-confirmed' in w for w in result['warnings'])
    assert main(['graph', 'workflow', '--route', '/predict', '--in-file', 'src/api/model_routes.py', '--json']) == 0
    scoped = _payload(capsys)['data']['result']
    assert any(x['path'] == 'tests/test_predict.py' for x in scoped['stages']['tests'])


def test_route_ambiguity_fails_closed_without_selecting_an_arbitrary_handler(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    for name in ('first', 'second'):
        _write(repo, f'src/api/{name}.py', "@router.get('/health')\ndef status():\n    return 'ok'\n")
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--route', '/health', '--json']) == 1
    result = _payload(capsys)['data']['result']
    assert result['status'] == 'ambiguous'
    assert {x['path'] for x in result['entrypoints']} == {'src/api/first.py', 'src/api/second.py'}
    assert main(['graph', 'workflow', '--route', '/health', '--in-file', 'src/api/first.py', '--json']) == 0
    chosen = _payload(capsys)['data']['result']
    assert chosen['stages']['entrypoints'][0]['path'] == 'src/api/first.py'


def test_rust_module_and_embedded_test_are_partial_source_evidence(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'crates/daemon/src/audio_clock.rs', 'pub struct AudioClockDriftEstimator;\n')
    _write(repo, 'crates/core/src/lib.rs', '''pub fn bridge_frames() { }
#[test]
fn host_audio_clock_default_contract() { assert!(true); }
''')
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--symbol', 'audio_clock', '--json']) == 0
    result = _payload(capsys)['data']['result']
    assert result['status'] == 'partial'
    assert {'crates/daemon/src/audio_clock.rs', 'crates/core/src/lib.rs'} <= {
        x['path'] for x in result['stages']['implementation']
    }
    assert any(x['path'] == 'crates/core/src/lib.rs' and x['relation'] == 'candidate' for x in result['stages']['tests'])
    assert result['completeness']['precision'] == 'partial'


def test_stale_empty_graph_uses_changed_source_and_refresh_restores_precision(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'README.txt', 'initial repository\n')
    _materialize(tmp_path)
    _write(repo, 'src/main.py', 'def start():\n    return 42\n')
    assert main(['graph', 'workflow', '--symbol', 'start', '--json']) == 0
    stale = _payload(capsys)
    assert stale['data']['query_status'] == 'partial'
    assert stale['data']['freshness']['status'] == 'stale'
    assert any(x['path'] == 'src/main.py' and x['relation'] == 'candidate' for x in stale['data']['result']['stages']['implementation'])
    assert stale['data']['result']['stages']['tests'] == []
    _materialize(tmp_path)
    assert main(['graph', 'workflow', '--symbol', 'start', '--json']) == 0
    refreshed = _payload(capsys)
    assert any(x['path'] == 'src/main.py' and x['relation'] == 'confirmed' for x in refreshed['data']['result']['stages']['implementation'])
    assert refreshed['data']['query_status'] == 'partial'  # no test evidence is not zero-impact


def test_workflow_benchmark_measures_paths_and_provenance_not_edges(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _python_route_fixture(repo)
    _materialize(tmp_path)
    fixture = tmp_path / 'case.json'
    fixture.write_text(json.dumps({
        'schema': 'repoctl.graph.workflow-benchmark',
        'cases': [{
            'id': 'real-route', 'repo_id': 'main', 'selector': {'route': '/predict'},
            'expected_paths': {'entrypoints': ['src/api/model_routes.py'],
                               'implementation': ['src/core/model_manager.py'],
                               'tests': ['tests/test_predict.py', 'tests/test_model_manager_concurrency.py']},
            'expected_statuses': ['partial'],
        }],
    }), encoding='utf-8')
    assert main(['graph', 'workflow-benchmark', '--case-id', 'real-route', '--fixture', str(fixture), '--min-path-recall', '1', '--json']) == 0
    result = _payload(capsys)['data']['benchmark']
    assert result['path_recall'] == 1.0
    assert result['confirmed_path_recall'] < result['path_recall']
    assert result['status_expected'] is True
    assert result['stages']['tests']['found_count'] == 2
    assert result['stages']['tests']['confirmed_count'] >= 1

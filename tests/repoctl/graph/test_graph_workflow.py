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
            'required_stages': ['entrypoint', 'implementation', 'tests'],
        }],
    }), encoding='utf-8')
    assert main(['graph', 'workflow-benchmark', '--case-id', 'real-route', '--fixture', str(fixture), '--min-path-recall', '1', '--json']) == 1
    result = _payload(capsys)['data']['benchmark']
    assert result['path_recall'] == 1.0
    assert result['confirmed_path_recall'] < result['path_recall']
    assert result['status_expected'] is True
    assert result['stages']['tests']['found_count'] == 2
    assert result['stages']['tests']['confirmed_count'] >= 1
    assert result['required_stages_covered'] is True
    assert result['required_stages_precise'] is False
    assert result['task_navigation_complete'] is False


def test_workflow_exact_file_precedes_two_thousand_other_entries(tmp_path, monkeypatch) -> None:
    from tools.repoctl.graph_model import GraphNode, GraphSnapshot, file_id
    from tools.repoctl.graph_workflow import query_graph_workflow
    from tools.repoctl.repositories import require_repo_target

    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'z_special.py', "@router.post('/predict')\ndef predict():\n    return 'ok'\n")
    files = [f'a/file{i:04}.py' for i in range(2048)] + ['z_special.py']
    snap = GraphSnapshot(repository={'id': 'main', 'path': 'repos'}, sources=[], completeness={},
        nodes=[GraphNode(file_id('main', path), 'file', {'path': path}) for path in files], edges=[])
    target = require_repo_target(tmp_path, repo_id='main')
    result = query_graph_workflow(snap, root=tmp_path, target=target,
        route='/predict', in_file='z_special.py', freshness={'status': 'stale'})
    assert result['stages']['entrypoints'][0]['path'] == 'z_special.py'
    assert result['completeness']['files_scanned'] == 1
    assert result['completeness']['source_scan_bounded'] is False


def test_workflow_unscored_caller_fails_even_with_full_path_recall() -> None:
    from tools.repoctl.graph_workflow import evaluate_workflow_benchmark
    result = {'status': 'partial', 'stages': {
        'entrypoints': [{'path': 'entry.py', 'relation': 'source'}],
        'implementation': [{'path': 'engine.py', 'relation': 'confirmed'}],
        'tests': [{'path': 'test_engine.py', 'relation': 'confirmed'}],
        'callers_or_dependents': [],
    }}
    case = {'id':'unscored-caller', 'expected_statuses':['partial'],
        'required_stages':['entrypoint','implementation','callers_or_dependents','tests'],
        'expected_paths': {'entrypoints':['entry.py'], 'implementation':['engine.py'], 'tests':['test_engine.py']}}
    score = evaluate_workflow_benchmark(result, case)
    assert score['path_recall'] == 1.0
    assert score['required_stages']['callers_or_dependents']['status'] == 'unscored_no_ground_truth'
    assert score['required_stages_covered'] is False
    assert score['task_navigation_complete'] is False


def test_workflow_full_required_relation_evidence_can_pass() -> None:
    from tools.repoctl.graph_workflow import evaluate_workflow_benchmark
    result = {'status':'found', 'stages': {
        'entrypoints': [{'path':'route.py','relation':'source'}],
        'implementation':[{'path':'engine.py','relation':'confirmed'}],
        'callers_or_dependents':[{'path':'caller.py','relation':'confirmed'}],
        'tests':[{'path':'test_engine.py','relation':'confirmed'}],
    }}
    case = {'id':'verified', 'expected_statuses':['found'],
        'required_stages':['entrypoint','implementation','callers_or_dependents','tests'],
        'expected_paths': {'entrypoints':['route.py'], 'implementation':['engine.py'],
                           'callers_or_dependents':['caller.py'], 'tests':['test_engine.py']}}
    score = evaluate_workflow_benchmark(result, case)
    assert score['task_navigation_complete'] is True
    assert score['required_stages_precise'] is True
    assert score['false_positive_count'] == 0
    assert score['confirmed_path_recall'] == 0.75


def test_dart_exact_seed_recovers_live_sibling_importer_without_inventing_a_call(tmp_path, monkeypatch) -> None:
    from tools.repoctl.graph_model import GraphNode, GraphSnapshot, file_id
    from tools.repoctl.graph_workflow import query_graph_workflow
    from tools.repoctl.repositories import require_repo_target

    repo = _workspace(tmp_path, monkeypatch)
    owner = 'lib/runtime/local_store/local_restaurant_order_authority.dart'
    consumer = 'lib/runtime/local_store/local_waitlist_authority.dart'
    _write(repo, owner, 'extension LocalRestaurantOrderAuthority on LocalWaitlistAuthority {}\n')
    _write(repo, consumer, "import 'local_restaurant_order_authority.dart';\nclass LocalWaitlistAuthority {}\n")
    files = [owner, consumer]
    snapshot = GraphSnapshot(repository={'id': 'main', 'path': 'repos'}, sources=[], completeness={},
        nodes=[GraphNode(file_id('main', path), 'file', {'path': path}) for path in files], edges=[])
    result = query_graph_workflow(snapshot, root=tmp_path, target=require_repo_target(tmp_path, repo_id='main'),
        symbol='LocalRestaurantOrderAuthority', in_file=owner,
        freshness={'status':'stale', 'changed_paths':files})
    assert result['status'] == 'partial'
    assert result['stages']['implementation'][0]['path'] == owner
    assert any(item['path'] == consumer and item['relation'] == 'source'
               and item['evidence'] == 'current Dart module import/export literal'
               for item in result['stages']['callers_or_dependents'])
    assert not any(edge['relation'] == 'confirmed' for edge in result['links'])


def test_stale_dart_entrypoint_has_dart_source_provenance_not_rust(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _write(repo, 'README.txt', 'empty initial repo\n')
    _materialize(tmp_path)
    _write(repo, 'lib/main.dart', 'void main() => boot();\nvoid boot() {}\n')
    assert main(['graph', 'workflow', '--symbol', 'main', '--in-file', 'lib/main.dart', '--json']) == 0
    result = _payload(capsys)['data']['result']
    assert result['status'] == 'partial'
    impl = next(item for item in result['stages']['implementation'] if item['path'] == 'lib/main.dart')
    assert impl['relation'] == 'source'
    assert impl['evidence'] == 'Dart top-level function declaration'
    assert 'Rust' not in impl['evidence']
    assert result['stages']['tests'] == []

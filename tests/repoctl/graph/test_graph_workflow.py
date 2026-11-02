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


def test_rust_module_precision_still_discovers_embedded_test_in_another_crate(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'crates/daemon/src/main.rs','mod audio_clock;\nfn main(){}\n')
    _write(repo,'crates/daemon/src/audio_clock.rs','pub struct AudioClockDriftEstimator;\n')
    _write(repo,'crates/core/src/lib.rs','''#[test]
fn host_audio_clock_default_contract_matches_values() { assert!(true); }
''')
    _materialize(tmp_path)
    assert main(['graph','workflow','--symbol','audio_clock','--repo-id','main','--json']) == 0
    result=_payload(capsys)['data']['result']
    assert any(item['path']=='crates/daemon/src/audio_clock.rs' and item['relation']=='confirmed'
               for item in result['stages']['implementation'])
    assert any(item['path']=='crates/daemon/src/main.rs' and item['relation']=='confirmed'
               for item in result['stages']['callers_or_dependents'])
    assert any(item['path']=='crates/core/src/lib.rs' and item['relation']=='candidate'
               for item in result['stages']['tests'])
    assert result['status']=='partial'


def test_dart_exact_part_of_chain_connects_library_caller_and_test(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'pubspec.yaml','name: app\n')
    _write(repo,'lib/src/authority.dart',"part 'authority_part.dart';\nclass Authority {}\n")
    _write(repo,'lib/src/authority_part.dart',"part of 'authority.dart';\nclass AuthorityPart {}\n")
    _write(repo,'test/authority_test.dart',"import 'package:app/src/authority.dart';\nvoid main() {}\n")
    _materialize(tmp_path)
    assert main(['graph','workflow','--symbol','AuthorityPart','--in-file','lib/src/authority_part.dart','--json'])==0
    payload=_payload(capsys)
    result=payload['data']['result']
    assert payload['data']['freshness']['status']=='current'
    assert any(item['path']=='lib/src/authority_part.dart' and item['relation']=='confirmed'
               for item in result['stages']['implementation'])
    assert any(item['path']=='lib/src/authority.dart' and item['relation']=='confirmed'
               for item in result['stages']['callers_or_dependents'])
    assert any(item['path']=='test/authority_test.dart' and item['relation']=='confirmed'
               for item in result['stages']['tests'])


def test_dart_invalid_part_of_never_creates_confirmed_part_dependency(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'lib/authority.dart',"part 'authority_part.dart';\nclass Authority {}\n")
    _write(repo,'lib/authority_part.dart',"part of 'other_library.dart';\nclass AuthorityPart {}\n")
    _materialize(tmp_path)
    assert main(['graph','workflow','--symbol','AuthorityPart','--in-file','lib/authority_part.dart','--json'])==0
    result=_payload(capsys)['data']['result']
    assert not any(item['path']=='lib/authority.dart' and item['relation']=='confirmed'
                   for item in result['stages']['callers_or_dependents'])


def test_dart_part_of_is_not_itself_a_reverse_import(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'lib/authority.dart',"part 'authority_part.dart';\nclass Authority {}\n")
    _write(repo,'lib/authority_part.dart',"part of 'authority.dart';\nclass AuthorityPart {}\n")
    _materialize(tmp_path)
    from tools.repoctl.repositories import require_repo_target
    from tools.repoctl.graph_store import load_materialized_graph
    snapshot,problems,meta=load_materialized_graph(tmp_path,target=require_repo_target(tmp_path,repo_id='main'))
    assert snapshot and not problems
    node_paths={n.id:n.identity.get('path') for n in snapshot.nodes if n.kind=='file'}
    part_edges=[edge for edge in snapshot.edges if edge.kind=='IMPORTS_FILE' and edge.source=='dart_import_resolver'
                and edge.facts.get('match_kind')=='dart_part_directive']
    assert len(part_edges)==1
    assert node_paths[part_edges[0].from_id]=='lib/authority.dart'
    assert node_paths[part_edges[0].to_id]=='lib/authority_part.dart'


def test_dart_part_inside_comment_never_makes_a_confirmed_graph_edge(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'lib/authority.dart',"""/*
part 'authority_part.dart';
*/
class Authority {}
""")
    _write(repo,'lib/authority_part.dart',"part of 'authority.dart';\nclass AuthorityPart {}\n")
    _materialize(tmp_path)
    from tools.repoctl.repositories import require_repo_target
    from tools.repoctl.graph_store import load_materialized_graph
    snapshot,_,_=load_materialized_graph(tmp_path,target=require_repo_target(tmp_path,repo_id='main'))
    assert snapshot
    assert not any(edge.kind=='IMPORTS_FILE' and edge.source=='dart_import_resolver'
                   and edge.facts.get('match_kind')=='dart_part_directive'
                   for edge in snapshot.edges)


def test_dart_comment_and_multiline_string_directive_scan_is_bounded() -> None:
    from tools.repoctl.language_profiles import dart_directives,index_dart
    text="""import 'dart:convert';
/*
part 'fake_part.dart';
/* nested part 'fake_part.dart'; */
*/
// export 'fake_part.dart';
const sample = '''
part 'fake_part.dart';
''';
part 'real_part.dart';
"""
    assert dart_directives(text)==[('import','dart:convert'),('part','real_part.dart')]
    assert index_dart(text)[1]==['dart:convert','real_part.dart']


def test_flask_factory_wiring_is_source_proven_not_falsely_confirmed(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _python_route_fixture(repo)
    _write(repo,'src/main.py', '''from flask import Flask
from src.core.model_manager import ModelManager
from src.api.model_routes import bp as model_bp

def create_app():
    app = Flask(__name__)
    app.model_manager = ModelManager()
    app.register_blueprint(model_bp)
    return app
''')
    _materialize(tmp_path)
    assert main(['graph','workflow','--route','/predict','--repo-id','main','--json'])==0
    result=_payload(capsys)['data']['result']
    impl=next(item for item in result['stages']['implementation'] if item['path']=='src/core/model_manager.py')
    # Route fixture calls `manager.predict`, not `current_app.model_manager.predict`.
    assert impl['relation']=='candidate'
    _write(repo,'src/api/model_routes.py', '''from flask import current_app
class Router:
    def route(self,*a,**kw):
        return lambda f: f
bp=Router()
@bp.route('/predict',methods=['POST'])
def predict():
    return current_app.model_manager.predict()
''')
    _materialize(tmp_path)
    assert main(['graph','workflow','--route','/predict','--repo-id','main','--json'])==0
    result=_payload(capsys)['data']['result']
    impl=next(item for item in result['stages']['implementation'] if item['path']=='src/core/model_manager.py')
    assert impl['relation']=='source'
    assert 'dispatch unconfirmed' in impl['evidence']
    assert any(edge['from']=='src/main.py' and edge['to']=='src/core/model_manager.py'
               and edge['relation']=='source' for edge in result['links'])
    assert result['status']=='partial'


def test_benchmark_current_source_alias_requires_explicit_refresh_witness() -> None:
    from tools.repoctl.graph_workflow import evaluate_workflow_benchmark
    case={
        'id':'clean-refresh', 'required_stages':['current_source','refresh','implementation'],
        'expected_statuses':['partial'],
        'expected_paths':{'current_source':['lib/main.dart'],'implementation':['lib/main.dart']},
    }
    implementation={'path':'lib/main.dart','relation':'confirmed','evidence':'provider symbol'}
    result={'status':'partial','stages':{'implementation':[implementation],'tests':[]}}
    before=evaluate_workflow_benchmark(result,case)
    assert before['stages']['current_source']['found_count']==1
    assert before['required_stages']['current_source']['precise'] is True
    assert before['required_stages']['refresh']['status']=='not_evaluated'
    assert before['task_navigation_complete'] is False
    # A query must not fabricate that Graph was rebuilt; an independent
    # verified before/after materialization can explicitly attach this proof.
    result['refresh']={'source_rechecked':True,'status':'current'}
    after=evaluate_workflow_benchmark(result,case)
    assert after['required_stages_precise'] is True
    assert after['task_navigation_complete'] is True


def test_cops_stale_empty_then_explicit_rebuild_proves_required_refresh(tmp_path,monkeypatch,capsys) -> None:
    from tools.repoctl.graph_workflow import evaluate_workflow_benchmark
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'README.txt','initial empty source inventory\n')
    _materialize(tmp_path)
    _write(repo,'lib/main.dart','void main() => launch();\nvoid launch() {}\n')
    assert main(['graph','workflow','--symbol','main','--in-file','lib/main.dart','--repo-id','main','--json'])==0
    before=_payload(capsys)['data']
    assert before['freshness']['status']=='stale'
    assert before['result']['status']=='partial'
    assert not any(item['relation']=='confirmed' for item in before['result']['stages']['implementation'])
    _materialize(tmp_path)
    assert main(['graph','workflow','--symbol','main','--in-file','lib/main.dart','--repo-id','main','--json'])==0
    after=_payload(capsys)['data']
    assert after['freshness']['status']=='current'
    assert after['result']['stages']['implementation'][0]['relation']=='confirmed'
    case={
        'id':'cops-source-refreshed',
        'expected_statuses':['partial'],
        'required_stages':['current_source','refresh','implementation'],
        'expected_paths':{'current_source':['lib/main.dart'],'implementation':['lib/main.dart']},
    }
    score_before=evaluate_workflow_benchmark(before['result'],case)
    assert score_before['required_stages_precise'] is False
    assert score_before['task_navigation_complete'] is False
    # This proof is constructed only after the test *actually* rebuilt the
    # isolated snapshot and observed a changed source become current. Normal
    # graph workflow never claims a refresh happened without that action.
    after_result=dict(after['result'])
    after_result['refresh']={
        'status':'current',
        'source_rechecked':before['freshness']['status']=='stale' and after['freshness']['status']=='current',
    }
    score_after=evaluate_workflow_benchmark(after_result,case)
    assert score_after['required_stages_covered'] is True
    assert score_after['required_stages_precise'] is True
    assert score_after['task_navigation_complete'] is True


def test_predict_route_omits_unrelated_cleanup_test_that_only_imports_manager(tmp_path, monkeypatch, capsys) -> None:
    repo = _workspace(tmp_path, monkeypatch)
    _python_route_fixture(repo)
    _write(repo, 'tests/test_model_manager_cleanup.py', '''from src.core.model_manager import ModelManager

def test_cleanup_only():
    manager = ModelManager()
    assert manager is not None
''')
    _materialize(tmp_path)
    assert main(['graph','workflow','--route','/predict','--json']) == 0
    result = _payload(capsys)['data']['result']
    paths = {item['path'] for item in result['stages']['tests']}
    assert 'tests/test_model_manager_cleanup.py' not in paths
    assert 'tests/test_model_manager_concurrency.py' in paths
    assert 'tests/test_predict.py' in paths


def test_flask_factories_in_separate_functions_do_not_prove_one_app_wiring(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _python_route_fixture(repo)
    _write(repo,'src/api/model_routes.py', '''from flask import current_app
class Router:
    def route(self,*a,**kw):
        return lambda f: f
bp=Router()
@bp.route('/predict',methods=['POST'])
def predict():
    return current_app.model_manager.predict()
''')
    _write(repo,'src/main.py', '''from src.core.model_manager import ModelManager
from src.api.model_routes import bp as model_bp

def factory_a():
    app=object()
    app.model_manager=ModelManager()

def factory_b():
    app=object()
    app.register_blueprint(model_bp)
''')
    _materialize(tmp_path)
    assert main(['graph','workflow','--route','/predict','--json'])==0
    result=_payload(capsys)['data']['result']
    impl=next(item for item in result['stages']['implementation'] if item['path']=='src/core/model_manager.py')
    assert impl['relation']=='candidate'
    assert not any(item['from']=='src/main.py' and item['to']=='src/core/model_manager.py'
                   for item in result['links'])


def test_flask_wiring_generalizes_to_other_receiver_and_class_names(tmp_path,monkeypatch,capsys) -> None:
    repo=_workspace(tmp_path,monkeypatch)
    _write(repo,'src/api/payments.py', '''from flask import current_app
class Router:
    def route(self,*a,**kw):
        return lambda f: f
bp=Router()
@bp.route('/charge',methods=['POST'])
def charge():
    return current_app.gateway.charge()
''')
    _write(repo,'src/core/payments.py', '''class PaymentGateway:
    def charge(self):
        return 'charged'
''')
    _write(repo,'src/app.py', '''from src.api.payments import bp as payment_bp
from src.core.payments import PaymentGateway

def create_app():
    app = object()
    app.gateway = PaymentGateway()
    app.register_blueprint(payment_bp)
    return app
''')
    _materialize(tmp_path)
    assert main(['graph','workflow','--route','/charge','--json'])==0
    result=_payload(capsys)['data']['result']
    impl=next(item for item in result['stages']['implementation'] if item['path']=='src/core/payments.py')
    assert impl['relation']=='source'
    assert 'dispatch unconfirmed' in impl['evidence']
    assert any(edge['from']=='src/app.py' and edge['to']=='src/core/payments.py'
               and edge['relation']=='source' for edge in result['links'])
    assert result['status']=='partial'

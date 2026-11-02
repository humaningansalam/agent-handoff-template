from pathlib import Path

import pytest

from tools.repoctl.code_index import CodeIndexEntry
from tools.repoctl.graph_tree_sitter_provider import build_tree_sitter_semantics
from tools.repoctl.repositories import RepoTarget, RepositoryIdentitySource


@pytest.mark.parametrize('language,path,source,expected_calls', [
    ('rust','src/lib.rs','fn work(){}\nfn main(){ work(); }\n',1),
    ('go','src/main.go','package main\nfunc work(){}\nfunc main(){ work() }\n',1),
    ('java','src/Model.java','class Model { void work(){} void main(){ work(); } }\n',1),
    ('java','src/Model.kt','fun work() {}\nfun main() { work() }\n',1),
    ('swift','src/Model.swift','func work(){}\nfunc main(){ work() }\n',1),
])
def test_native_ast_definitions_and_unambiguous_same_file_calls(tmp_path: Path, language: str, path: str, source: str, expected_calls: int) -> None:
    target = RepoTarget(id='main',root_path=tmp_path,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    file = tmp_path / path
    file.parent.mkdir(parents=True,exist_ok=True)
    file.write_text(source,encoding='utf-8')
    entry = CodeIndexEntry(path,'repos/'+path,language,'source',[],[],[],[],[])
    result = build_tree_sitter_semantics(target=target,entries=[entry])
    assert {'work','main'} <= {sym.name for sym in result.symbols}
    assert len(result.calls) == expected_calls
    assert result.calls[0].scope == 'same_file'
    assert result.calls[0].anchor.path == path
    assert result.symbol_analyzed_paths == (path,)
    assert result.call_coverage.evidence_level == 'conservative'


def test_member_dispatch_and_duplicate_simple_names_never_invent_precise_calls(tmp_path: Path) -> None:
    target = RepoTarget(id='main',root_path=tmp_path,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    path='src/lib.rs'
    (tmp_path/'src').mkdir()
    (tmp_path/path).write_text('''struct A; struct B;
impl A {fn work(&self) {}} impl B {fn work(&self) {}}
fn main() { obj.work(); }
''')
    entry = CodeIndexEntry(path,'repos/'+path,'rust','source',[],[],[],[],[])
    result = build_tree_sitter_semantics(target=target,entries=[entry])
    assert len([x for x in result.symbols if x.name=='work']) == 2
    assert result.calls == ()


def test_invalid_native_source_is_failed_not_precise(tmp_path: Path) -> None:
    target = RepoTarget(id='main',root_path=tmp_path,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    path='src/lib.rs'
    (tmp_path/'src').mkdir()
    (tmp_path/path).write_text('fn unfinished( {')
    entry = CodeIndexEntry(path,'repos/'+path,'rust','source',[],[],[],[],[])
    result = build_tree_sitter_semantics(target=target,entries=[entry])
    assert result.symbol_failed_paths == (path,)
    assert result.symbols == () and result.calls == ()


def test_native_provider_materializes_graph_relations_and_source_ranges(tmp_path, monkeypatch, capsys) -> None:
    import json
    from tests.repoctl.workspace.test_check import write_workspace
    from tests.repoctl.meta.test_meta_check import write_repometa
    from tests.repoctl.repository.test_repositories import init_repo
    from tools.repoctl.cli import main

    write_workspace(tmp_path)
    repo = tmp_path / 'repos'
    init_repo(repo)
    write_repometa(repo)
    (repo / 'src').mkdir()
    (repo / 'src/lib.rs').write_text('fn work(){}\nfn main(){ work(); }\n',encoding='utf-8')
    monkeypatch.setattr('tools.repoctl.cli.find_workspace_root', lambda: tmp_path)
    assert main(['graph','build','--repo-id','main','--full','--json']) == 0
    data=json.loads(capsys.readouterr().out)
    snapshot=data['data']['snapshot']
    symbols={n['id']:n for n in snapshot['nodes'] if n['kind']=='symbol'}
    assert any(n.get('facts',{}).get('provider',{}).get('qualified_name')=='work' for n in symbols.values())
    calls=[e for e in snapshot['edges'] if e['kind']=='CALLS' and e['source']=='tree_sitter_structural']
    assert len(calls)==1
    assert calls[0]['assertion']=='resolved'
    assert main(['graph','workflow','--symbol','work','--repo-id','main','--json']) == 0
    result=json.loads(capsys.readouterr().out)
    assert any(x['relation']=='confirmed' and x['path']=='src/lib.rs' for x in result['data']['result']['stages']['implementation'])
    assert result['data']['result']['completeness']['precision']=='partial'  # no test relation


def test_rust_mod_declaration_materializes_verified_module_to_file_relation(tmp_path, monkeypatch, capsys) -> None:
    import json
    from tests.repoctl.workspace.test_check import write_workspace
    from tests.repoctl.meta.test_meta_check import write_repometa
    from tests.repoctl.repository.test_repositories import init_repo
    from tools.repoctl.cli import main

    write_workspace(tmp_path)
    repo = tmp_path / 'repos'
    init_repo(repo)
    write_repometa(repo)
    (repo / 'src').mkdir()
    (repo / 'src/main.rs').write_text('mod audio_clock;\nfn main(){ audio_clock::tick(); }\n',encoding='utf-8')
    (repo / 'src/audio_clock.rs').write_text('pub fn tick(){}\n',encoding='utf-8')
    monkeypatch.setattr('tools.repoctl.cli.find_workspace_root', lambda: tmp_path)
    assert main(['graph','build','--repo-id','main','--full','--json']) == 0
    data=json.loads(capsys.readouterr().out)
    matches=[e for e in data['data']['snapshot']['edges'] if e['kind']=='IMPORTS_FILE' and e['source']=='tree_sitter_structural']
    assert len(matches)==1
    assert matches[0]['assertion']=='resolved'
    assert matches[0]['facts']['match_kind']=='rust_mod_declaration'
    assert main(['graph','workflow','--repo-id','main','--symbol','audio_clock','--json']) == 0
    work=json.loads(capsys.readouterr().out)['data']['result']
    assert any(x['path']=='src/audio_clock.rs' and x['relation']=='confirmed' for x in work['stages']['implementation'])
    assert any(x['path']=='src/main.rs' and x['relation']=='confirmed' for x in work['stages']['callers_or_dependents'])


def test_large_realistic_rust_ast_is_iterative_and_preserves_module_line_anchors(tmp_path: Path) -> None:
    """Regression for native tree-sitter node/Point crashes on large Rust source.

    Thousands of nested descendant accesses must not use Python recursion or
    the tree-sitter native Point getter; source coordinates stay byte-accurate.
    """
    root=tmp_path
    path='crates/daemon/src/main.rs'
    src=root/path
    src.parent.mkdir(parents=True,exist_ok=True)
    (root/'crates/daemon/src/audio_clock.rs').write_text('pub struct AudioClockDriftEstimator;\n',encoding='utf-8')
    chunks=['mod audio_clock;\n','fn work() -> i64 { 1 }\n','fn entry() -> i64 { work() }\n']
    chunks.extend(f'fn generated_{i}() -> i64 {{ entry() + {i} }}\n' for i in range(3100))
    src.write_text(''.join(chunks),encoding='utf-8')
    target=RepoTarget(id='main',root_path=root,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    entries=[CodeIndexEntry(p,'repos/'+p,'rust','source',[],[],[],[],[]) for p in (path,'crates/daemon/src/audio_clock.rs')]
    result=build_tree_sitter_semantics(target=target,entries=entries)
    assert not result.symbol_failed_paths
    assert len(result.symbols)>=3102
    assert len(result.calls)>=3100
    assert result.tool['module_file_relations'][0]['target_path']=='crates/daemon/src/audio_clock.rs'
    assert result.tool['module_file_relations'][0]['anchor']['start_line']==1
    assert any(call.anchor.start_line>3100 for call in result.calls)


@pytest.mark.parametrize('language,path,source', [
    ('rust','src/lib.rs','fn work() {}\nfn main() { let work = || {}; work(); }\n'),
    ('go','src/main.go','package main\nfunc work() {}\nfunc main() { work := func(){}; work() }\n'),
    ('rust','src/lib.rs','fn work() {}\nfn caller(work: fn()) { work(); }\n'),
    ('go','src/main.go','package main\nfunc work() {}\nfunc caller(work func()){work()}\n'),
])
def test_local_bindings_shadow_same_named_functions_not_confirmed(tmp_path: Path,language: str,path: str,source: str) -> None:
    target=RepoTarget(id='main',root_path=tmp_path,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    file=tmp_path/path
    file.parent.mkdir(parents=True,exist_ok=True)
    file.write_text(source,encoding='utf-8')
    entry=CodeIndexEntry(path,'repos/'+path,language,'source',[],[],[],[],[])
    result=build_tree_sitter_semantics(target=target,entries=[entry])
    assert len(result.symbols)>=2
    assert result.calls == (), 'Shadowed local callable must not become a provider-confirmed CALLS edge'


@pytest.mark.parametrize('language,path,source', [
    ('rust','src/lib.rs','struct A; impl A { fn work(&self) {} } fn entry(){ work(); }'),
    ('go','src/main.go','package main\ntype S struct{}\nfunc (s S) work() {}\nfunc entry(){work()}\n'),
    ('java','src/Test.java','class A{void work(){}} class B{void entry(){work();}}'),
])
def test_unqualified_call_does_not_resolve_to_method_in_another_scope(
    tmp_path: Path, language: str, path: str, source: str,
) -> None:
    target = RepoTarget(id='main',root_path=tmp_path,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    f=tmp_path/path
    f.parent.mkdir(parents=True,exist_ok=True)
    f.write_text(source,encoding='utf-8')
    entry=CodeIndexEntry(path,'repos/'+path,language,'source',[],[],[],[],[])
    result=build_tree_sitter_semantics(target=target,entries=[entry])
    assert len(result.symbols) >= 2
    assert result.calls == (), 'Implicit member dispatch cannot be inferred from equal names alone'


def test_go_calls_across_files_same_verified_package_and_directory(tmp_path: Path) -> None:
    target = RepoTarget(id='main',root_path=tmp_path,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    parts = {
        'src/main.go': 'package queue\nfunc Dispatch() { Run() }\n',
        'src/handler.go': 'package queue\nfunc Run() {}\n',
        'other/main.go': 'package other\nfunc Run() {}\n',
    }
    entries = []
    for name, source in parts.items():
        path = tmp_path / name
        path.parent.mkdir(parents=True,exist_ok=True)
        path.write_text(source,encoding='utf-8')
        entries.append(CodeIndexEntry(name, 'repos/'+name, 'go', 'source', [], [], [], [], []))
    result = build_tree_sitter_semantics(target=target,entries=entries)
    symbols = {item.provider_symbol_id:item for item in result.symbols}
    assert result.symbol_failed_paths == ()
    assert len(result.calls) == 1
    call = result.calls[0]
    assert call.scope == 'same_package' and call.path == 'src/main.go'
    assert symbols[call.caller_provider_symbol_id].name == 'Dispatch'
    assert symbols[call.callee_provider_symbol_id].path == 'src/handler.go'
    assert call.anchor.start_line == 2


def test_go_cross_file_call_shadowed_by_parameter_does_not_make_false_graph_edge(tmp_path: Path) -> None:
    target=RepoTarget(id='main',root_path=tmp_path,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    parts={
        'src/api.go':'package app\nfunc Dispatch(Run func()){Run()}\n',
        'src/impl.go':'package app\nfunc Run(){}\n',
    }
    entries=[]
    for name, source in parts.items():
        file=tmp_path/name
        file.parent.mkdir(parents=True,exist_ok=True)
        file.write_text(source,encoding='utf-8')
        entries.append(CodeIndexEntry(name,'repos/'+name,'go','source',[],[],[],[],[]))
    result=build_tree_sitter_semantics(target=target,entries=entries)
    assert result.symbol_failed_paths == ()
    assert result.calls == ()


@pytest.mark.parametrize('extension,lang,callee_source,import_source', [
    ('java','java','package com.example;\npublic class Model {}\n',
     'package com.other;\nimport com.example.Model;\nclass TestModel { Model model; }\n'),
    ('kt','kotlin','package com.example\n\nclass Model {}\n',
     'package com.other\n\nimport com.example.Model\n\nclass TestModel { val model: Model? = null\n}\n'),
])
def test_java_kotlin_only_explicit_exact_type_import_creates_proven_source_dependency(
    tmp_path: Path, extension: str, lang: str, callee_source: str, import_source: str,
) -> None:
    target=RepoTarget(id='main',root_path=tmp_path,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    parts={f'src/Model.{extension}':callee_source,f'tests/TestModel.{extension}':import_source}
    entries=[]
    for name,content in parts.items():
        file=tmp_path/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(content,encoding='utf-8')
        entries.append(CodeIndexEntry(name,'repos/'+name,'java','source',[],[],[],[],[]))
    result=build_tree_sitter_semantics(target=target,entries=entries)
    assert result.symbol_failed_paths == (),result.failures
    links=result.tool['module_file_relations']
    assert len(links)==1,links
    assert links[0]['kind']==lang+'_exact_type_import'
    assert links[0]['importer_path']==f'tests/TestModel.{extension}'
    assert links[0]['target_path']==f'src/Model.{extension}'
    assert links[0]['anchor']['start_line'] >= 2


@pytest.mark.parametrize('import_expr', [
    'import com.example.*;',
    'import static com.example.Model.work;',
    'import com.other.Model;',
])
def test_java_ambiguous_or_non_type_import_is_never_confirmed(tmp_path: Path,import_expr: str) -> None:
    target=RepoTarget(id='main',root_path=tmp_path,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    parts={
        'src/Model.java':'package com.example;\npublic class Model {}\n',
        'tests/TestModel.java':f'package test;\n{import_expr}\nclass TestModel {{}}\n',
    }
    entries=[]
    for name,content in parts.items():
        file=tmp_path/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(content,encoding='utf-8')
        entries.append(CodeIndexEntry(name,'repos/'+name,'java','source',[],[],[],[],[]))
    result=build_tree_sitter_semantics(target=target,entries=entries)
    assert result.tool['module_file_relations']==[]


def test_native_exact_java_import_materializes_test_to_production_relation(tmp_path,monkeypatch,capsys) -> None:
    import json
    from tests.repoctl.workspace.test_check import write_workspace
    from tests.repoctl.meta.test_meta_check import write_repometa
    from tests.repoctl.repository.test_repositories import init_repo
    from tools.repoctl.cli import main

    write_workspace(tmp_path)
    repo=tmp_path/'repos';init_repo(repo);write_repometa(repo)
    for name,content in {
        'src/Model.java':'package com.example;\npublic class Model {}\n',
        'tests/TestModel.java':'package com.other;\nimport com.example.Model;\nclass TestModel { Model model; }\n',
    }.items():
        file=repo/name;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(content,encoding='utf-8')
    monkeypatch.setattr('tools.repoctl.cli.find_workspace_root',lambda:tmp_path)
    assert main(['graph','build','--repo-id','main','--full','--json'])==0
    snapshot=json.loads(capsys.readouterr().out)['data']['snapshot']
    native=[edge for edge in snapshot['edges'] if edge['source']=='tree_sitter_structural']
    assert any(edge['kind']=='IMPORTS_FILE' and edge['facts']['match_kind']=='java_exact_type_import' for edge in native)
    assert any(edge['kind']=='TESTS_FILE' and edge['facts']['evidence_type']=='exact_structural_import_by_test' for edge in native)
    assert main(['graph','workflow','--symbol','Model','--in-file','src/Model.java','--repo-id','main','--json'])==0
    result=json.loads(capsys.readouterr().out)['data']['result']
    assert any(item['path']=='tests/TestModel.java' and item['relation']=='confirmed' for item in result['stages']['tests'])


def test_go_same_package_graph_workflow_has_verified_caller_and_tests(tmp_path,monkeypatch,capsys) -> None:
    import json
    from tests.repoctl.workspace.test_check import write_workspace
    from tests.repoctl.meta.test_meta_check import write_repometa
    from tests.repoctl.repository.test_repositories import init_repo
    from tools.repoctl.cli import main

    write_workspace(tmp_path)
    repo=tmp_path/'repos';init_repo(repo);write_repometa(repo)
    for name,content in {
        'src/handler.go':'package queue\nfunc Run() {}\n',
        'src/main.go':'package queue\nfunc Dispatch() { Run() }\n',
        'src/handler_test.go':'package queue\nimport "testing"\nfunc TestRun(t *testing.T) { Run() }\n',
    }.items():
        path=repo/name;path.parent.mkdir(parents=True,exist_ok=True);path.write_text(content,encoding='utf-8')
    monkeypatch.setattr('tools.repoctl.cli.find_workspace_root',lambda:tmp_path)
    assert main(['graph','build','--repo-id','main','--full','--json'])==0
    snapshot=json.loads(capsys.readouterr().out)['data']['snapshot']
    edges=[edge for edge in snapshot['edges'] if edge['kind']=='CALLS' and edge['source']=='tree_sitter_structural']
    assert len(edges)==2,edges
    assert all(edge['facts']['scope']=='same_package' for edge in edges)
    assert main(['graph','workflow','--symbol','Run','--in-file','src/handler.go','--repo-id','main','--json'])==0
    result=json.loads(capsys.readouterr().out)['data']['result']
    assert result['stages']['implementation'][0]['relation']=='confirmed'
    assert any(item['path']=='src/main.go' and item['relation']=='confirmed'
               for item in result['stages']['callers_or_dependents'])
    assert any(item['path']=='src/handler_test.go' and item['relation']=='confirmed'
               for item in result['stages']['tests'])


def test_go_native_file_suffix_is_test_role() -> None:
    from tools.repoctl.path_roles import is_test_path
    assert is_test_path('src/handler_test.go')
    assert not is_test_path('src/handler.go')


def test_verified_rust_mod_direct_call_across_files_is_typed(tmp_path: Path) -> None:
    root=tmp_path
    from tools.repoctl.graph_tree_sitter_provider import build_tree_sitter_semantics
    target=RepoTarget(id='main',root_path=root,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    sources={
        'src/main.rs':'mod clock;\nfn main(){ clock::tick(); }\n',
        'src/clock.rs':'pub fn tick(){}\n',
    }
    entries=[]
    for path,text in sources.items():
        file=root/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(text)
        entries.append(CodeIndexEntry(path,'repos/'+path,'rust','source',[],[],[],[],[]))
    result=build_tree_sitter_semantics(target=target,entries=entries)
    sym={item.provider_symbol_id:item for item in result.symbols}
    calls=[item for item in result.calls if item.scope=='cross_file_import']
    assert len(calls)==1
    assert sym[calls[0].caller_provider_symbol_id].path=='src/main.rs'
    assert sym[calls[0].callee_provider_symbol_id].path=='src/clock.rs'
    assert calls[0].anchor.start_line==2


def test_rust_mod_scoped_call_to_private_fn_stays_unconfirmed(tmp_path: Path) -> None:
    root=tmp_path
    target=RepoTarget(id='main',root_path=root,display_path='repos',identity_source=RepositoryIdentitySource.RESERVED)
    entries=[]
    for path,text in {
        'src/main.rs':'mod clock;\nfn main(){ clock::tick(); }\n',
        'src/clock.rs':'fn tick(){}\n',
    }.items():
        file=root/path;file.parent.mkdir(parents=True,exist_ok=True);file.write_text(text)
        entries.append(CodeIndexEntry(path,'repos/'+path,'rust','source',[],[],[],[],[]))
    result=build_tree_sitter_semantics(target=target,entries=entries)
    assert not result.calls


def test_rust_scoped_module_call_graph_end_to_end(tmp_path,monkeypatch,capsys) -> None:
    import json
    from tests.repoctl.workspace.test_check import write_workspace
    from tests.repoctl.meta.test_meta_check import write_repometa
    from tests.repoctl.repository.test_repositories import init_repo
    from tools.repoctl.cli import main
    write_workspace(tmp_path)
    repo=tmp_path/'repos';init_repo(repo);write_repometa(repo)
    for path,text in {
        'src/main.rs':'mod clock;\nfn main(){ clock::tick(); }\n',
        'src/clock.rs':'pub fn tick(){}\n',
    }.items():
        f=repo/path;f.parent.mkdir(parents=True,exist_ok=True);f.write_text(text)
    monkeypatch.setattr('tools.repoctl.cli.find_workspace_root',lambda:tmp_path)
    assert main(['graph','build','--repo-id','main','--full','--json'])==0
    snapshot=json.loads(capsys.readouterr().out)['data']['snapshot']
    assert any(edge['kind']=='CALLS' and edge['source']=='tree_sitter_structural'
               and edge['facts']['scope']=='cross_file_import' for edge in snapshot['edges'])
    assert main(['graph','workflow','--symbol','tick','--in-file','src/clock.rs','--json'])==0
    result=json.loads(capsys.readouterr().out)['data']['result']
    assert any(item['path']=='src/main.rs' and item['relation']=='confirmed'
               for item in result['stages']['callers_or_dependents'])

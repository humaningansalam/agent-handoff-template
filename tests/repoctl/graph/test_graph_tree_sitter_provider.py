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

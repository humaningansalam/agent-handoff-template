from __future__ import annotations
from tests.repoctl.graph.test_graph_build import _snapshot

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import file_id, symbol_id
from tools.repoctl.graph_store import materialize_graph
from tools.repoctl.repositories import require_repo_target
from tests.repoctl.workspace.test_check import write_workspace
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo


def _materialize(root: Path) -> None:
    snapshot, problems, _meta = materialize_graph(root, target=require_repo_target(root, repo_id="main"))
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]


def test_graph_resolves_same_file_python_calls(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "auth").mkdir()
    (repo / "auth/flow.py").write_text(
        'def validate_token(token: str) -> bool:\n    return token == "ok"\n\n\ncheck_token = validate_token\n\n\ndef login(token: str) -> str:\n    if check_token(token):\n        return "ok"\n    return "denied"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    validate_id = symbol_id("main", "python_ast", "python_ast:auth/flow.py:validate_token:function:1:0:2:24")
    login_id = symbol_id("main", "python_ast", "python_ast:auth/flow.py:login:function:8:0:11:19")
    assert "same_file_calls" in snapshot["capabilities"]
    assert any(source["kind"] == "python_ast_calls" and source["assertion"] == "resolved" for source in snapshot["sources"])
    assert any(edge["kind"] == "CALLS" and edge["from"] == login_id and edge["to"] == validate_id and edge["facts"]["scope"] == "same_file" for edge in snapshot["edges"])


def test_graph_resolves_same_class_python_method_calls(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "auth").mkdir()
    (repo / "auth/method_flow.py").write_text(
        'class TokenFlow:\n    def validate(self, token: str) -> bool:\n        return token == "ok"\n\n    def login(self, token: str) -> str:\n        if self.validate(token):\n            return "ok"\n        return "denied"\n',
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    validate_id = symbol_id("main", "python_ast", "python_ast:auth/method_flow.py:TokenFlow.validate:method:2:4:3:28")
    login_id = symbol_id("main", "python_ast", "python_ast:auth/method_flow.py:TokenFlow.login:method:5:4:8:23")
    assert any(edge["kind"] == "CALLS" and edge["from"] == login_id and edge["to"] == validate_id and edge["facts"]["scope"] == "same_file" for edge in snapshot["edges"])


def test_graph_resolves_cross_file_python_imported_function_calls(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "services").mkdir()
    (repo / "handlers").mkdir()
    (repo / "p_audit").mkdir()
    (repo / "services/token_service.py").write_text(
        "def issue_token(user_id: str) -> str:\n"
        "    return f'token:{user_id}'\n\n"
        "class TokenIssuer:\n"
        "    pass\n",
        encoding="utf-8",
    )
    (repo / "handlers/cross_login.py").write_text(
        "from services.token_service import issue_token\n\n\ndef login(user_id: str) -> str:\n    return issue_token(user_id)\n",
        encoding="utf-8",
    )
    (repo / "services/conditional.py").write_text(
        "if False:\n"
        "    def hidden():\n"
        "        return 1\n",
        encoding="utf-8",
    )
    (repo / "handlers/conditional_direct.py").write_text(
        "from services.conditional import hidden\n\n"
        "def call_hidden():\n"
        "    return hidden()\n",
        encoding="utf-8",
    )
    (repo / "handlers/conditional_qualified.py").write_text(
        "import services.conditional as conditional\n\n"
        "def call_hidden():\n"
        "    return conditional.hidden()\n",
        encoding="utf-8",
    )
    (repo / "handlers/conditional_import_direct.py").write_text(
        "if False:\n"
        "    from services.token_service import issue_token\n\n"
        "def login(user_id: str) -> str:\n"
        "    return issue_token(user_id)\n",
        encoding="utf-8",
    )
    (repo / "handlers/conditional_import_qualified.py").write_text(
        "if False:\n"
        "    import services.token_service as tokens\n\n"
        "def login(user_id: str) -> str:\n"
        "    return tokens.issue_token(user_id)\n",
        encoding="utf-8",
    )
    (repo / "handlers/conditional_module_alias.py").write_text(
        "import services.token_service as tokens\n"
        "if False:\n"
        "    selected_tokens = tokens\n\n"
        "def login(user_id: str) -> str:\n"
        "    return selected_tokens.issue_token(user_id)\n",
        encoding="utf-8",
    )
    (repo / "handlers/deleted_import_direct.py").write_text(
        "from services.token_service import issue_token\n"
        "del issue_token\n\n"
        "def login(user_id: str) -> str:\n"
        "    return issue_token(user_id)\n",
        encoding="utf-8",
    )
    (repo / "handlers/deleted_import_qualified.py").write_text(
        "import services.token_service as tokens\n"
        "del tokens\n\n"
        "def login(user_id: str) -> str:\n"
        "    return tokens.issue_token(user_id)\n",
        encoding="utf-8",
    )
    (repo / "handlers/dotted_import_without_alias.py").write_text(
        "import services.token_service\n\n"
        "def login(user_id: str) -> str:\n"
        "    return services.issue_token(user_id)\n",
        encoding="utf-8",
    )
    (repo / "handlers/cross_factory.py").write_text(
        "from services.token_service import TokenIssuer\n\n\n"
        "def build() -> TokenIssuer:\n"
        "    return TokenIssuer()\n",
        encoding="utf-8",
    )
    (repo / "p_audit/__init__.py").write_text("", encoding="utf-8")
    (repo / "p_audit/sub.py").write_text("def sub():\n    return 'function'\n", encoding="utf-8")
    (repo / "handlers/submodule_call.py").write_text(
        "from p_audit import sub\n\n\n"
        "def caller():\n"
        "    return sub()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    issue_id = symbol_id("main", "python_ast", "python_ast:services/token_service.py:issue_token:function:1:0:2:29")
    issuer_id = symbol_id("main", "python_ast", "python_ast:services/token_service.py:TokenIssuer:class:4:0:5:8")
    login_id = symbol_id("main", "python_ast", "python_ast:handlers/cross_login.py:login:function:4:0:5:31")
    build_id = symbol_id("main", "python_ast", "python_ast:handlers/cross_factory.py:build:function:4:0:5:24")
    sub_id = symbol_id("main", "python_ast", "python_ast:p_audit/sub.py:sub:function:1:0:2:21")
    submodule_caller_id = symbol_id("main", "python_ast", "python_ast:handlers/submodule_call.py:caller:function:4:0:5:16")
    assert "cross_file_import_calls" in snapshot["capabilities"]
    assert any(edge["kind"] == "IMPORTS_FILE" and edge["from"] == file_id("main", "handlers/cross_login.py") and edge["to"] == file_id("main", "services/token_service.py") for edge in snapshot["edges"])
    assert any(edge["kind"] == "CALLS" and edge["from"] == login_id and edge["to"] == issue_id and edge["facts"]["scope"] == "cross_file_import" for edge in snapshot["edges"])
    assert any(edge["kind"] == "CALLS" and edge["from"] == build_id and edge["to"] == issuer_id and edge["facts"]["scope"] == "cross_file_import" for edge in snapshot["edges"])
    assert not any(edge["kind"] == "CALLS" and edge["from"] == submodule_caller_id and edge["to"] == sub_id for edge in snapshot["edges"])
    assert {
        edge["from"]
        for edge in snapshot["edges"]
        if edge["kind"] == "CALLS" and edge["to"] == issue_id
    } == {login_id}
    hidden_ids = {
        node["id"]
        for node in snapshot["nodes"]
        if node["kind"] == "symbol" and node["facts"]["provider"]["qualified_name"] == "hidden"
    }
    assert hidden_ids
    assert not any(edge["kind"] == "CALLS" and edge["to"] in hidden_ids for edge in snapshot["edges"])


def test_graph_resolves_python_module_qualified_calls_without_overstating_completeness(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "services").mkdir()
    (repo / "handlers").mkdir()
    (repo / "services/token_service.py").write_text(
        "def issue_token(user_id: str) -> str:\n    return f'token:{user_id}'\n",
        encoding="utf-8",
    )
    (repo / "handlers/cross_login.py").write_text(
        "import services.token_service as tokens\n\n\ndef login(user_id: str) -> str:\n    return tokens.issue_token(user_id)\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    issue_id = symbol_id("main", "python_ast", "python_ast:services/token_service.py:issue_token:function:1:0:2:29")
    login_id = symbol_id("main", "python_ast", "python_ast:handlers/cross_login.py:login:function:4:0:5:38")
    assert any(edge["kind"] == "CALLS" and edge["from"] == login_id and edge["to"] == issue_id for edge in snapshot["edges"])
    assert snapshot["completeness"]["capabilities"]["calls"] == "partial"
    assert snapshot["completeness"]["provider_coverage"]["calls"]["coverage_gaps"] == [
        "python_dynamic_call_targets_are_not_exhaustive"
    ]


def test_graph_resolves_python_callable_reexports(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "origin.py").write_text("def target():\n    return 1\n", encoding="utf-8")
    (repo / "reexport.py").write_text("from origin import target\n", encoding="utf-8")
    (repo / "aliased.py").write_text("from origin import target as public_target\n", encoding="utf-8")
    (repo / "wildpkg").mkdir()
    (repo / "wildpkg/__init__.py").write_text("from origin import target\nfrom .replacement import *\n", encoding="utf-8")
    (repo / "wildpkg/replacement.py").write_text("__all__ = ['target']\n\ndef target():\n    return 2\n", encoding="utf-8")
    (repo / "consumer.py").write_text(
        "from reexport import target\n"
        "import aliased\n\n"
        "import wildpkg\n\n"
        "def direct():\n"
        "    return target()\n\n"
        "def qualified():\n"
        "    return aliased.public_target()\n\n"
        "def wildcard_qualified():\n"
        "    return wildpkg.target()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    names = {
        node["id"]: node["facts"]["provider"]["qualified_name"]
        for node in snapshot["nodes"]
        if node["kind"] == "symbol"
    }
    calls = {(names[edge["from"]], names[edge["to"]]) for edge in snapshot["edges"] if edge["kind"] == "CALLS"}
    assert {("direct", "target"), ("qualified", "target")} <= calls
    assert not any(caller == "wildcard_qualified" for caller, _callee in calls)


def test_graph_skips_shadowed_cross_file_python_imported_function_calls(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "services").mkdir()
    (repo / "handlers").mkdir()
    (repo / "services/token_service.py").write_text(
        "def issue_token(user_id: str) -> str:\n    return f'token:{user_id}'\n",
        encoding="utf-8",
    )
    (repo / "handlers/cross_login.py").write_text(
        "from services.token_service import issue_token\n\n\ndef login(issue_token) -> str:\n    return issue_token()\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    issue_id = symbol_id("main", "python_ast", "python_ast:services/token_service.py:issue_token:function:1:0:2:29")
    login_id = symbol_id("main", "python_ast", "python_ast:handlers/cross_login.py:login:function:4:0:5:24")
    assert not any(edge["kind"] == "CALLS" and edge["from"] == login_id and edge["to"] == issue_id for edge in snapshot["edges"])


def test_graph_python_calls_follow_lexical_scopes(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "scope.py").write_text(
        "def target():\n"
        "    return 1\n\n"
        "def other():\n"
        "    return 2\n\n"
        "class Factory:\n"
        "    pass\n\n"
        "def call_class():\n"
        "    return Factory()\n\n"
        "class ClassComprehension:\n"
        "    def target():\n"
        "        return 9\n"
        "    values = [target() for _ in (1,)]\n\n"
        "class EarlyClassSuite:\n"
        "    value = later_class_target()\n\n"
        "def later_class_target():\n"
        "    return 10\n\n"
        "f = target\n"
        "handler = target\n\n"
        "def call_alias():\n"
        "    return f()\n\n"
        "def outer():\n"
        "    def inner():\n"
        "        return target()\n"
        "    return inner()\n\n"
        "def shadowed(target):\n"
        "    return target()\n\n"
        "def comprehension_shadow(values):\n"
        "    return [target() for target in values]\n\n"
        "def comprehension_direct(values):\n"
        "    return [target() for value in values]\n\n"
        "def call_before_alias():\n"
        "    target()\n"
        "    target = other\n\n"
        "def call_before_definition():\n"
        "    inner()\n"
        "    def inner():\n"
        "        return 1\n\n"
        "def comprehension_assignment():\n"
        "    [f := other for _ in ()]\n"
        "    return f()\n\n"
        "def comprehension_assignment_target(box, values):\n"
        "    return [box.items[0] for box.items[target()] in values]\n\n"
        "def pattern_assignment(value):\n"
        "    match value:\n"
        "        case {'handler': handler}:\n"
        "            pass\n"
        "    return handler()\n\n"
        "def conditional_definition():\n"
        "    if False:\n"
        "        def hidden():\n"
        "            return 1\n"
        "    return hidden()\n\n"
        "def deleted_target():\n"
        "    return 3\n\n"
        "del deleted_target\n\n"
        "def call_deleted_target():\n"
        "    return deleted_target()\n\n"
        "early_alias = later_target\n\n"
        "def later_target():\n"
        "    return 4\n\n"
        "def call_early_alias():\n"
        "    return early_alias()\n\n"
        "def replace_target(function):\n"
        "    return other\n\n"
        "@replace_target\n"
        "def decorated_target():\n"
        "    return 5\n\n"
        "def call_decorated_target():\n"
        "    return decorated_target()\n\n"
        "def annotation_only():\n"
        "    value: target()\n\n"
        "def annotation_target(items):\n"
        "    items[target()]: int\n\n"
        "def generator_lazy():\n"
        "    return (target() for _ in (1,))\n\n"
        "def generator_iterable():\n"
        "    return (value for value in target())\n\n"
        "def recursive(value):\n"
        "    if value:\n"
        "        return recursive(value - 1)\n"
        "    return 0\n\n"
        "def outer_default():\n"
        "    def inner(value=target()):\n"
        "        return value\n"
        "    return inner\n\n"
        "def outer_self_default():\n"
        "    def inner(value=inner()):\n"
        "        return value\n"
        "    return inner\n\n"
        "def outer_lambda_default():\n"
        "    handler = lambda value=target(): value\n"
        "    return handler\n\n"
        "def walrus_definition():\n"
        "    def local_target():\n"
        "        return 11\n"
        "    def local_other():\n"
        "        return 12\n"
        "    def install(value=(local_target := local_other)):\n"
        "        return value\n"
        "    return local_target()\n\n"
        "def walrus_comprehension():\n"
        "    def local_target():\n"
        "        return 13\n"
        "    def local_other():\n"
        "        return 14\n"
        "    [(local_target := local_other) for _ in (1,)]\n"
        "    return local_target()\n\n"
        "def install_global():\n"
        "    global installed_target\n"
        "    def installed_target():\n"
        "        return 6\n\n"
        "def call_installed_target():\n"
        "    return installed_target()\n\n"
        "def nonlocal_rebinding():\n"
        "    def installer():\n"
        "        nonlocal selected\n"
        "        def selected():\n"
        "            return 7\n\n"
        "    def selected():\n"
        "        return 8\n\n"
        "    installer()\n"
        "    return selected()\n\n"
        "class ClassGlobalBindings:\n"
        "    global installed_alias, installed_import, installed_function\n"
        "    installed_alias = target\n"
        "    from dependency import remote_target as installed_import\n"
        "    def installed_function():\n"
        "        return 15\n\n"
        "    installed_value = installed_alias()\n\n"
        "def call_class_global_alias():\n"
        "    return installed_alias()\n\n"
        "def call_class_global_import():\n"
        "    return installed_import()\n\n"
        "def call_class_global_function():\n"
        "    return installed_function()\n\n"
        "def class_nonlocal_rebinding():\n"
        "    def selected():\n"
        "        return 20\n"
        "    class Installer:\n"
        "        nonlocal selected\n"
        "        def selected():\n"
        "            return 21\n"
        "        value = selected()\n"
        "    return Installer.value\n\n"
        "class FutureClassLocal:\n"
        "    value = target()\n"
        "    target = other\n\n"
        "def outer_previous_default():\n"
        "    def selected():\n"
        "        return 16\n"
        "    def selected(value=selected()):\n"
        "        return value\n"
        "    return selected\n\n"
        "class ClassTimeline:\n"
        "    def selected():\n"
        "        return 17\n"
        "    value = selected()\n"
        "    def selected():\n"
        "        return 18\n\n"
        "class StaticCaller:\n"
        "    def target(self):\n"
        "        return 19\n"
        "    @staticmethod\n"
        "    def caller(self):\n"
        "        return self.target()\n\n"
        "annotated_alias: object = target\n\n"
        "def call_annotated_alias():\n"
        "    return annotated_alias()\n\n"
        "def decorator_factory():\n"
        "    return lambda function: function\n\n"
        "def direct_decorator(value):\n"
        "    return value\n\n"
        "def outer_direct_decorator():\n"
        "    @direct_decorator\n"
        "    def inner():\n"
        "        return 1\n"
        "    return inner\n\n"
        "def outer_class_decorator():\n"
        "    @direct_decorator\n"
        "    class Inner:\n"
        "        pass\n"
        "    return Inner\n\n"
        "def outer_class_suite():\n"
        "    class Inner:\n"
        "        value = target()\n"
        "    return Inner\n\n"
        "def outer_decorator():\n"
        "    @decorator_factory()\n"
        "    def inner():\n"
        "        return 1\n"
        "    return inner()\n\n"
        "class Flow:\n"
        "    def target(self):\n"
        "        return 1\n\n"
        "    def outer(self):\n"
        "        def closure():\n"
        "            return self.target()\n"
        "        def shadowed(self):\n"
        "            return self.target()\n"
        "        return closure()\n\n"
        "class NamedReceiver:\n"
        "    def target(instance):\n"
        "        return 1\n"
        "    def caller(instance):\n"
        "        return instance.target()\n\n"
        "class NonReceiverSelf:\n"
        "    def target(receiver):\n"
        "        return 1\n"
        "    def caller(receiver, self):\n"
        "        return self.target()\n\n"
        "class WrappedStatic:\n"
        "    def target(instance):\n"
        "        return 1\n"
        "    def caller(receiver):\n"
        "        return receiver.target()\n"
        "    caller = staticmethod(caller)\n",
        encoding="utf-8",
    )
    (repo / "conditional_method.py").write_text(
        "class Service:\n"
        "    if False:\n"
        "        def hidden(self):\n"
        "            return 1\n\n"
        "    def caller(self):\n"
        "        return self.hidden()\n\n"
        "class DeletedService:\n"
        "    def hidden(self):\n"
        "        return 1\n\n"
        "    del hidden\n\n"
        "    def caller(self):\n"
        "        return self.hidden()\n",
        encoding="utf-8",
    )
    (repo / "dependency.py").write_text("def remote_target():\n    return 1\n", encoding="utf-8")
    (repo / "local_import.py").write_text(
        "def call_before_import():\n"
        "    remote_target()\n"
        "    from dependency import remote_target\n",
        encoding="utf-8",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "build", "--full", "--json"]) == 0

    snapshot = _snapshot(json.loads(capsys.readouterr().out))
    names = {
        node["id"]: node["facts"]["provider"]["qualified_name"]
        for node in snapshot["nodes"]
        if node["kind"] == "symbol"
    }
    calls = {(names[edge["from"]], names[edge["to"]]) for edge in snapshot["edges"] if edge["kind"] == "CALLS"}
    assert calls == {
        ("Flow.outer", "Flow.outer.closure"),
        ("Flow.outer.closure", "Flow.target"),
        ("NamedReceiver.caller", "NamedReceiver.target"),
        ("ClassComprehension", "target"),
        ("annotation_target", "target"),
        ("call_alias", "target"),
        ("call_annotated_alias", "target"),
        ("call_class_global_alias", "target"),
        ("call_class_global_function", "ClassGlobalBindings.installed_function"),
        ("call_class_global_import", "remote_target"),
        ("call_class", "Factory"),
        ("ClassGlobalBindings", "target"),
        ("class_nonlocal_rebinding.Installer", "class_nonlocal_rebinding.Installer.selected"),
        ("comprehension_direct", "target"),
        ("comprehension_assignment_target", "target"),
        ("generator_iterable", "target"),
        ("generator_lazy", "target"),
        ("FutureClassLocal", "target"),
        ("ClassTimeline", "ClassTimeline.selected"),
        ("nonlocal_rebinding", "nonlocal_rebinding.installer"),
        ("outer_class_decorator", "direct_decorator"),
        ("outer_class_suite.Inner", "target"),
        ("outer_decorator", "decorator_factory"),
        ("outer_direct_decorator", "direct_decorator"),
        ("outer_default", "target"),
        ("outer_lambda_default", "target"),
        ("outer_previous_default", "outer_previous_default.selected"),
        ("outer", "outer.inner"),
        ("outer.inner", "target"),
        ("recursive", "recursive"),
    }


def test_graph_query_symbol_callers_and_callees(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "auth").mkdir()
    callers = "\n\n".join(
        f"def caller_{index:02d}(token: str) -> bool:\n    return validate_token(token)"
        for index in range(10)
    )
    (repo / "auth/flow.py").write_text(
        f'def validate_token(token: str) -> bool:\n    return token == "ok"\n\n\n{callers}\n',
        encoding="utf-8",
    )
    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--symbol", "validate_token", "--json"]) == 0
    symbol_result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert symbol_result["query"] == {"type": "symbol", "symbol": "validate_token"}
    assert symbol_result["matches"][0]["qualified_name"] == "validate_token"
    assert symbol_result["matches"][0]["path"] == "auth/flow.py"
    assert symbol_result["paths"][0]["edge"] == "CALLS"
    assert any(path["edge"] == "CALLS" for path in symbol_result["paths"])
    direct_selector = {"kind": "symbol", "value": "validate_token", "in_file": "auth/flow.py"}
    assert all(item["selector"] != direct_selector for item in symbol_result["continuations"])

    assert main(["graph", "query", "--callers-of", direct_selector["value"], "--in-file", direct_selector["in_file"], "--json"]) == 0
    callers_result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert callers_result["query"] == {"type": "callers_of", "symbol": "validate_token", "in_file": "auth/flow.py"}
    assert 1 <= len(callers_result["paths"]) <= 3
    assert len(callers_result["continuations"]) <= 3
    continuation_selectors = {
        (item["selector"]["kind"], item["selector"]["value"], item["selector"].get("in_file", ""))
        for item in callers_result["continuations"]
    }
    assert ("symbol", "validate_token", "auth/flow.py") not in continuation_selectors
    for path in callers_result["paths"]:
        assert path["edge"] == "CALLS"
        for endpoint in (path["from"], path["to"]):
            assert endpoint["path"] == "auth/flow.py"
        assert set(path["evidence"]) == {"type", "assertion", "provider", "confidence", "completeness", "freshness"}

    caller_selector = next(
        item["selector"]
        for item in callers_result["continuations"]
        if item["selector"].get("value", "").startswith("caller_")
    )
    assert main(["graph", "query", "--symbol", direct_selector["value"], "--in-file", direct_selector["in_file"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["query_status"] == "found"
    assert main(["graph", "query", "--symbol", caller_selector["value"], "--in-file", caller_selector["in_file"], "--json"]) == 0
    assert json.loads(capsys.readouterr().out)["data"]["query_status"] == "found"

    assert main(["graph", "query", "--callees-of", "caller_00", "--in-file", "auth/flow.py", "--json"]) == 0
    callees_result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert any(path["edge"] == "CALLS" and path["from"]["qualified_name"] == "caller_00" and path["to"]["qualified_name"] == "validate_token" for path in callees_result["paths"])


def test_graph_query_symbol_ambiguity_fails_closed(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "web").mkdir()
    (repo / "api").mkdir()
    (repo / "web/auth.py").write_text("def login():\n    return 'web'\n", encoding="utf-8")
    (repo / "api/auth.py").write_text("def login():\n    return 'api'\n", encoding="utf-8")
    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--symbol", "login", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "graph_query_ambiguous_symbol"
    result = payload["data"]["result"]
    assert {match["path"] for match in result["matches"]} == {"api/auth.py", "web/auth.py"}

    assert main(["graph", "query", "--symbol", "login", "--in-file", "api/auth.py", "--json"]) == 0
    narrowed = json.loads(capsys.readouterr().out)["data"]["result"]
    assert narrowed["matches"][0]["path"] == "api/auth.py"


def test_graph_query_impact_file_uses_import_and_call_edges(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "services").mkdir()
    (repo / "handlers").mkdir()
    (repo / "services/token_service.py").write_text("def issue_token(user_id: str) -> str:\n    return f'token:{user_id}'\n", encoding="utf-8")
    (repo / "handlers/login.py").write_text(
        "from services.token_service import issue_token\n\n\ndef login(user_id: str) -> str:\n    return issue_token(user_id)\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--impact-file", "services/token_service.py", "--depth", "2", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert result["query"] == {"type": "impact_file", "path": "services/token_service.py", "depth": 2}
    assert any(path["edge"] == "IMPORTS_FILE" and path["from"]["path"] == "handlers/login.py" for path in result["paths"])
    assert any(path["edge"] == "CALLS" and path["from"]["qualified_name"] == "login" and path["to"]["qualified_name"] == "issue_token" for path in result["paths"])
    assert not any(path["edge"] in {"DEFINES", "ANCHORS"} for path in result["paths"])
    assert "edge_counts" not in result
    assert "display" not in result
    assert len(result["paths"]) <= 3
    assert len(result["continuations"]) <= 3
    assert all(set(path["evidence"]) == {"type", "assertion", "provider", "confidence", "completeness", "freshness"} for path in result["paths"])
    path_edges = [
        (path["edge"], json.dumps(path["from"], sort_keys=True), json.dumps(path["to"], sort_keys=True))
        for path in result["paths"]
    ]
    assert len(path_edges) == len(set(path_edges))


def test_graph_query_js_ts_impact_is_file_level(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "frontend/src/api").mkdir(parents=True)
    (repo / "frontend/src/client.ts").write_text("import { issueToken } from './api/tokens';\nexport const login = () => issueToken();\n", encoding="utf-8")
    (repo / "frontend/src/api/tokens.ts").write_text("export const issueToken = () => 'token';\n", encoding="utf-8")
    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--impact-file", "frontend/src/api/tokens.ts", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert any(path["edge"] == "IMPORTS_FILE" and path["from"]["path"] == "frontend/src/client.ts" for path in result["paths"])
    assert not any(path["edge"] == "CALLS" for path in result["paths"])


def test_graph_and_context_prioritize_owner_source_and_direct_test(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "tests").mkdir()
    (repo / "auth.py").write_text(
        "def validate_token(token: str) -> bool:\n    return token == 'ok'\n",
        encoding="utf-8",
    )
    (repo / "tests/test_auth.py").write_text(
        "from auth import validate_token\n\n\ndef test_validate_token() -> None:\n    assert validate_token('ok')\n",
        encoding="utf-8",
    )
    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--file", "repos/auth.py", "--json"]) == 0
    graph_result = json.loads(capsys.readouterr().out)["data"]["result"]
    direct_test = next(path for path in graph_result["paths"] if path["edge"] == "TESTS_FILE")
    assert direct_test["from"]["path"] == "tests/test_auth.py"
    assert direct_test["to"]["path"] == "auth.py"
    assert direct_test["evidence"]["type"] == "direct_test_import"
    assert direct_test["evidence"]["confidence"] == "medium"
    assert not any(
        path["edge"] == "IMPORTS_FILE"
        and path["from"] == direct_test["from"]
        and path["to"] == direct_test["to"]
        for path in graph_result["paths"]
    )
    assert any(item["selector"] == {"kind": "file", "value": "tests/test_auth.py"} for item in graph_result["continuations"])

    assert main(["context", "query", "validate_token", "--repo-id", "main", "--json"]) == 0
    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert bundle["groups"]["likely_change_surface"][0]["source_ref"]["path"] == "repos/auth.py"
    assert bundle["groups"]["tests_and_verification"][0]["source_ref"]["path"] == "repos/tests/test_auth.py"
    assert sum(len(items) for group, items in bundle["groups"].items() if group != "warnings_and_completeness") <= 8
    assert "selection" not in bundle
    assert "provider_coverage" not in bundle["completeness"]


def test_compact_graph_paths_and_continuations_share_one_budget(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "target.py").write_text("def run() -> int:\n    return 1\n", encoding="utf-8")
    for name in ("alpha", "beta", "gamma"):
        (repo / f"{name}.py").write_text("from target import run\n", encoding="utf-8")
    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--file", "target.py", "--json"]) == 0

    result = json.loads(capsys.readouterr().out)["data"]["result"]
    assert len(result["paths"]) == 3
    continuation_files = {
        item["selector"]["value"]
        for item in result["continuations"]
        if item["selector"].get("kind") == "file"
    }
    related_files = {
        endpoint["path"]
        for path in result["paths"]
        for endpoint in (path["from"], path["to"])
        if endpoint.get("path") != "target.py"
    }
    assert continuation_files == related_files == {"alpha.py", "beta.py", "gamma.py"}

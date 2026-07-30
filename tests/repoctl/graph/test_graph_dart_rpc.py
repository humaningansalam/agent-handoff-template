from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pytest

from tools.repoctl.code_index import CodeIndexEntry
from tools.repoctl.cli import main
from tools.repoctl.graph_dart_provider import _dart_executable, _helper_executable, _rpc_invocations, build_dart_semantics
from tools.repoctl.graph_semantic_model import (
    RpcInvocationContract,
    RpcInvocationFact,
    RpcInvocationReasonCode,
    RpcInvocationStatus,
    RpcParamsReasonCode,
    RpcParamsStatus,
    RpcRoutineReasonCode,
    RpcRoutineStatus,
    RpcSchemaReasonCode,
    RpcSchemaSelection,
    RpcSchemaStatus,
    SourceAnchor,
)
from tools.repoctl.graph_store import _provider_result_from_dict, _provider_result_to_dict, graph_materialization_freshness, materialize_graph
from tools.repoctl.graph_structured_relations import build_structured_file_relations
from tools.repoctl.repositories import RepoTarget, require_repo_target
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo
from tests.repoctl.workspace.test_check import write_workspace


def _require_dart_analyzer(root: Path) -> None:
    dart, _source = _dart_executable()
    if not dart:
        pytest.skip("Dart SDK is not installed")
    helper, error = _helper_executable(root, dart)
    if helper is None:
        pytest.skip(f"package:analyzer is unavailable offline: {error}")


def _write_dart_package(repo: Path, dependency_root: Path, source: str) -> None:
    dependency_lib = dependency_root / "lib"
    (dependency_lib / "src").mkdir(parents=True)
    (dependency_lib / "src/supabase_client.dart").write_text(
        "class SupabaseQuerySchema {\n"
        "  Future<dynamic> rpc(String routine, {Map<String, dynamic>? params}) async => null;\n"
        "}\n"
        "class SupabaseClient {\n"
        "  SupabaseQuerySchema schema(String name) => SupabaseQuerySchema();\n"
        "  Future<dynamic> rpc(String routine, {Map<String, dynamic>? params}) async => null;\n"
        "}\n",
        encoding="utf-8",
    )
    (dependency_lib / "supabase.dart").write_text(
        "export 'src/supabase_client.dart';\n",
        encoding="utf-8",
    )
    (dependency_root / "pubspec.yaml").write_text(
        "name: supabase\nenvironment:\n  sdk: '>=3.6.0 <4.0.0'\n",
        encoding="utf-8",
    )

    (repo / "lib").mkdir(parents=True)
    (repo / "lib/client.dart").write_text(source, encoding="utf-8")
    (repo / "pubspec.yaml").write_text(
        "name: rpc_fixture\n"
        "environment:\n"
        "  sdk: '>=3.6.0 <4.0.0'\n"
        "dependencies:\n"
        "  supabase: any\n",
        encoding="utf-8",
    )
    package_config = repo / ".dart_tool/package_config.json"
    package_config.parent.mkdir(parents=True)
    package_config.write_text(
        json.dumps(
            {
                "configVersion": 2,
                "packages": [
                    {
                        "name": "rpc_fixture",
                        "rootUri": "../",
                        "packageUri": "lib/",
                        "languageVersion": "3.6",
                    },
                    {
                        "name": "supabase",
                        "rootUri": dependency_root.resolve().as_uri() + "/",
                        "packageUri": "lib/",
                        "languageVersion": "3.6",
                    },
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )


def _entry() -> CodeIndexEntry:
    return CodeIndexEntry(
        path="lib/client.dart",
        workspace_path="repos/lib/client.dart",
        language="dart",
        classification="source",
        symbols=[],
        imports=["package:supabase/supabase.dart"],
        calls=["rpc"],
        deps=[],
        observed_effects=[],
    )


def _target(repo: Path) -> RepoTarget:
    return RepoTarget(id="main", root_path=repo, display_path="repos", identity_source="reserved")


def _snapshot_data(root: Path) -> dict:
    return json.loads((root / ".repoctl-state/graph/main/snapshot.json").read_text(encoding="utf-8"))


def _client_rpc(snapshot: dict) -> dict:
    node = next(
        item
        for item in snapshot["nodes"]
        if item["kind"] == "file" and item["identity"].get("path") == "lib/client.dart"
    )
    return node["facts"]["rpc"]


def test_dart_analyzer_emits_typed_rpc_facts_without_local_method_false_positives(tmp_path: Path) -> None:
    repo = tmp_path / "repos"
    repo.mkdir()
    source = (
        "import 'package:supabase/supabase.dart';\n"
        "class LocalBus {}\n"
        "final client = SupabaseClient();\n"
        "final routineName = 'dynamic_name';\n"
        "final dynamicParams = <String, dynamic>{'extra': 1};\n"
        "Future<void> run() async {\n"
        "  await client.rpc('complete', params: {'id': 1, 'note': 'ok'});\n"
        "  await client.rpc(routineName, params: {'id': 1});\n"
        "  await client.rpc('partial', params: {'id': 1, ...dynamicParams});\n"
        "  await client.rpc('unknown', params: dynamicParams);\n"
        "  await client.rpc('empty');\n"
        "  await client.rpc('empty_key', params: {'': 1});\n"
        "  await client.rpc('');\n"
        "  await client.rpc();\n"
        "  await client.rpc('extra', 'oops');\n"
        "  await client.rpc('bogus', bogus: 1);\n"
        "  await client.schema('private').rpc('schema_call');\n"
        "  await LocalBus().rpc('complete');\n"
        "}\n"
    )
    _write_dart_package(repo, tmp_path / "pub-cache/supabase", source)
    _require_dart_analyzer(tmp_path)

    result = build_dart_semantics(root=tmp_path, target=_target(repo), entries=[_entry()])

    assert result.rpc_analyzed_paths == ("lib/client.dart",)
    assert result.rpc_failed_paths == ()
    assert len(result.rpc_invocations) == 10
    assert len({fact.fact_id for fact in result.rpc_invocations}) == 10
    assert all(fact.resolved_callee_identity.endswith("#SupabaseClient.rpc") for fact in result.rpc_invocations)
    assert all(fact.receiver_type == "SupabaseClient" for fact in result.rpc_invocations)
    assert all(fact.schema_selection.status is RpcSchemaStatus.UNKNOWN for fact in result.rpc_invocations)
    assert all(fact.schema_selection.reason_code is RpcSchemaReasonCode.SCHEMA_NOT_OBSERVED for fact in result.rpc_invocations)
    assert "schema_call" not in {fact.routine for fact in result.rpc_invocations}
    assert all(source[fact.start_offset : fact.end_offset].startswith("client.rpc") for fact in result.rpc_invocations)
    expected_content_hash = "sha256:" + hashlib.sha256(source.encode("utf-8")).hexdigest()
    assert {fact.content_sha256 for fact in result.rpc_invocations} == {expected_content_hash}

    known = {fact.routine: fact for fact in result.rpc_invocations if fact.routine_status.value == "known"}
    dynamic_reasons = {
        fact.routine_reason_code: fact
        for fact in result.rpc_invocations
        if fact.routine_status.value == "unknown"
    }
    assert set(dynamic_reasons) == {
        RpcRoutineReasonCode.NOT_STATIC_STRING,
        RpcRoutineReasonCode.ARGUMENT_MISSING,
    }
    assert dynamic_reasons[RpcRoutineReasonCode.ARGUMENT_MISSING].syntactic_argument_count == 0
    assert dynamic_reasons[RpcRoutineReasonCode.ARGUMENT_MISSING].invocation.status is RpcInvocationStatus.INVALID
    assert dynamic_reasons[RpcRoutineReasonCode.ARGUMENT_MISSING].invocation.reason_code is RpcInvocationReasonCode.MISSING_REQUIRED_ARGUMENT
    assert known["extra"].invocation.status is RpcInvocationStatus.INVALID
    assert known["extra"].invocation.reason_code is RpcInvocationReasonCode.UNEXPECTED_ARGUMENT
    assert known["bogus"].invocation.status is RpcInvocationStatus.INVALID
    assert known["bogus"].invocation.unmatched_argument_count == 1
    assert known["complete"].params_status.value == "complete"
    assert known["complete"].param_names == ("id", "note")
    assert known["partial"].params_status.value == "partial"
    assert known["partial"].param_names == ("id",)
    assert known["partial"].params_reason_code is RpcParamsReasonCode.MAP_NOT_FULLY_STATIC
    assert known["unknown"].params_status.value == "unknown"
    assert known["empty"].params_status.value == "complete"
    assert known["empty"].param_names == ()
    assert known["empty_key"].params_status.value == "complete"
    assert known["empty_key"].param_names == ("",)
    assert known[""].syntactic_argument_count == 1

    contradictory = known["complete"].to_dict()
    contradictory["params"]["reason_code"] = RpcParamsReasonCode.NOT_MAP_LITERAL.value
    parsed, error = _rpc_invocations(
        [contradictory],
        repo_id="main",
        repo=repo,
        selected_paths={"lib/client.dart"},
    )
    assert parsed is None
    assert error

    cached = _provider_result_to_dict(result)
    cached["rpc_invocations"][0]["params"]["reason_code"] = RpcParamsReasonCode.NOT_MAP_LITERAL.value
    assert _provider_result_from_dict(cached, expected_provider="dart_analyzer") is None

    contradictory = known["unknown"].to_dict()
    contradictory["params"]["reason_code"] = RpcParamsReasonCode.MAP_NOT_FULLY_STATIC.value
    parsed, error = _rpc_invocations(
        [contradictory],
        repo_id="main",
        repo=repo,
        selected_paths={"lib/client.dart"},
    )
    assert parsed is None
    assert error

    cached = _provider_result_to_dict(result)
    cached_unknown = next(
        fact for fact in cached["rpc_invocations"] if fact["params"]["status"] == RpcParamsStatus.UNKNOWN.value
    )
    cached_unknown["params"]["reason_code"] = RpcParamsReasonCode.MAP_NOT_FULLY_STATIC.value
    assert _provider_result_from_dict(cached, expected_provider="dart_analyzer") is None

    legacy = _provider_result_to_dict(result)
    for key in ("rpc_invocations", "rpc_analyzed_paths", "rpc_failed_paths", "rpc_coverage"):
        legacy.pop(key)
    restored = _provider_result_from_dict(legacy, expected_provider="dart_analyzer")
    assert restored is not None
    assert restored.rpc_invocations == ()
    assert restored.rpc_analyzed_paths == ()


def test_dart_analyzer_keeps_resolved_unrelated_top_level_rpc_out_of_coverage(tmp_path: Path) -> None:
    repo = tmp_path / "repos"
    repo.mkdir()
    source = (
        "import 'package:supabase/supabase.dart';\n"
        "final client = SupabaseClient();\n"
        "Future<void> rpc(String value) async {}\n"
        "Future<void> run() async {\n"
        "  await client.rpc('linked');\n"
        "  await rpc('unrelated');\n"
        "}\n"
    )
    _write_dart_package(repo, tmp_path / "pub-cache/supabase", source)
    _require_dart_analyzer(tmp_path)

    result = build_dart_semantics(root=tmp_path, target=_target(repo), entries=[_entry()])

    assert result.rpc_analyzed_paths == ("lib/client.dart",)
    assert result.rpc_failed_paths == ()
    assert len(result.rpc_invocations) == 1
    assert result.rpc_invocations[0].routine == "linked"


def test_dart_analyzer_records_direct_rpc_tearoff_invocation(tmp_path: Path) -> None:
    repo = tmp_path / "repos"
    repo.mkdir()
    source = (
        "import 'package:supabase/supabase.dart';\n"
        "final client = SupabaseClient();\n"
        "Future<void> run() async {\n"
        "  await (client.rpc)('linked', params: {'id': 1});\n"
        "}\n"
    )
    _write_dart_package(repo, tmp_path / "pub-cache/supabase", source)
    _require_dart_analyzer(tmp_path)

    result = build_dart_semantics(root=tmp_path, target=_target(repo), entries=[_entry()])

    assert result.rpc_analyzed_paths == ("lib/client.dart",)
    assert result.rpc_failed_paths == ()
    assert len(result.rpc_invocations) == 1
    fact = result.rpc_invocations[0]
    assert fact.routine == "linked"
    assert fact.param_names == ("id",)
    assert fact.receiver_type == "SupabaseClient"
    assert source[fact.start_offset : fact.end_offset] == "(client.rpc)('linked', params: {'id': 1})"


def test_dart_analyzer_marks_stored_rpc_tearoff_enumeration_incomplete(tmp_path: Path) -> None:
    repo = tmp_path / "repos"
    repo.mkdir()
    source = (
        "import 'package:supabase/supabase.dart';\n"
        "final client = SupabaseClient();\n"
        "Future<void> run() async {\n"
        "  final invoke = client.rpc;\n"
        "  await invoke('linked');\n"
        "}\n"
    )
    _write_dart_package(repo, tmp_path / "pub-cache/supabase", source)
    _require_dart_analyzer(tmp_path)

    result = build_dart_semantics(root=tmp_path, target=_target(repo), entries=[_entry()])

    assert result.rpc_analyzed_paths == ()
    assert result.rpc_failed_paths == ("lib/client.dart",)
    assert result.rpc_invocations == ()
    assert any(failure.code == "dart_rpc_enumeration_incomplete" for failure in result.failures)


def test_dart_analyzer_marks_implicit_receiver_rpc_tearoff_enumeration_incomplete(tmp_path: Path) -> None:
    repo = tmp_path / "repos"
    repo.mkdir()
    source = (
        "import 'package:supabase/supabase.dart';\n"
        "final client = SupabaseClient();\n"
        "class Child extends SupabaseClient {\n"
        "  Future<void> run() async {\n"
        "    await client.rpc('direct');\n"
        "    final invoke = rpc;\n"
        "    await invoke('linked');\n"
        "  }\n"
        "}\n"
    )
    _write_dart_package(repo, tmp_path / "pub-cache/supabase", source)
    _require_dart_analyzer(tmp_path)

    result = build_dart_semantics(root=tmp_path, target=_target(repo), entries=[_entry()])

    assert result.rpc_analyzed_paths == ()
    assert result.rpc_failed_paths == ("lib/client.dart",)
    assert [fact.routine for fact in result.rpc_invocations] == ["direct"]
    assert any(failure.code == "dart_rpc_enumeration_incomplete" for failure in result.failures)


def test_dart_rpc_provider_unavailability_is_typed_and_has_no_fallback(tmp_path: Path, monkeypatch) -> None:
    repo = tmp_path / "repos"
    repo.mkdir()
    (repo / "lib").mkdir()
    (repo / "lib/client.dart").write_text("void run() {}\n", encoding="utf-8")
    monkeypatch.setattr("tools.repoctl.graph_dart_provider._dart_executable", lambda: ("", ""))

    result = build_dart_semantics(root=tmp_path, target=_target(repo), entries=[_entry()])

    assert result.rpc_invocations == ()
    assert result.rpc_analyzed_paths == ()
    assert result.rpc_failed_paths == ("lib/client.dart",)
    assert result.failures[0].code == "dart_provider_unavailable"
    assert "rpc" in result.failures[0].capability


def test_unmatched_drop_signature_fails_closed_instead_of_linking_stale_function(tmp_path: Path) -> None:
    repo = tmp_path / "repos"
    (repo / "lib").mkdir(parents=True)
    (repo / "sql").mkdir()
    (repo / "lib/client.dart").write_text("client.rpc('aliased', params: {'id': 1});\n", encoding="utf-8")
    (repo / "sql/functions.sql").write_text(
        "CREATE FUNCTION public.aliased(id integer) RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "DROP FUNCTION IF EXISTS public.aliased(int4);\n",
        encoding="utf-8",
    )
    entries = [
        _entry(),
        CodeIndexEntry(
            path="sql/functions.sql",
            workspace_path="repos/sql/functions.sql",
            language="sql",
            classification="source",
            symbols=[],
            imports=[],
            calls=[],
            deps=[],
            observed_effects=[],
        ),
    ]
    fact = RpcInvocationFact(
        fact_id="rpc:alias-drop",
        repository_id="main",
        path="lib/client.dart",
        provider="dart_analyzer",
        language="dart",
        content_sha256="sha256:" + "0" * 64,
        start_offset=0,
        end_offset=42,
        resolved_callee_identity="package:supabase/src/supabase_client.dart#SupabaseClient.rpc",
        receiver_type="SupabaseClient",
        invocation=RpcInvocationContract(RpcInvocationStatus.VALID),
        schema_selection=RpcSchemaSelection(
            RpcSchemaStatus.UNKNOWN,
            reason_code=RpcSchemaReasonCode.SCHEMA_NOT_OBSERVED,
        ),
        routine_status=RpcRoutineStatus.KNOWN,
        routine="aliased",
        routine_reason_code=None,
        params_status=RpcParamsStatus.COMPLETE,
        param_names=("id",),
        params_reason_code=None,
        syntactic_argument_count=2,
        anchor=SourceAnchor("lib/client.dart", 1, 0, 1, 42),
    )

    result = build_structured_file_relations(
        repo,
        entries,
        dart_rpc_invocations=(fact,),
        dart_rpc_analyzed_paths=("lib/client.dart",),
    )

    assert len(result.rpc_resolutions) == 1
    assert result.rpc_resolutions[0].outcome.value == "incomplete"
    assert result.rpc_resolutions[0].reason_code == "sql_lifecycle_incomplete"
    assert not [relation for relation in result.relations if relation.from_path == "lib/client.dart"]


def test_dart_rpc_links_only_when_schema_evidence_selects_the_target(tmp_path: Path) -> None:
    repo = tmp_path / "repos"
    (repo / "lib").mkdir(parents=True)
    (repo / "sql").mkdir()
    (repo / "lib/client.dart").write_text("client.rpc('selected');\n", encoding="utf-8")
    (repo / "sql/public.sql").write_text(
        "CREATE FUNCTION public.selected() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n",
        encoding="utf-8",
    )
    (repo / "sql/private.sql").write_text(
        "CREATE FUNCTION private.selected() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n",
        encoding="utf-8",
    )
    entries = [
        _entry(),
        CodeIndexEntry(
            path="sql/public.sql",
            workspace_path="repos/sql/public.sql",
            language="sql",
            classification="source",
            symbols=[],
            imports=[],
            calls=[],
            deps=[],
            observed_effects=[],
        ),
        CodeIndexEntry(
            path="sql/private.sql",
            workspace_path="repos/sql/private.sql",
            language="sql",
            classification="source",
            symbols=[],
            imports=[],
            calls=[],
            deps=[],
            observed_effects=[],
        ),
    ]
    fact = RpcInvocationFact(
        fact_id="rpc:known-schema",
        repository_id="main",
        path="lib/client.dart",
        provider="dart_analyzer",
        language="dart",
        content_sha256="sha256:" + "0" * 64,
        start_offset=0,
        end_offset=22,
        resolved_callee_identity="package:supabase/src/supabase_client.dart#SupabaseClient.rpc",
        receiver_type="SupabaseClient",
        invocation=RpcInvocationContract(RpcInvocationStatus.VALID),
        schema_selection=RpcSchemaSelection(RpcSchemaStatus.KNOWN, schema="public"),
        routine_status=RpcRoutineStatus.KNOWN,
        routine="selected",
        routine_reason_code=None,
        params_status=RpcParamsStatus.COMPLETE,
        param_names=(),
        params_reason_code=None,
        syntactic_argument_count=1,
        anchor=SourceAnchor("lib/client.dart", 1, 0, 1, 22),
    )

    result = build_structured_file_relations(
        repo,
        entries,
        dart_rpc_invocations=(fact,),
        dart_rpc_analyzed_paths=("lib/client.dart",),
    )

    assert len(result.rpc_resolutions) == 1
    resolution = result.rpc_resolutions[0]
    assert resolution.outcome.value == "linked"
    assert resolution.linked_target is not None
    assert resolution.linked_target.path == "sql/public.sql"
    rpc_relations = [relation for relation in result.relations if relation.from_path == "lib/client.dart"]
    assert len(rpc_relations) == 1
    assert rpc_relations[0].to_path == "sql/public.sql"


def test_graph_preserves_every_dart_rpc_outcome_and_projects_only_linked_facts(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source = (
        "import 'package:supabase/supabase.dart';\n"
        "class LocalBus {}\n"
        "final client = SupabaseClient();\n"
        "final routineName = 'dynamic_name';\n"
        "final dynamicParams = <String, dynamic>{'extra': 1};\n"
        "Future<void> run() async {\n"
        "  await client.rpc('linked', params: {'id': 1});\n"
        "  await client.rpc('another');\n"
        "  await client.rpc('missing');\n"
        "  await client.rpc('ambiguous', params: {'id': 1});\n"
        "  await client.rpc(routineName);\n"
        "  await client.rpc('partial', params: {'id': 1, ...dynamicParams});\n"
        "  await client.rpc('empty_key', params: {'': 1});\n"
        "  await client.rpc('dropped');\n"
        "  await client.rpc('invalid_extra', 'oops');\n"
        "  await client.rpc('invalid_named', bogus: 1);\n"
        "  await client.rpc('private.foo');\n"
        "  await client.rpc('cross_schema');\n"
        "  await LocalBus().rpc('linked');\n"
        "}\n"
    )
    _write_dart_package(repo, tmp_path / "pub-cache/supabase", source)
    migrations = repo / "supabase/migrations"
    migrations.mkdir(parents=True)
    (migrations / "20240101000000_functions.sql").write_text(
        "CREATE FUNCTION public.linked(id bigint, note text DEFAULT '') RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE FUNCTION public.another() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE FUNCTION public.ambiguous(id bigint) RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE FUNCTION public.ambiguous(id text) RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE FUNCTION public.empty_key(id bigint) RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE FUNCTION public.dropped() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE FUNCTION public.invalid_extra() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE FUNCTION public.invalid_named() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE SCHEMA private;\n"
        "CREATE FUNCTION private.foo() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE FUNCTION public.cross_schema() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE FUNCTION private.cross_schema() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n",
        encoding="utf-8",
    )
    active_migration = migrations / "20240201000000_active.sql"
    active_migration.write_text(
        "DROP FUNCTION public.dropped();\n"
        "CREATE OR REPLACE FUNCTION public.linked(id bigint, note text DEFAULT '') "
        "RETURNS void LANGUAGE sql AS $$ SELECT 2; $$;\n",
        encoding="utf-8",
    )
    _require_dart_analyzer(tmp_path)
    target = require_repo_target(tmp_path, repo_id="main")

    first, problems, first_meta = materialize_graph(tmp_path, target=target)

    assert first is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    assert first_meta["materialization"]["status"] in {"rebuilt", "updated"}
    first_data = _snapshot_data(tmp_path)
    assert first_data["completeness"]["capabilities"]["rpc_resolution"] == "partial"
    rpc = _client_rpc(first_data)
    assert len(rpc["source_facts"]) == 12
    assert len(rpc["resolutions"]) == 12
    assert {item["fact_id"] for item in rpc["source_facts"]} == {item["fact_id"] for item in rpc["resolutions"]}
    facts_by_id = {item["fact_id"]: item for item in rpc["source_facts"]}
    resolution_by_routine = {
        facts_by_id[item["fact_id"]]["routine"].get("value", "<dynamic>"): item
        for item in rpc["resolutions"]
    }
    assert resolution_by_routine["linked"]["outcome"] == "incomplete"
    assert resolution_by_routine["linked"]["reason_code"] == "schema_not_observed"
    assert len(resolution_by_routine["linked"]["candidates"]) == 1
    assert resolution_by_routine["another"]["reason_code"] == "schema_not_observed"
    assert len(resolution_by_routine["another"]["candidates"]) == 1
    assert resolution_by_routine["missing"]["outcome"] == "unresolved"
    assert resolution_by_routine["ambiguous"]["outcome"] == "incomplete"
    assert resolution_by_routine["ambiguous"]["reason_code"] == "schema_not_observed"
    assert len(resolution_by_routine["ambiguous"]["candidates"]) == 2
    assert resolution_by_routine["<dynamic>"]["outcome"] == "incomplete"
    assert resolution_by_routine["partial"]["outcome"] == "incomplete"
    assert resolution_by_routine["empty_key"]["outcome"] == "unresolved"
    assert resolution_by_routine["empty_key"]["reason_code"] == "parameter_contract_mismatch"
    assert resolution_by_routine["dropped"]["outcome"] == "unresolved"
    assert resolution_by_routine["invalid_extra"]["outcome"] == "incomplete"
    assert resolution_by_routine["invalid_extra"]["reason_code"] == "unexpected_argument"
    assert resolution_by_routine["invalid_named"]["outcome"] == "incomplete"
    assert resolution_by_routine["private.foo"]["outcome"] == "unresolved"
    assert resolution_by_routine["cross_schema"]["outcome"] == "incomplete"
    assert resolution_by_routine["cross_schema"]["reason_code"] == "schema_not_observed"
    assert len(resolution_by_routine["cross_schema"]["candidates"]) == 2

    rpc_edges = [
        edge
        for edge in first_data["edges"]
        if edge["kind"] == "USES_FILE"
        and edge["from"].endswith("lib%2Fclient.dart")
        and any(item["relation"] == "sql_rpc_dependency" for item in edge["facts"]["relations"])
    ]
    assert rpc_edges == []

    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["graph", "query", "--file", "lib/client.dart", "--json"]) == 0
    graph_payload = json.loads(capsys.readouterr().out)
    graph_result = graph_payload["data"]["result"]
    assert graph_result["result_digest"].startswith("sha256:")
    assert graph_result["relationship_candidate_count"] == 4
    assert len(graph_result["relationship_candidates"]) == 3
    assert graph_result["relationship_candidates_truncated"] is True
    linked_candidate = next(
        item
        for item in graph_result["relationship_candidates"]
        if item["source"]["runtime_identity"]["value"] == "linked"
    )
    assert linked_candidate["authoritative"] is False
    assert linked_candidate["status"] == "non_authoritative"
    assert linked_candidate["resolution"] == {
        "outcome": "incomplete",
        "reason_code": "schema_not_observed",
    }
    assert linked_candidate["source"]["location"]["start_line"] > 0
    assert linked_candidate["targets"] == [
        {
            "identity": {"kind": "sql_routine", "value": "public.linked"},
            "path": "supabase/migrations/20240201000000_active.sql",
            "location": {"line": 2},
            "parameter_names": ["id", "note"],
            "parameter_types": ["bigint", "text"],
            "required_parameter_names": ["id"],
        }
    ]
    assert linked_candidate["continuations"][0]["selector"] == {
        "kind": "file",
        "value": "supabase/migrations/20240201000000_active.sql",
    }
    assert not any(
        item["source"]["runtime_identity"]["value"] == "empty_key"
        for item in graph_result["relationship_candidates"]
    )

    assert main(["graph", "query", "--file", "lib/client.dart", "--full", "--json"]) == 0
    full_graph_payload = json.loads(capsys.readouterr().out)
    full_graph_result = full_graph_payload["data"]["result"]
    assert full_graph_result["result_digest"] == graph_result["result_digest"]
    assert full_graph_result["relationship_candidate_count"] == 4
    assert len(full_graph_result["relationship_candidates"]) == 4
    assert full_graph_result["relationship_candidates_truncated"] is False

    assert main(["context", "query", "linked rpc", "--mode", "code-location", "--json"]) == 0
    context_payload = json.loads(capsys.readouterr().out)
    context_bundle = context_payload["data"]["bundle"]
    assert context_bundle["relationship_candidate_count"] == 1
    graph_anchor = context_bundle["completeness"]["graph_anchor"]
    assert graph_anchor["status"] == "resolved"
    assert graph_anchor["code"] == "context_graph_anchor_resolved"
    assert graph_anchor["seed_paths"] == ["lib/client.dart"]
    assert graph_anchor["candidate_paths"] == [
        "lib/client.dart",
        "supabase/migrations/20240201000000_active.sql",
        "supabase/migrations/20240101000000_functions.sql",
    ]
    assert graph_anchor["selection_coverage"]["status"] == "complete"
    assert graph_anchor["selection_coverage"]["omitted_paths"] == [
        "supabase/migrations/20240201000000_active.sql",
        "supabase/migrations/20240101000000_functions.sql",
    ]
    assert graph_anchor["seed_anchors"] == [
        {
            "path": "lib/client.dart",
            "provenance": "lexical_file",
            "anchor_strength": "weak",
            "kind": "file",
            "retrieval_lane": "product_source",
            "lexical_rank": 1,
        }
    ]
    context_candidate = next(
        item
        for item in context_bundle["relationship_candidates"]
        if item["source"]["runtime_identity"]["value"] == "linked"
    )
    assert context_candidate["targets"][0]["path"] == "supabase/migrations/20240201000000_active.sql"
    assert context_candidate["resolution"]["reason_code"] == "schema_not_observed"
    assert {
        item["source_ref"]["path"]
        for item in context_bundle["groups"]["likely_change_surface"]
    } == {"repos/lib/client.dart"}

    provider_path = tmp_path / ".repoctl-state/graph/main/providers/dart_analyzer.json"
    provider_data = json.loads(provider_path.read_text(encoding="utf-8"))
    assert len(provider_data["rpc_invocations"]) == 12
    assert provider_data["rpc_analyzed_paths"] == ["lib/client.dart"]

    reused, reused_problems, reused_meta = materialize_graph(tmp_path, target=target)
    assert reused is not None
    assert not [problem for problem in reused_problems if problem.severity == "error"]
    assert reused_meta["materialization"]["status"] == "reused"
    assert reused.snapshot_digest == first.snapshot_digest

    package_config = repo / ".dart_tool/package_config.json"
    original_config = json.loads(package_config.read_text(encoding="utf-8"))
    config = {
        **original_config,
        "generator": "rpc-fixture-broken-resolution",
        "packages": [
            package
            for package in original_config["packages"]
            if package["name"] != "supabase"
        ],
    }
    package_config.write_text(json.dumps(config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    freshness, freshness_problems = graph_materialization_freshness(tmp_path, target=target)
    assert freshness_problems == []
    assert freshness["status"] == "stale"
    assert freshness["changed_paths"] == []
    assert freshness["changed_provider_configs"] == {
        "dart_analyzer": [".dart_tool/package_config.json"]
    }
    assert freshness["provider_stale_paths"] == {
        "dart_analyzer": ["lib/client.dart"]
    }
    assert freshness["semantic_stale_paths"] == ["lib/client.dart"]
    assert freshness["stale_paths"] == ["lib/client.dart"]

    assert main(["graph", "query", "--file", "lib/client.dart", "--json"]) == 0
    provider_stale_graph_payload = json.loads(capsys.readouterr().out)
    assert provider_stale_graph_payload["data"]["result"]["relationship_candidates"] == []
    assert provider_stale_graph_payload["data"]["result"]["relationship_candidate_count"] == 0
    assert any(action["kind"] == "graph_refresh" for action in provider_stale_graph_payload["next_actions"])

    assert main(["context", "query", "linked rpc", "--mode", "code-location", "--json"]) == 0
    provider_stale_context_payload = json.loads(capsys.readouterr().out)
    assert provider_stale_context_payload["data"]["bundle"]["relationship_candidates"] == []
    assert provider_stale_context_payload["data"]["bundle"]["relationship_candidate_count"] == 0
    assert any(
        item["code"] == "context_graph_stale"
        for item in provider_stale_context_payload["data"]["bundle"]["groups"]["warnings_and_completeness"]
    )

    restored_config = {**original_config, "generator": "rpc-fixture-update"}
    package_config.write_text(json.dumps(restored_config, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    refreshed, refresh_problems, refresh_meta = materialize_graph(tmp_path, target=target)
    assert refreshed is not None
    assert not [problem for problem in refresh_problems if problem.severity == "error"]
    assert refresh_meta["materialization"]["updated_paths"]["dart_analyzer"] == ["lib/client.dart"]
    assert len(_client_rpc(_snapshot_data(tmp_path))["source_facts"]) == 12

    active_migration.write_text(active_migration.read_text(encoding="utf-8") + "-- target changed\n", encoding="utf-8")
    assert main(["graph", "query", "--file", "lib/client.dart", "--json"]) == 0
    target_stale_payload = json.loads(capsys.readouterr().out)
    target_stale_candidates = target_stale_payload["data"]["result"]["relationship_candidates"]
    assert "linked" not in {
        item["source"]["runtime_identity"]["value"]
        for item in target_stale_candidates
    }
    assert "another" in {
        item["source"]["runtime_identity"]["value"]
        for item in target_stale_candidates
    }
    assert any(action["kind"] == "graph_refresh" for action in target_stale_payload["next_actions"])

    target_refreshed, target_refresh_problems, _target_refresh_meta = materialize_graph(tmp_path, target=target)
    assert target_refreshed is not None
    assert not [problem for problem in target_refresh_problems if problem.severity == "error"]

    reduced_source = (
        "import 'package:supabase/supabase.dart';\n"
        "final client = SupabaseClient();\n"
        "Future<void> run() => client.rpc('linked', params: {'id': 1});\n"
    )
    (repo / "lib/client.dart").write_text(reduced_source, encoding="utf-8")
    assert main(["graph", "query", "--file", "lib/client.dart", "--json"]) == 0
    source_stale_payload = json.loads(capsys.readouterr().out)
    assert source_stale_payload["data"]["result"]["relationship_candidates"] == []
    assert source_stale_payload["data"]["result"]["relationship_candidate_count"] == 0
    assert any(action["kind"] == "graph_refresh" for action in source_stale_payload["next_actions"])

    assert main(["context", "query", "linked rpc", "--mode", "code-location", "--json"]) == 0
    stale_context_payload = json.loads(capsys.readouterr().out)
    assert stale_context_payload["data"]["bundle"]["relationship_candidates"] == []
    assert stale_context_payload["data"]["bundle"]["relationship_candidate_count"] == 0
    assert any(
        item["code"] == "context_graph_stale"
        for item in stale_context_payload["data"]["bundle"]["groups"]["warnings_and_completeness"]
    )

    reduced, reduced_problems, reduced_meta = materialize_graph(tmp_path, target=target)
    assert reduced is not None
    assert not [problem for problem in reduced_problems if problem.severity == "error"]
    assert reduced_meta["materialization"]["updated_paths"]["dart_analyzer"] == ["lib/client.dart"]
    reduced_data = _snapshot_data(tmp_path)
    assert reduced_data["completeness"]["capabilities"]["rpc_resolution"] == "partial"
    reduced_rpc = _client_rpc(reduced_data)
    assert len(reduced_rpc["source_facts"]) == 1
    assert len(reduced_rpc["resolutions"]) == 1
    assert reduced_rpc["resolutions"][0]["outcome"] == "incomplete"
    assert reduced_rpc["resolutions"][0]["reason_code"] == "schema_not_observed"
    assert len(reduced_rpc["resolutions"][0]["candidates"]) == 1


def test_repometa_eligibility_change_invalidates_materialized_rpc_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    _write_dart_package(
        repo,
        tmp_path / "pub-cache/supabase",
        (
            "import 'package:supabase/supabase.dart';\n"
            "final client = SupabaseClient();\n"
            "Future<void> run() => client.rpc('linked');\n"
        ),
    )
    migration = repo / "supabase/migrations/20240201000000_linked.sql"
    migration.parent.mkdir(parents=True)
    migration.write_text(
        "CREATE FUNCTION public.linked() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n",
        encoding="utf-8",
    )
    _require_dart_analyzer(tmp_path)
    target = require_repo_target(tmp_path, repo_id="main")

    snapshot, problems, _meta = materialize_graph(tmp_path, target=target)
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]

    policy_path = repo / ".repometa/policy.json"
    policy = json.loads(policy_path.read_text(encoding="utf-8"))
    policy["indexing"]["exclude"].append("lib/client.dart")
    policy_path.write_text(json.dumps(policy, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    freshness, freshness_problems = graph_materialization_freshness(tmp_path, target=target)
    assert freshness_problems == []
    assert freshness["changed_paths"] == []
    assert freshness["inventory_stale_paths"] == ["lib/client.dart"]
    assert freshness["stale_paths"] == ["lib/client.dart"]
    assert freshness["stale_path_classifications"] == {"lib/client.dart": "excluded"}

    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    assert main(["graph", "query", "--file", "lib/client.dart", "--json"]) == 0
    graph_payload = json.loads(capsys.readouterr().out)
    assert graph_payload["data"]["result"]["relationship_candidates"] == []
    assert graph_payload["data"]["result"]["relationship_candidate_count"] == 0
    assert any(action["kind"] == "graph_refresh" for action in graph_payload["next_actions"])

    assert main(["context", "query", "linked rpc", "--mode", "code-location", "--json"]) == 0
    context_payload = json.loads(capsys.readouterr().out)
    bundle = context_payload["data"]["bundle"]
    assert bundle["relationship_candidates"] == []
    assert bundle["relationship_candidate_count"] == 0
    assert not [
        item
        for group in bundle["groups"].values()
        for item in group
        if item.get("source_ref", {}).get("path") == "repos/lib/client.dart"
    ]
    assert any(action["kind"] == "graph_refresh" for action in context_payload["next_actions"])


def test_context_exact_rpc_identity_selects_one_same_line_source_fact(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source = (
        "import 'package:supabase/supabase.dart';\n"
        "final client = SupabaseClient();\n"
        "Future<void> run() async { await client.rpc('alpha'); await client.rpc('beta'); }\n"
    )
    _write_dart_package(repo, tmp_path / "pub-cache/supabase", source)
    migrations = repo / "supabase/migrations"
    migrations.mkdir(parents=True)
    (migrations / "20240101000000_functions.sql").write_text(
        "CREATE FUNCTION public.alpha() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE FUNCTION public.beta() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n",
        encoding="utf-8",
    )
    _require_dart_analyzer(tmp_path)

    snapshot, problems, _meta = materialize_graph(tmp_path, target=require_repo_target(tmp_path, repo_id="main"))

    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]
    snapshot_data = _snapshot_data(tmp_path)
    rpc = _client_rpc(snapshot_data)
    facts_by_routine = {
        fact["routine"]["value"]: fact
        for fact in rpc["source_facts"]
        if fact["routine"].get("status") == "known"
    }
    assert facts_by_routine["alpha"]["anchor"]["start_line"] == facts_by_routine["beta"]["anchor"]["start_line"]
    assert facts_by_routine["alpha"]["anchor"]["start_col"] != facts_by_routine["beta"]["anchor"]["start_col"]
    assert not [
        edge
        for edge in snapshot_data["edges"]
        if edge["kind"] == "USES_FILE"
        and any(item.get("relation") == "sql_rpc_dependency" for item in edge.get("facts", {}).get("relations", []))
    ]

    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    for routine in ("alpha", "beta"):
        assert main(["context", "query", routine, "--mode", "code-location", "--full", "--json"]) == 0
        bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
        assert bundle["relationship_candidate_count"] == 1
        assert [item["source"]["runtime_identity"]["value"] for item in bundle["relationship_candidates"]] == [routine]
        exact_source = next(
            item
            for item in bundle["evidence"]
            if item["source_ref"].get("section_kind") == "provider_relationship"
            and "exact_relationship" in item["evidence_kinds"]
        )
        assert exact_source["source_ref"]["source_fact_id"] == bundle["relationship_candidates"][0]["candidate_id"]

    assert main(["context", "query", "lib/client.dart", "--mode", "code-location", "--json"]) == 0
    file_bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    assert file_bundle["relationship_candidate_count"] == 2
    assert {
        item["source"]["runtime_identity"]["value"]
        for item in file_bundle["relationship_candidates"]
    } == {"alpha", "beta"}

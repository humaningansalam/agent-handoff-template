from __future__ import annotations

import json
from pathlib import Path
from urllib.parse import unquote

from tools.repoctl.cli import main
from tools.repoctl.graph_model import file_id
from tools.repoctl.graph_store import materialize_graph
from tools.repoctl.repositories import require_repo_target
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo
from tests.repoctl.workspace.test_check import write_workspace


def _materialize(root: Path) -> None:
    snapshot, problems, _meta = materialize_graph(root, target=require_repo_target(root, repo_id="main"))
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]


def test_graph_resolves_structured_file_relations_from_explicit_syntax(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)

    (repo / "scripts").mkdir()
    (repo / "scripts/start.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "scripts/common.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "scripts/deploy.sh").write_text("#!/bin/sh\nsource ./scripts/common.sh\n", encoding="utf-8")
    (repo / "package.json").write_text('{"name":"structured-fixture"}\n', encoding="utf-8")
    (repo / ".env").write_text("APP_ENV=test\n", encoding="utf-8")
    (repo / "Dockerfile").write_text(
        "FROM alpine:3.21\nCOPY package.json /app/package.json\nCOPY scripts/start.sh /app/start.sh\n",
        encoding="utf-8",
    )
    (repo / "compose.yml").write_text(
        "services:\n  api:\n    build:\n      context: .\n      dockerfile: Dockerfile\n    env_file:\n      - .env\n",
        encoding="utf-8",
    )
    action = repo / ".github/actions/setup/action.yml"
    action.parent.mkdir(parents=True)
    action.write_text("name: setup\nruns:\n  using: composite\n  steps: []\n", encoding="utf-8")
    workflow = repo / ".github/workflows/release.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n  release:\n    runs-on: ubuntu-latest\n    steps:\n"
        "      - uses: ./.github/actions/setup\n"
        "      - run: |\n          docker build -f Dockerfile .\n          scripts/deploy.sh\n",
        encoding="utf-8",
    )

    migrations = repo / "supabase/migrations"
    migrations.mkdir(parents=True)
    initial = migrations / "20240101000000_initial.sql"
    initial.write_text(
        "CREATE TABLE public.jobs (id bigint primary key);\n"
        "CREATE FUNCTION public.claim_job() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n",
        encoding="utf-8",
    )
    upgrade = migrations / "20240201000000_claim_job.sql"
    upgrade.write_text(
        "ALTER TABLE public.jobs ADD COLUMN status text;\n"
        "CREATE OR REPLACE FUNCTION public.claim_job() RETURNS void LANGUAGE sql AS $$\n"
        "  INSERT INTO public.jobs (id) VALUES (1);\n"
        "$$;\n",
        encoding="utf-8",
    )
    (repo / "supabase/seed.sql").write_text("INSERT INTO public.jobs (id) VALUES (2);\n", encoding="utf-8")
    (repo / "src").mkdir()
    (repo / "src/jobs.ts").write_text(
        "import { createClient } from '@supabase/supabase-js';\n"
        "const client = createClient(url, key);\n"
        "export const claim = () => client.rpc('claim_job');\n",
        encoding="utf-8",
    )
    (repo / "src/jobs.dart").write_text(
        "import 'package:supabase/supabase.dart';\n"
        "final SupabaseClient client = SupabaseClient('url', 'key');\n"
        "Future<void> claim() => client.rpc('claim_job');\n",
        encoding="utf-8",
    )

    _materialize(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["graph", "query", "--file", "supabase/seed.sql", "--full", "--json"]) == 0
    snapshot_result = json.loads(capsys.readouterr().out)["data"]["result"]
    edges = [edge for edge in snapshot_result["edges"] if edge["kind"] == "USES_FILE"]
    edge_pairs = {(edge["from"], edge["to"]): edge for edge in edges}

    expected_pairs = {
        ("Dockerfile", "package.json"),
        ("Dockerfile", "scripts/start.sh"),
        ("compose.yml", "Dockerfile"),
        ("compose.yml", ".env"),
        (".github/workflows/release.yml", ".github/actions/setup/action.yml"),
        (".github/workflows/release.yml", "Dockerfile"),
        (".github/workflows/release.yml", "scripts/deploy.sh"),
        ("scripts/deploy.sh", "scripts/common.sh"),
        ("supabase/migrations/20240201000000_claim_job.sql", "supabase/migrations/20240101000000_initial.sql"),
        ("supabase/seed.sql", "supabase/migrations/20240101000000_initial.sql"),
        ("src/jobs.ts", "supabase/migrations/20240201000000_claim_job.sql"),
        ("src/jobs.dart", "supabase/migrations/20240201000000_claim_job.sql"),
    }
    all_edges = {
        (
                unquote(edge["from"].removeprefix("repo:main:file:")),
                unquote(edge["to"].removeprefix("repo:main:file:")),
            ): edge
        for edge in json.loads((tmp_path / ".repoctl-state/graph/main/snapshot.json").read_text(encoding="utf-8"))["edges"]
        if edge["kind"] == "USES_FILE"
    }
    assert expected_pairs <= set(all_edges)
    assert all(edge["source"] == "structured_file_relations" for edge in all_edges.values())
    assert all(edge["facts"]["evidence_type"] == "structured_file_relation" for edge in all_edges.values())
    assert {
        item["relation"]
        for edge in all_edges.values()
        for item in edge["facts"]["relations"]
    } >= {
        "docker_copy_source",
        "compose_dockerfile",
        "compose_env_file",
        "workflow_local_action",
        "workflow_run_file",
        "shell_source_file",
        "sql_schema_dependency",
        "sql_seed_dependency",
        "sql_rpc_dependency",
    }
    assert (file_id("main", "supabase/seed.sql"), file_id("main", "supabase/migrations/20240101000000_initial.sql")) in edge_pairs

    assert main(["graph", "query", "--file", "supabase/seed.sql", "--json"]) == 0
    compact = json.loads(capsys.readouterr().out)["data"]["result"]
    path = next(item for item in compact["paths"] if item["edge"] == "USES_FILE")
    assert path["evidence"] == {
        "type": "structured_file_relation",
        "assertion": "resolved",
        "provider": "structured_file_relations",
        "confidence": "high",
        "completeness": "complete",
        "freshness": "current",
    }
    assert any(item["selector"] == {"kind": "file", "value": "supabase/migrations/20240101000000_initial.sql"} for item in compact["continuations"])

    assert main(["context", "query", "src/jobs.dart", "--repo-id", "main", "--json"]) == 0
    bundle = json.loads(capsys.readouterr().out)["data"]["bundle"]
    visible_paths = {
        item.get("source_ref", {}).get("path")
        for items in bundle["groups"].values()
        for item in items
        if isinstance(item, dict)
    }
    assert "repos/src/jobs.dart" in visible_paths
    assert "repos/supabase/migrations/20240201000000_claim_job.sql" in visible_paths
    assert any(
        item.get("source_ref", {}).get("kind") == "graph_relation" and "--USES_FILE-->" in item.get("excerpt", "")
        for item in bundle["groups"]["callers_and_dependents"]
    )


def test_graph_structured_relations_fail_closed_when_ownership_is_ambiguous_or_dynamic(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)

    (repo / "common.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts/common.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "scripts/run.sh").write_text("source common.sh\nsource \"$SCRIPT\"\n", encoding="utf-8")
    (repo / "Dockerfile").write_text("COPY $SOURCE /app/source\n", encoding="utf-8")
    (repo / "compose.yml").write_text(
        "services:\n  api:\n    build:\n      context: .\n      dockerfile: ${DOCKERFILE}\n",
        encoding="utf-8",
    )
    workflow = repo / ".github/workflows/release.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "jobs:\n  release:\n    steps:\n"
        "      - uses: ${{ github.action_path }}\n"
        "      - run: source \"$SCRIPT\"\n",
        encoding="utf-8",
    )

    migrations = repo / "supabase/migrations"
    migrations.mkdir(parents=True)
    (migrations / "20240101000000_public.sql").write_text(
        "CREATE TABLE public.jobs (id bigint);\n"
        "CREATE FUNCTION public.claim_job() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n",
        encoding="utf-8",
    )
    (migrations / "20240102000000_private.sql").write_text(
        "CREATE TABLE private.jobs (id bigint);\n"
        "CREATE FUNCTION private.claim_job() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n",
        encoding="utf-8",
    )
    (repo / "supabase/seed.sql").write_text("INSERT INTO jobs (id) VALUES (1);\n", encoding="utf-8")
    (repo / "client.ts").write_text(
        "const name = 'claim_job';\nclient.rpc(name);\nclient.rpc('claim_job');\n",
        encoding="utf-8",
    )

    _materialize(tmp_path)

    snapshot = json.loads((tmp_path / ".repoctl-state/graph/main/snapshot.json").read_text(encoding="utf-8"))
    assert not [edge for edge in snapshot["edges"] if edge["kind"] == "USES_FILE"]


def _structured_edge_rows(root: Path) -> list[tuple[str, str, set[str]]]:
    snapshot = json.loads((root / ".repoctl-state/graph/main/snapshot.json").read_text(encoding="utf-8"))
    return [
        (
            unquote(edge["from"].removeprefix("repo:main:file:")),
            unquote(edge["to"].removeprefix("repo:main:file:")),
            {item["relation"] for item in edge["facts"]["relations"]},
        )
        for edge in snapshot["edges"]
        if edge["kind"] == "USES_FILE"
    ]


def test_structured_rpc_relations_require_proven_receiver_and_complete_literal(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)

    (repo / "sql").mkdir()
    (repo / "sql/functions.sql").write_text(
        "CREATE FUNCTION public.claim_job() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE FUNCTION public.claim_() RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n",
        encoding="utf-8",
    )
    (repo / "src").mkdir()
    (repo / "src/local.py").write_text(
        "class LocalBus:\n"
        "    def rpc(self, name):\n"
        "        pass\n\n"
        "LocalBus().rpc('claim_job')\n",
        encoding="utf-8",
    )
    (repo / "src/dynamic.ts").write_text("client.rpc('claim_' + suffix);\n", encoding="utf-8")
    (repo / "src/dynamic.dart").write_text(
        "import 'package:supabase/supabase.dart';\n"
        "final client = SupabaseClient('url', 'key');\n"
        "client.rpc('claim_$suffix');\n",
        encoding="utf-8",
    )
    (repo / "src/client.ts").write_text(
        "import { createClient } from '@supabase/supabase-js';\n"
        "const client = createClient(url, key);\n"
        "client.rpc('claim_job');\n",
        encoding="utf-8",
    )
    (repo / "src/shadow.py").write_text(
        "from supabase import create_client\n"
        "client = create_client('url', 'key')\n"
        "def claim(client):\n"
        "    client.rpc('claim_job')\n",
        encoding="utf-8",
    )
    (repo / "src/shadow.ts").write_text(
        "import { createClient } from '@supabase/supabase-js';\n"
        "const client = createClient(url, key);\n"
        "function claim(client: LocalBus) { client.rpc('claim_job'); }\n"
        "const claimAgain = (client: LocalBus) => client.rpc('claim_job');\n"
        "const nestedClaim = (client: LocalBus) => (() => client.rpc('claim_job'));\n",
        encoding="utf-8",
    )
    (repo / "src/shadow.dart").write_text(
        "import 'package:supabase/supabase.dart';\n"
        "final client = SupabaseClient('url', 'key');\n"
        "void claim(LocalBus client) { client.rpc('claim_job'); }\n"
        "void claimAgain(LocalBus client) => client.rpc('claim_job');\n",
        encoding="utf-8",
    )

    _materialize(tmp_path)
    rows = _structured_edge_rows(tmp_path)

    assert ("src/client.ts", "sql/functions.sql", {"sql_rpc_dependency"}) in rows
    assert not [
        row
        for row in rows
        if row[0] in {
            "src/local.py",
            "src/dynamic.ts",
            "src/dynamic.dart",
            "src/shadow.py",
            "src/shadow.ts",
            "src/shadow.dart",
        }
    ]


def test_structured_sql_relations_preserve_identity_scope_and_unique_ownership(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    sql = repo / "sql"
    sql.mkdir()

    (sql / "a.sql").write_text(
        "CREATE FUNCTION public.claim_job(integer) RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "CREATE TABLE public.\"Jobs\" (id integer);\n"
        "CREATE TABLE public.jobs (id integer);\n",
        encoding="utf-8",
    )
    (sql / "b.sql").write_text(
        "CREATE FUNCTION public.claim_job(text) RETURNS void LANGUAGE sql AS $$ SELECT 1; $$;\n"
        "INSERT INTO public.jobs (id) VALUES (1);\n",
        encoding="utf-8",
    )
    (sql / "cte.sql").write_text(
        "WITH jobs AS (SELECT 1 AS id) SELECT * FROM jobs;\n",
        encoding="utf-8",
    )
    (repo / "client.ts").write_text(
        "import { createClient } from '@supabase/supabase-js';\n"
        "const client = createClient(url, key);\n"
        "client.rpc('claim_job');\n",
        encoding="utf-8",
    )

    _materialize(tmp_path)
    rows = _structured_edge_rows(tmp_path)

    assert ("sql/b.sql", "sql/a.sql", {"sql_schema_dependency"}) in rows
    assert not [row for row in rows if row[0] in {"client.ts", "sql/cte.sql"}]


def test_structured_yaml_relations_require_schema_locations_and_working_directory(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / ".env").write_text("A=1\n", encoding="utf-8")
    (repo / "compose.yml").write_text(
        "services:\n"
        "  api:\n"
        "    environment:\n"
        "      env_file: .env\n",
        encoding="utf-8",
    )
    (repo / "deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts/deploy.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    workflow = repo / ".github/workflows/release.yml"
    workflow.parent.mkdir(parents=True)
    workflow.write_text(
        "env:\n"
        "  run: ./deploy.sh\n"
        "defaults:\n"
        "  run:\n"
        "    working-directory: scripts\n"
        "jobs:\n"
        "  release:\n"
        "    steps:\n"
        "      - run: ./deploy.sh\n",
        encoding="utf-8",
    )

    _materialize(tmp_path)
    rows = _structured_edge_rows(tmp_path)

    assert (".github/workflows/release.yml", "scripts/deploy.sh", {"workflow_run_file"}) in rows
    assert not [row for row in rows if row[0] == "compose.yml"]
    assert (".github/workflows/release.yml", "deploy.sh", {"workflow_run_file"}) not in rows


def test_structured_docker_and_shell_relations_use_typed_file_resolution(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "package.json").write_text("{}\n", encoding="utf-8")
    docker = repo / "docker"
    docker.mkdir()
    (docker / "package.json").write_text("{}\n", encoding="utf-8")
    (docker / "Dockerfile").write_text("COPY package.json /app/package.json\n", encoding="utf-8")
    (repo / "scripts").mkdir()
    (repo / "scripts/tool.py").write_text("print('not a script operand')\n", encoding="utf-8")
    (repo / "a.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "b.sh").write_text("#!/bin/sh\n", encoding="utf-8")
    (repo / "run.sh").write_text(
        "python -c scripts/tool.py\n"
        "source a.sh;./b.sh\n",
        encoding="utf-8",
    )

    _materialize(tmp_path)
    rows = _structured_edge_rows(tmp_path)

    assert ("run.sh", "a.sh", {"shell_source_file"}) in rows
    assert ("run.sh", "b.sh", {"shell_run_file"}) in rows
    assert not [row for row in rows if row[0] == "docker/Dockerfile"]
    assert not [row for row in rows if row[:2] == ("run.sh", "scripts/tool.py")]


def test_structured_parse_failures_are_reported_as_failed_coverage(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "bad.py").write_text("client.rpc(\n", encoding="utf-8")
    (repo / "bad.sh").write_text('source "unterminated\n', encoding="utf-8")

    _materialize(tmp_path)
    snapshot = json.loads((tmp_path / ".repoctl-state/graph/main/snapshot.json").read_text(encoding="utf-8"))
    coverage = snapshot["completeness"]["provider_coverage"]["structured_relations"]

    assert coverage["analyzed_paths"] == []
    assert coverage["failed_paths"] == ["bad.py", "bad.sh"]

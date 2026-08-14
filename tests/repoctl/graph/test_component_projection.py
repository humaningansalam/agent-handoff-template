from __future__ import annotations

from pathlib import Path

from tools.repoctl.component_projection import (
    ComponentManifestRegistry,
    ComponentManifestProvider,
    DEFAULT_COMPONENT_MANIFEST_REGISTRY,
    annotate_graph_query_components,
    component_projection,
)
from tools.repoctl.graph import query_graph
from tools.repoctl.graph_model import GraphEdge, GraphNode, GraphSnapshot, file_id, repository_id
from tools.repoctl.graph_store import materialize_graph
from tools.repoctl.repositories import require_repo_target
from tests.repoctl.meta.test_meta_check import write_repometa
from tests.repoctl.repository.test_repositories import init_repo
from tests.repoctl.workspace.test_check import write_workspace


def component_manifest_fact(text: str, *, manifest_path: str):
    return DEFAULT_COMPONENT_MANIFEST_REGISTRY.manifest_fact(manifest_path, text)


def test_component_projection_reports_only_query_visible_crossings(tmp_path: Path) -> None:
    repo = tmp_path / "repos"
    (repo / "apps/web").mkdir(parents=True)
    (repo / "services/api").mkdir(parents=True)
    (repo / "apps/web/package.json").write_text('{"name":"web"}\n', encoding="utf-8")
    (repo / "services/api/pyproject.toml").write_text('[project]\nname="api"\n', encoding="utf-8")
    repo_id = "main"
    web = file_id(repo_id, "apps/web/src/client.ts")
    api = file_id(repo_id, "services/api/src/server.py")
    snapshot = GraphSnapshot(
        repository={"id": repo_id, "path": "repos", "identity_source": "reserved"},
        sources=[],
        completeness={},
        nodes=[
            GraphNode(repository_id(repo_id), "repository", {"id": repo_id}),
            GraphNode(file_id(repo_id, "apps/web/package.json"), "file", {"path": "apps/web/package.json"}, {"component_manifest": {"provider": "package.json", "name": "web"}}),
            GraphNode(file_id(repo_id, "services/api/pyproject.toml"), "file", {"path": "services/api/pyproject.toml"}, {"component_manifest": {"provider": "pyproject.toml", "name": "api"}}),
            GraphNode(web, "file", {"path": "apps/web/src/client.ts"}),
            GraphNode(api, "file", {"path": "services/api/src/server.py"}),
        ],
        edges=[GraphEdge("USES_FILE", web, api, "resolved", "structured")],
    ).with_digest()

    projection = component_projection(snapshot)
    relation = projection["relations"][0]

    assert projection["subjects"][web] == ["component:package.json:apps/web:web"]
    assert projection["subjects"][api] == ["component:pyproject.toml:services/api:api"]
    assert relation["edge"] == "USES_FILE"
    assert set(relation["crossed_component_ids"]) == {
        "component:package.json:apps/web:web",
        "component:pyproject.toml:services/api:api",
    }

    payload = annotate_graph_query_components(
        {
            "matches": [{"id": web, "kind": "file", "path": "apps/web/src/client.ts"}],
            "nodes": [],
            "edges": [],
            "paths": [],
        },
        projection,
    )
    assert payload["matches"][0]["component_ids"] == ["component:package.json:apps/web:web"]
    assert payload["component_crossing_count"] == 0

    visible = annotate_graph_query_components(
        {
            "matches": [{"id": web, "kind": "file", "path": "apps/web/src/client.ts"}],
            "nodes": [],
            "edges": [],
            "paths": [
                {
                    "edge": "USES_FILE",
                    "from": {"id": web, "kind": "file", "path": "apps/web/src/client.ts"},
                    "to": {"id": api, "kind": "file", "path": "services/api/src/server.py"},
                }
            ],
        },
        projection,
    )
    assert visible["component_crossing_count"] == 1
    assert visible["component_crossings"][0]["edge"] == "USES_FILE"


def test_shipped_manifest_providers_project_declared_field_components() -> None:
    unity_manifest = """{
      "name": "OrbitDuel.Networking.Authority",
      "references": ["OrbitDuel.Simulation"]
    }
    """
    swift_manifest = """
    import PackageDescription
    let package = Package(
        name: "CarryMesh",
        targets: [.target(name: "CarryMesh")]
    )
    """
    gradle_manifest = """
    rootProject.name = "carrymesh-android"
    include(":carrymesh-android", ":qualification-app")
    """

    assert component_manifest_fact(
        unity_manifest,
        manifest_path="game/Assets/OrbitDuel/Networking/Authority/OrbitDuel.Networking.Authority.asmdef",
    ) == {"provider": "unity.asmdef", "name": "OrbitDuel.Networking.Authority"}
    assert component_manifest_fact(
        swift_manifest,
        manifest_path="ios/Package.swift",
    ) == {
        "provider": "Package.swift",
        "components": [
            {"name": "CarryMesh", "root": ""},
            {"name": "CarryMesh", "root": "Sources/CarryMesh"},
        ],
    }
    gradle_fact = component_manifest_fact(
        gradle_manifest,
        manifest_path="android/settings.gradle.kts",
    )
    assert gradle_fact == {
        "provider": "gradle.settings",
        "components": [
            {"name": "carrymesh-android", "root": ""},
            {"name": ":carrymesh-android", "root": "carrymesh-android"},
            {"name": ":qualification-app", "root": "qualification-app"},
        ],
    }

    source_path = "android/carrymesh-android/src/main/kotlin/CarryMesh.kt"
    source_id = file_id("main", source_path)
    snapshot = GraphSnapshot(
        repository={"id": "main", "path": "repos", "identity_source": "reserved"},
        sources=[],
        completeness={},
        nodes=[
            GraphNode(
                file_id("main", "android/settings.gradle.kts"),
                "file",
                {"path": "android/settings.gradle.kts"},
                {"component_manifest": gradle_fact},
            ),
            GraphNode(source_id, "file", {"path": source_path}),
        ],
        edges=[],
    ).with_digest()

    assert component_projection(snapshot)["subjects"][source_id] == [
        "component:gradle.settings:android/carrymesh-android::carrymesh-android",
        "component:gradle.settings:android:carrymesh-android",
    ]

    assert component_manifest_fact(
        'let package = Package(\n'
        '  name: "Demo",\n'
        '  targets: [\n'
        '    .target(name: "Core", dependencies: [.target(name: "Support")], path: "Modules/Core"),\n'
        '    .testTarget(name: "CoreTests"),\n'
        '    .executableTarget(name: "DemoCLI")\n'
        '  ]\n'
        ')\n',
        manifest_path="Package.swift",
    ) == {
        "provider": "Package.swift",
        "components": [
            {"name": "Demo", "root": ""},
            {"name": "Core", "root": "Modules/Core"},
            {"name": "DemoCLI", "root": "Sources/DemoCLI"},
            {"name": "CoreTests", "root": "Tests/CoreTests"},
        ],
    }
    assert component_manifest_fact(
        "name: demo_app # the package name\n",
        manifest_path="pubspec.yaml",
    ) == {"provider": "pubspec.yaml", "name": "demo_app"}
    assert component_manifest_fact(
        'rootProject.name = "demo"\n'
        'val docs = """\ninclude(":fake")\n"""\n'
        'include(":app")\n'
        'project(":app").projectDir = file("actual/app")\n',
        manifest_path="settings.gradle.kts",
    ) == {
        "provider": "gradle.settings",
        "components": [
            {"name": "demo", "root": ""},
            {"name": ":app", "root": "actual/app"},
        ],
    }


def test_graph_result_digest_is_pinned_until_explicit_rebuild(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "apps/web/src").mkdir(parents=True)
    (repo / "apps/web/package.json").write_text('{"name":"web"}\n', encoding="utf-8")
    path = "apps/web/src/client.ts"
    (repo / path).write_text("export const owner = true;\n", encoding="utf-8")
    target = require_repo_target(tmp_path, repo_id="main")
    snapshot, problems, _meta = materialize_graph(tmp_path, target=target, rebuild=True)
    assert snapshot is not None
    assert not [problem for problem in problems if problem.severity == "error"]

    payload, problems = query_graph(snapshot, file=path)
    assert problems == []
    assert payload is not None
    original_digest = payload["result_digest"]
    (repo / "apps/web/package.json").write_text('{"name":"web-renamed"}\n', encoding="utf-8")

    replay, replay_problems = query_graph(snapshot, file=path)
    assert replay_problems == []
    assert replay is not None
    assert replay["matches"][0]["component_ids"] == payload["matches"][0]["component_ids"]
    assert replay["result_digest"] == original_digest

    rebuilt, rebuild_problems, _meta = materialize_graph(tmp_path, target=target, rebuild=True)
    assert rebuilt is not None
    assert not [problem for problem in rebuild_problems if problem.severity == "error"]
    refreshed, refreshed_problems = query_graph(rebuilt, file=path)
    assert refreshed_problems == []
    assert refreshed is not None
    assert refreshed["matches"][0]["component_ids"] != payload["matches"][0]["component_ids"]
    assert refreshed["result_digest"] != original_digest


def test_component_registry_identity_invalidates_a_reused_graph(tmp_path: Path) -> None:
    write_workspace(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    (repo / "features/search/src").mkdir(parents=True)
    (repo / "features/search/module.component").write_text(
        "component = discovery\n",
        encoding="utf-8",
    )
    source_path = "features/search/src/query.engine"
    (repo / source_path).write_text("query engine\n", encoding="utf-8")
    provider_v1 = ComponentManifestProvider(
        id="example.module",
        matches_path=lambda path: path.name == "module.component",
        read_name=lambda text: text.partition("=")[2].strip(),
        revision=1,
    )
    provider_v2 = ComponentManifestProvider(
        id="example.module",
        matches_path=lambda path: path.name == "module.component",
        read_name=lambda text: text.partition("=")[2].strip(),
        revision=2,
    )
    target = require_repo_target(tmp_path, repo_id="main")

    first, first_problems, _meta = materialize_graph(
        tmp_path,
        target=target,
        rebuild=True,
        component_manifest_registry=ComponentManifestRegistry((provider_v1,)),
    )
    assert first is not None
    assert not [problem for problem in first_problems if problem.severity == "error"]

    second, second_problems, second_meta = materialize_graph(
        tmp_path,
        target=target,
        component_manifest_registry=ComponentManifestRegistry((provider_v2,)),
    )
    assert second is not None
    assert not [problem for problem in second_problems if problem.severity == "error"]
    assert second_meta["materialization"]["status"] != "reused"

from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

from tools.repoctl.release import build_release_archive
from tests.repoctl.test_meta_check import write_repometa


def test_build_release_archive_uses_manifest_managed_paths(tmp_path: Path) -> None:
    root = tmp_path / "source"
    out = tmp_path / "dist"
    manifest = {
        "schema_version": 1,
        "package": "agent-workspace-control-plane",
        "version": "0.1.0",
        "replace_paths": ["scripts/repoctl"],
        "create_paths": ["docs/workflows/repo-metadata.md"],
        "preserve_paths": ["repos/**", "docs/BOARD.md"],
    }
    prefix = f"{manifest['package']}-{manifest['version']}"
    (root / "scripts").mkdir(parents=True)
    (root / "docs/workflows").mkdir(parents=True)
    (root / "scripts/repoctl").write_text("tool\n", encoding="utf-8")
    (root / "docs/workflows/repo-metadata.md").write_text("workflow\n", encoding="utf-8")
    (root / "repoctl-upgrade-manifest.json").write_text(
        json.dumps(manifest, indent=2),
        encoding="utf-8",
    )

    archive_path = build_release_archive(root, out)

    assert archive_path.name == f"{prefix}.tar.gz"
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())
    assert f"{prefix}/repoctl-upgrade-manifest.json" in names
    assert f"{prefix}/scripts/repoctl" in names
    assert f"{prefix}/docs/workflows/repo-metadata.md" in names


def test_release_archive_contains_repoctl_repository_module_and_imports(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[2]
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    extract_dir = tmp_path / "extract"
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(extract_dir)
    package_root = extract_dir / f"{manifest['package']}-{manifest['version']}"

    assert (package_root / "tools/repoctl/repositories.py").is_file()
    (package_root / "docs/tasks").mkdir(parents=True, exist_ok=True)
    (package_root / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n## Backlog\n", encoding="utf-8")
    result = subprocess.run(
        ["./scripts/repoctl", "repo", "list", "--json"],
        cwd=package_root,
        env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stderr
    payload = json.loads(result.stdout)
    assert payload["command"] == "repo.list"


def test_release_archive_smokes_context_and_knowledge_commands(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[2]
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    extract_dir = tmp_path / "extract-context"
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(extract_dir)
    package_root = extract_dir / f"{manifest['package']}-{manifest['version']}"

    checks = [
        (["./scripts/repoctl", "context", "--help"], "pack-benchmark-materialize"),
        (["./scripts/repoctl", "field-gate", "run", "--help"], "release-candidate"),
        (["./scripts/repoctl", "knowledge", "--help"], "render"),
        (["./scripts/repoctl", "knowledge", "render", "--help"], "--check"),
    ]
    for command, expected in checks:
        result = subprocess.run(
            command,
            cwd=package_root,
            env={**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")},
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        assert expected in result.stdout


def test_release_archive_runs_context_benchmark_field_gate(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[2]
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    extract_dir = tmp_path / "extract-field-gate"
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(extract_dir)
    package_root = extract_dir / f"{manifest['package']}-{manifest['version']}"
    (package_root / "docs/tasks").mkdir(parents=True, exist_ok=True)
    (package_root / "docs/BOARD.md").write_text("# BOARD\n\n## Board\n\n## Backlog\n", encoding="utf-8")
    (package_root / "repos").mkdir(exist_ok=True)
    subprocess.run(["git", "init"], cwd=package_root / "repos", stdout=subprocess.DEVNULL, check=True)
    write_repometa(package_root / "repos")

    env = {**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")}
    materialize = subprocess.run(
        ["./scripts/repoctl", "context", "benchmark-materialize", "--fixture", "tests/fixtures/context-benchmark", "--repo-id", "main", "--json"],
        cwd=package_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert materialize.returncode == 0, materialize.stderr
    materialize_payload = json.loads(materialize.stdout)
    assert materialize_payload["data"]["totals"]["created"] >= 10
    assert materialize_payload["data"]["totals"]["conflict"] == 0

    benchmark = subprocess.run(
        [
            "./scripts/repoctl",
            "context",
            "benchmark",
            "--fixture",
            "tests/fixtures/context-benchmark",
            "--repo-id",
            "main",
            "--min-recall-at-5",
            "0.85",
            "--require-source-integrity",
            "--require-fixture-corpus",
            "--require-no-forbidden",
            "--json",
        ],
        cwd=package_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert benchmark.returncode == 0, benchmark.stderr
    benchmark_payload = json.loads(benchmark.stdout)
    assert benchmark_payload["data"]["question_count"] == 24
    assert benchmark_payload["data"]["summary"]["mean_recall_at_5"] >= 0.85
    assert benchmark_payload["problems"] == []

    pack_materialize = subprocess.run(
        ["./scripts/repoctl", "context", "pack-benchmark-materialize", "--fixture", "tests/fixtures/context-pack-benchmark", "--json"],
        cwd=package_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert pack_materialize.returncode == 0, pack_materialize.stderr
    pack_materialize_payload = json.loads(pack_materialize.stdout)
    assert pack_materialize_payload["data"]["totals"]["created"] == 5
    assert pack_materialize_payload["data"]["totals"]["conflict"] == 0

    pack_benchmark = subprocess.run(
        [
            "./scripts/repoctl",
            "context",
            "pack-benchmark",
            "--fixture",
            "tests/fixtures/context-pack-benchmark",
            "--repo-id",
            "main",
            "--min-must-read-recall",
            "1.0",
            "--json",
        ],
        cwd=package_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert pack_benchmark.returncode == 0, pack_benchmark.stderr
    pack_benchmark_payload = json.loads(pack_benchmark.stdout)
    assert pack_benchmark_payload["data"]["case_count"] == 5
    assert pack_benchmark_payload["data"]["summary"]["mean_must_read_recall"] == 1.0
    assert pack_benchmark_payload["problems"] == []

    field_gate_output = ".repoctl-state/field-gates/release-candidate.json"
    field_gate = subprocess.run(
        ["./scripts/repoctl", "field-gate", "run", "release-candidate", "--repo-id", "main", "--output", field_gate_output, "--json"],
        cwd=package_root,
        env=env,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert field_gate.returncode == 0, field_gate.stderr
    field_gate_payload = json.loads(field_gate.stdout)
    assert field_gate_payload["data"]["failed_count"] == 0
    assert field_gate_payload["data"]["artifact"]["path"] == field_gate_output
    assert (package_root / field_gate_output).is_file()


def test_release_archive_closes_maintenance_runtime_dependencies(tmp_path: Path) -> None:
    source_root = Path(__file__).resolve().parents[2]
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    prefix = f"{manifest['package']}-{manifest['version']}"
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    with tarfile.open(archive_path, "r:gz") as archive:
        names = set(archive.getnames())

    managed = {"repoctl-upgrade-manifest.json", *manifest["replace_paths"], *manifest["create_paths"]}
    missing = sorted(path for path in managed if f"{prefix}/{path}" not in names)
    assert missing == []

    settings_paths = [".claude/settings.json", ".claude/settings.maintenance.json"]
    hook_commands: set[str] = set()
    agent_names: set[str] = set()
    for rel in settings_paths:
        settings = json.loads((source_root / rel).read_text(encoding="utf-8"))
        for permission in settings.get("permissions", {}).get("allow", []):
            agent_match = re.fullmatch(r"Agent\(([^)]+)\)", permission)
            if agent_match:
                agent_names.add(agent_match.group(1))
            hook_match = re.search(r"\.claude/hooks/[^\" ]+\.sh", permission)
            if hook_match:
                hook_commands.add(hook_match.group(0))
        for hook_entries in settings.get("hooks", {}).values():
            for entry in hook_entries:
                for hook in entry.get("hooks", []):
                    command = str(hook.get("command") or "")
                    hook_match = re.search(r"\.claude/hooks/[^\" ]+\.sh", command)
                    if hook_match:
                        hook_commands.add(hook_match.group(0))
    for hook_path in tuple(hook_commands):
        source = source_root / hook_path
        if source.is_file() and "run_python_module.sh" in source.read_text(encoding="utf-8"):
            hook_commands.add(".claude/hooks/run_python_module.sh")

    for agent_name in agent_names:
        assert f"{prefix}/.claude/agents/{agent_name}.md" in names
    for hook_path in hook_commands:
        assert f"{prefix}/{hook_path}" in names

    required_imports = [
        "tools.agent_harness.safe_artifact_writer",
        "tools.agent_harness.checker",
        "tools.hooks.maintenance.enforce_scope",
        "tools.hooks.maintenance.enforce_final_report",
        "tools.hooks.maintenance.mark_active",
        "tools.runtime.json_io",
        "tools.registries.agent_registry",
    ]
    extract_dir = tmp_path / "extract-maintenance"
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(extract_dir)
    package_root = extract_dir / prefix
    for module in required_imports:
        result = subprocess.run(
            ["python3", "-c", f"import {module}"],
            cwd=package_root,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, f"{module}: {result.stderr}"


def test_release_workflow_pins_actions_and_verifies_existing_tag() -> None:
    source_root = Path(__file__).resolve().parents[2]
    workflow = (source_root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    mutable_uses = re.findall(r"uses:\s+[^@\s]+@v\d+", workflow)

    assert mutable_uses == []
    assert "steps.existing.outputs.tag_exists == 'true' && steps.existing.outputs.release_exists == 'false'" in workflow
    assert "git rev-list -n 1" in workflow
    assert 'test "$TAG_SHA" = "$GITHUB_SHA"' in workflow


def test_generated_knowledge_views_are_ignored_but_records_are_tracked() -> None:
    source_root = Path(__file__).resolve().parents[2]
    ignored = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=source_root,
        input="docs/knowledge/generated/INDEX.md\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert ignored.returncode == 0, ignored.stderr
    assert ignored.stdout.strip() == "docs/knowledge/generated/INDEX.md"

    tracked = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=source_root,
        input="docs/knowledge/records/K-example.json\ndocs/knowledge/events/E-example.json\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert tracked.returncode == 1, tracked.stdout

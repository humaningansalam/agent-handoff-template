from __future__ import annotations

import json
import os
import re
import subprocess
import tarfile
from pathlib import Path

from tools.repoctl.release import build_release_archive


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
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    extract_dir = tmp_path / "extract"
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(extract_dir)
    package_root = extract_dir / f"{manifest['package']}-{manifest['version']}"

    assert (package_root / "tools/repoctl/repositories.py").is_file()
    assert (package_root / "docs/adr/.gitkeep").is_file()
    assert (package_root / "docs/archive/tasks/.gitkeep").is_file()
    assert (package_root / "docs/knowledge/events/.gitkeep").is_file()
    assert (package_root / "docs/knowledge/records/.gitkeep").is_file()
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


def test_release_manifest_manages_every_public_example() -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    managed = {*manifest["replace_paths"], *manifest["create_paths"]}
    example_files = {
        path.relative_to(source_root).as_posix()
        for path in (source_root / "examples").rglob("*")
        if path.is_file()
    }

    assert example_files <= managed


def test_release_manifest_manages_every_repoctl_test() -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    managed = set(manifest["replace_paths"])
    repoctl_tests = {
        path.relative_to(source_root).as_posix()
        for path in (source_root / "tests/repoctl").rglob("*.py")
        if path.is_file()
    }

    assert repoctl_tests <= managed


def test_release_archive_smokes_context_and_knowledge_commands(tmp_path: Path) -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    extract_dir = tmp_path / "extract-context"
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(extract_dir)
    package_root = extract_dir / f"{manifest['package']}-{manifest['version']}"

    assert (package_root / "tests/fixtures/context-benchmark/attribution-cases.json").is_file()

    checks = [
        (["./scripts/repoctl", "context", "--help"], "query"),
        (["./scripts/repoctl", "graph", "--help"], "query"),
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


def test_release_workflow_pins_actions_and_verifies_existing_tag() -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    workflow = (source_root / ".github/workflows/release.yml").read_text(encoding="utf-8")
    mutable_uses = re.findall(r"uses:\s+[^@\s]+@v\d+", workflow)

    assert mutable_uses == []
    assert "if: steps.existing.outputs.tag_exists == 'true'" in workflow
    assert "git rev-list -n 1" in workflow
    assert 'test "$TAG_SHA" = "$GITHUB_SHA"' in workflow
    verify_tests = workflow.split("- name: Verify tests", 1)[1].split("- name: Verify workspace contracts", 1)[0]
    verify_contracts = workflow.split("- name: Verify workspace contracts", 1)[1].split("- name: Build release artifact", 1)[0]
    assert "if:" not in verify_tests
    assert "if:" not in verify_contracts


def test_generated_knowledge_views_are_not_ignored_and_records_are_not_ignored() -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    generated = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=source_root,
        input="docs/knowledge/generated/INDEX.md\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert generated.returncode == 1, generated.stdout

    records = subprocess.run(
        ["git", "check-ignore", "--stdin"],
        cwd=source_root,
        input="docs/knowledge/records/K-example.json\ndocs/knowledge/events/E-example.json\n",
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert records.returncode == 1, records.stdout

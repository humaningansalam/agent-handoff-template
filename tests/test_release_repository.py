from __future__ import annotations

import io
import json
import os
import re
import subprocess
import sys
import tarfile
from pathlib import Path

import pytest

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


def test_release_rejects_unsafe_identity_and_preserves_existing_artifact_on_failure(tmp_path: Path) -> None:
    root = tmp_path / "source"
    out = tmp_path / "dist"
    (root / "scripts").mkdir(parents=True)
    (root / "scripts/repoctl").write_text("tool\n", encoding="utf-8")
    manifest_path = root / "repoctl-upgrade-manifest.json"
    manifest = {
        "schema_version": 1,
        "package": "../escaped",
        "version": "0.1.0",
        "replace_paths": ["scripts/repoctl"],
        "create_paths": [],
        "preserve_paths": [],
    }
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")

    with pytest.raises(SystemExit, match="safe archive component"):
        build_release_archive(root, out)

    assert not out.exists()
    manifest["package"] = "agent-workspace-control-plane"
    manifest["replace_paths"].append("missing.py")
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    out.mkdir()
    existing = out / "agent-workspace-control-plane-0.1.0.tar.gz"
    existing.write_bytes(b"known-good")

    with pytest.raises(SystemExit, match="missing or not a regular file"):
        build_release_archive(root, out)

    assert existing.read_bytes() == b"known-good"
    assert not list(out.glob("*.tmp"))


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


def test_release_archive_excludes_python_tests_and_keeps_field_gate_fixtures(tmp_path: Path) -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    prefix = f"{manifest['package']}-{manifest['version']}/"
    with tarfile.open(archive_path, "r:gz") as archive:
        members = [member for member in archive.getmembers() if member.isfile()]
    relative_names = {member.name.removeprefix(prefix) for member in members}
    fixtures = {
        name for name in relative_names if name.startswith("tests/fixtures/")
    }

    assert any((source_root / "tests/repoctl").rglob("*.py"))
    assert not any(name.startswith("tests/") and name.endswith(".py") for name in relative_names)
    assert fixtures == {
        "tests/fixtures/context-benchmark-multirepo/corpus.json",
        "tests/fixtures/context-benchmark-multirepo/expected-sources.json",
        "tests/fixtures/context-benchmark-multirepo/questions.jsonl",
        "tests/fixtures/context-benchmark/corpus.json",
        "tests/fixtures/context-benchmark/expected-sources.json",
        "tests/fixtures/context-benchmark/questions.jsonl",
        "tests/fixtures/context-pack-benchmark/cases.json",
        "tests/fixtures/context-pack-benchmark/tasks.json",
    }
    assert len(members) < 151
    assert sum(member.size for member in members) < 5_588_918


def test_release_archive_smokes_context_and_knowledge_commands(tmp_path: Path) -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    manifest = json.loads((source_root / "repoctl-upgrade-manifest.json").read_text(encoding="utf-8"))
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    extract_dir = tmp_path / "extract-context"
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(extract_dir)
    package_root = extract_dir / f"{manifest['package']}-{manifest['version']}"

    assert (package_root / "tests/fixtures/context-benchmark/corpus.json").is_file()

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
    assert "release_exists" not in workflow
    assert "if: steps.existing" not in workflow
    assert 'git fetch --force origin "refs/tags/$TAG:refs/tags/$TAG"' in workflow
    assert "git rev-list -n 1" in workflow
    assert '= "$GITHUB_SHA"' in workflow
    verify_tests = workflow.split("- name: Verify tests", 1)[1].split("- name: Verify workspace contracts", 1)[0]
    verify_contracts = workflow.split("- name: Verify workspace contracts", 1)[1].split("- name: Build release artifact", 1)[0]
    assert "if:" not in verify_tests
    assert "if:" not in verify_contracts


def test_release_help_is_read_only_and_archives_are_reproducible(tmp_path: Path) -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    result = subprocess.run(
        [sys.executable, "-m", "tools.repoctl.release", "--help"],
        cwd=tmp_path,
        env={**os.environ, "PYTHONPATH": source_root.as_posix()},
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0
    assert "usage:" in result.stdout
    assert not (tmp_path / "--help").exists()
    first = build_release_archive(source_root, tmp_path / "first")
    second = build_release_archive(source_root, tmp_path / "second")
    assert first.read_bytes() == second.read_bytes()


def test_v090_release_upgrade_and_fresh_postflight(tmp_path: Path) -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    archive_path = build_release_archive(source_root, tmp_path / "dist")
    release_parent = tmp_path / "release"
    with tarfile.open(archive_path, "r:gz") as archive:
        archive.extractall(release_parent)
    release_root = next(release_parent.iterdir())
    target = tmp_path / "target"
    target.mkdir()
    archived_v090 = subprocess.run(
        ["git", "archive", "v0.9.0"],
        cwd=source_root,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )
    assert archived_v090.returncode == 0, archived_v090.stderr.decode()
    with tarfile.open(fileobj=io.BytesIO(archived_v090.stdout)) as archive:
        archive.extractall(target)
    env = {**os.environ, "UV_CACHE_DIR": str(tmp_path / "uv-cache")}

    def repoctl(root: Path, *args: str) -> dict:
        result = subprocess.run(
            [str(root / "scripts/repoctl"), *args, "--json"],
            cwd=root,
            env=env,
            text=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            check=False,
        )
        assert result.returncode == 0, result.stderr
        return json.loads(result.stdout)

    plan_file = tmp_path / "plan.json"
    plan = repoctl(
        release_root,
        "upgrade",
        "plan",
        "--workspace-root",
        str(target),
        "--from",
        str(release_root),
        "--output",
        str(plan_file),
    )
    assert plan["data"]["source_version"] == "0.10.0"
    assert plan["data"]["operations"]
    repoctl(release_root, "upgrade", "apply", "--workspace-root", str(target), "--plan-file", str(plan_file))
    version = repoctl(target, "version")
    assert version["data"]["pyproject_version"] == version["data"]["manifest_version"] == "0.10.0"
    assert repoctl(target, "upgrade", "postflight")["ok"] is True
    assert not list((target / "tests").rglob("*.py"))
    assert repoctl(release_root, "upgrade", "plan", "--workspace-root", str(release_root), "--from", str(release_root))["data"]["operations"] == []


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

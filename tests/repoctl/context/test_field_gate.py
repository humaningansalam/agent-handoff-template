from __future__ import annotations

import json
import shutil
from pathlib import Path

import pytest

from tools.repoctl.cli import main
from tests.repoctl.context_test_helpers import _write_context_docs, init_repo, write_workspace


def _copy_release_fixtures(root: Path) -> None:
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", root / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", root / "tests/fixtures/context-pack-benchmark")


def test_release_candidate_field_gate_runs_real_quality_checks_and_cleans_fixture_state(
    tmp_path: Path,
    monkeypatch,
    capsys,
) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    _copy_release_fixtures(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    output = tmp_path / ".repoctl-state/field-gates/release-candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    gates = {gate["name"]: gate for gate in payload["data"]["gates"]}
    assert payload["data"]["failed_count"] == 0
    assert gates["context_benchmark"]["summary"]["mean_recall_at_5"] >= 0.85
    assert gates["context_pack_benchmark"]["summary"]["mean_must_read_recall"] == 1.0
    assert json.loads(output.read_text(encoding="utf-8"))["data"]["artifact"]["path"] == ".repoctl-state/field-gates/release-candidate.json"
    assert not (repo / ".repometa").exists()
    assert not (repo / "auth").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260624020202Z--pack-benchmark.md").exists()


def test_release_candidate_field_gate_cleans_materialized_state_when_a_gate_raises(
    tmp_path: Path,
    monkeypatch,
) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    def fail_benchmark(*args, **kwargs):
        raise RuntimeError("benchmark failed")

    monkeypatch.setattr("tools.repoctl.cli.run_context_benchmark", fail_benchmark)

    with pytest.raises(RuntimeError, match="benchmark failed"):
        main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--json"])

    assert not (repo / "auth").exists()
    assert not (repo / ".repometa").exists()

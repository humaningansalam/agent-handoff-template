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
    source_annotation = repo / ".repometa/annotations/preexisting.json"
    source_annotation.parent.mkdir(parents=True)
    source_annotation.write_text("{}\n", encoding="utf-8")
    _copy_release_fixtures(tmp_path)
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    output = tmp_path / ".repoctl-state/field-gates/release-candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 0

    compact_output = capsys.readouterr().out
    payload = json.loads(compact_output)
    gates = {gate["name"]: gate for gate in payload["data"]["gates"]}
    assert payload["data"]["scope"] == "workspace_control_plane"
    assert payload["data"]["applicability"] == "repoctl_release_candidate"
    assert payload["data"]["product_readiness"] == "not_evaluated"
    assert payload["data"]["failed_count"] == 0
    assert "reviewed_knowledge_check" in gates
    assert gates["context_benchmark"]["summary"]["mean_recall_at_5"] >= 0.85
    assert "knowledge_result_questions" not in gates["context_benchmark"]["summary"]
    assert "by_category" not in gates["context_benchmark"]["summary"]
    assert gates["context_pack_benchmark"]["summary"]["mean_must_read_recall"] == 1.0
    artifact_output = output.read_text(encoding="utf-8")
    artifact = json.loads(artifact_output)
    assert artifact["data"]["artifact"]["path"] == ".repoctl-state/field-gates/release-candidate.json"
    artifact_gates = {gate["name"]: gate for gate in artifact["data"]["gates"]}
    assert "by_category" in artifact_gates["context_benchmark"]["summary"]
    assert all(
        "mean_knowledge_recall_at_5" not in category
        for category in artifact_gates["context_benchmark"]["summary"]["by_category"].values()
    )
    assert len(compact_output.encode("utf-8")) < len(artifact_output.encode("utf-8"))
    assert source_annotation.read_text(encoding="utf-8") == "{}\n"
    assert not (repo / ".repometa/policy.json").exists()
    assert not (repo / "auth").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260624020202Z--pack-benchmark.md").exists()

    assert main(["check", "--json"]) == 0
    check_payload = json.loads(capsys.readouterr().out)
    release_gate = check_payload["data"]["field_gates"]["release_candidate"]
    assert release_gate["scope"] == "workspace_control_plane"
    assert release_gate["applicability"] == "repoctl_release_candidate"
    assert release_gate["product_readiness"] == "not_evaluated"


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

    with pytest.raises(RuntimeError):
        main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--json"])

    assert not (repo / "auth").exists()
    assert not (repo / ".repometa").exists()

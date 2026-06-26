from __future__ import annotations

import json
import shutil
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import digest_data
from tests.repoctl.context_test_helpers import (
    _write_context_docs,
    init_repo,
    write_repometa,
    write_settings,
    write_workspace,
)


def test_release_candidate_field_gate_runner_writes_summary_artifact(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    output = tmp_path / ".repoctl-state/field-gates/release-candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    artifact = json.loads(output.read_text(encoding="utf-8"))
    assert payload["command"] == "field-gate run"
    assert payload["data"]["schema"] == "repoctl.field_gate.release_candidate"
    assert payload["data"]["failed_count"] == 0
    assert payload["data"]["gate_count"] == 7
    assert artifact["data"]["artifact"]["path"] == ".repoctl-state/field-gates/release-candidate.json"
    gate_names = [gate["name"] for gate in payload["data"]["gates"]]
    assert gate_names == [
        "workspace_check",
        "repository_check",
        "knowledge_check",
        "context_benchmark_materialize",
        "context_benchmark",
        "context_pack_benchmark_materialize",
        "context_pack_benchmark",
    ]
    context_summary = next(gate["summary"] for gate in payload["data"]["gates"] if gate["name"] == "context_benchmark")
    pack_summary = next(gate["summary"] for gate in payload["data"]["gates"] if gate["name"] == "context_pack_benchmark")
    assert context_summary["mean_recall_at_5"] >= 0.85
    assert pack_summary["mean_must_read_recall"] == 1.0


def test_release_candidate_field_gate_rejects_invalid_output_before_mutation(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    outside = tmp_path.parent / "release-candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", outside.as_posix(), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "field_gate_output_outside_workspace"
    assert not outside.exists()
    assert not (tmp_path / "repos/auth/flow.py").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260624020202Z--pack-benchmark.md").exists()


def test_release_candidate_field_gate_fails_on_stale_reviewed_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    (tmp_path / "docs/adr/evidence-context-authority-v0.md").write_text("# Drifted\n\n## Decision\n\nChanged after approval.\n", encoding="utf-8")

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    knowledge_gate = next(gate for gate in payload["data"]["gates"] if gate["name"] == "knowledge_check")
    assert knowledge_gate["ok"] is False
    assert knowledge_gate["summary"]["record_error_count"] == 1
    assert knowledge_gate["summary"]["record_problem_codes"] == {"knowledge_source_digest_drift": 1}
    assert any(problem["code"] == "knowledge_source_digest_drift" for problem in knowledge_gate["problems"])
    assert any(problem["code"] == "field_gate_failed" and problem["message"].endswith("knowledge_check") for problem in payload["problems"])


def test_release_candidate_field_gate_runner_includes_multirepo_isolation_when_configured(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    init_repo(tmp_path / "repos/web")
    init_repo(tmp_path / "repos/api")
    write_repometa(tmp_path / "repos/web")
    write_repometa(tmp_path / "repos/api")
    write_settings(tmp_path, {"repositories": [{"id": "web", "path": "repos/web"}, {"id": "api", "path": "repos/api"}]})
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-benchmark-multirepo", tmp_path / "tests/fixtures/context-benchmark-multirepo")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "web", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    gate_names = [gate["name"] for gate in payload["data"]["gates"]]
    assert "context_benchmark" not in gate_names
    assert "context_benchmark_multirepo_materialize" in gate_names
    assert "context_benchmark_multirepo_isolation" in gate_names
    multi_summary = next(gate["summary"] for gate in payload["data"]["gates"] if gate["name"] == "context_benchmark_multirepo_isolation")
    assert multi_summary["question_count"] == 8
    assert multi_summary["cross_repo_ref_count"] == 0
    assert multi_summary["by_category"]["multi-repo-isolation"]["mean_packed_recall"] == 1.0
    assert payload["data"]["failed_count"] == 0


def test_field_gate_compare_detects_gate_regression_and_digest_tamper(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    baseline = tmp_path / ".repoctl-state/field-gates/baseline.json"
    candidate = tmp_path / ".repoctl-state/field-gates/candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    assert main(["field-gate", "compare", "--baseline", baseline.as_posix(), "--candidate", baseline.as_posix(), "--max-failed-count-increase", "0", "--require-same-gates", "--require-no-gate-regressions", "--json"]) == 0
    compare_payload = json.loads(capsys.readouterr().out)
    assert compare_payload["command"] == "field-gate compare"
    assert compare_payload["data"]["failed_count_delta"]["delta"] == 0
    assert compare_payload["data"]["missing_gates"] == []
    assert compare_payload["data"]["new_gates"] == []

    regressed = json.loads(baseline.read_text(encoding="utf-8"))
    regressed["data"]["failed_count"] = 1
    regressed["data"]["passed_count"] -= 1
    regressed["data"]["gates"][-1]["ok"] = False
    regressed["data"]["gates"][-1]["problems"] = [{"severity": "error", "code": "synthetic", "message": "synthetic"}]
    regressed["data"]["run_digest"] = digest_data({key: value for key, value in regressed["data"].items() if key not in {"run_digest", "artifact"}})
    candidate.write_text(json.dumps(regressed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["field-gate", "compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-failed-count-increase", "0", "--require-same-gates", "--require-no-gate-regressions", "--json"]) == 1
    failed_payload = json.loads(capsys.readouterr().out)
    codes = [problem["code"] for problem in failed_payload["problems"]]
    assert "field_gate_failed_count_regressed" in codes
    assert "field_gate_gate_regressed" in codes

    tampered = json.loads(candidate.read_text(encoding="utf-8"))
    tampered["data"]["failed_count"] = 0
    candidate.write_text(json.dumps(tampered, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["field-gate", "compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--json"]) == 1
    tamper_payload = json.loads(capsys.readouterr().out)
    assert tamper_payload["problems"][0]["code"] == "field_gate_artifact_digest_mismatch"


def test_field_gate_compare_accepts_failed_run_artifact(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    baseline = tmp_path / ".repoctl-state/field-gates/baseline.json"
    candidate = tmp_path / ".repoctl-state/field-gates/candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    assert main(["field-gate", "cleanup", "--artifact", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    assert main(["knowledge", "candidate", "build", "--source", "docs/adr/evidence-context-authority-v0.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    (tmp_path / "docs/adr/evidence-context-authority-v0.md").write_text("# Drifted\n\n## Decision\n\nChanged after approval.\n", encoding="utf-8")

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", candidate.as_posix(), "--json"]) == 1
    failed_run_payload = json.loads(capsys.readouterr().out)
    assert failed_run_payload["ok"] is False
    assert candidate.is_file()

    assert main(["field-gate", "compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-failed-count-increase", "0", "--require-same-gates", "--require-no-gate-regressions", "--json"]) == 1
    compare_payload = json.loads(capsys.readouterr().out)
    codes = [problem["code"] for problem in compare_payload["problems"]]
    assert "field_gate_artifact_failed" not in codes
    assert "field_gate_failed_count_regressed" in codes
    assert "field_gate_gate_regressed" in codes
    assert compare_payload["data"]["failed_count_delta"]["baseline"] == 0
    assert compare_payload["data"]["failed_count_delta"]["candidate"] >= 1
    assert compare_payload["data"]["failed_count_delta"]["delta"] >= 1
    knowledge_delta = next(delta for delta in compare_payload["data"]["gate_deltas"] if delta["name"] == "knowledge_check")
    assert knowledge_delta["ok"]["regressed"] is True
    assert knowledge_delta["problem_count"]["candidate"] == 1


def test_field_gate_cleanup_removes_only_recorded_created_files(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    shutil.copytree(source_root / "tests/fixtures/context-pack-benchmark", tmp_path / "tests/fixtures/context-pack-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    artifact = tmp_path / ".repoctl-state/field-gates/release-candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", artifact.as_posix(), "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    cleanup_count = sum(len(gate.get("cleanup", [])) for gate in payload["data"]["gates"])
    assert cleanup_count == 17
    assert (tmp_path / "repos/auth/flow.py").is_file()
    assert (tmp_path / "docs/archive/tasks/T-20260624020202Z--pack-benchmark.md").is_file()

    assert main(["field-gate", "cleanup", "--artifact", artifact.as_posix(), "--json"]) == 0
    cleanup_payload = json.loads(capsys.readouterr().out)
    assert cleanup_payload["data"]["removed_count"] == 17
    assert not (tmp_path / "repos/auth/flow.py").exists()
    assert not (tmp_path / "repos/auth").exists()
    assert not (tmp_path / "docs/archive/tasks/T-20260624020202Z--pack-benchmark.md").exists()
    assert (tmp_path / "docs/archive/tasks").is_dir()

    assert main(["field-gate", "cleanup", "--artifact", artifact.as_posix(), "--json"]) == 0
    second_payload = json.loads(capsys.readouterr().out)
    assert second_payload["data"]["removed_count"] == 0
    assert second_payload["data"]["skipped_count"] == 17


def test_field_gate_cleanup_refuses_changed_created_file(tmp_path: Path, monkeypatch, capsys) -> None:
    write_workspace(tmp_path)
    _write_context_docs(tmp_path)
    repo = tmp_path / "repos"
    init_repo(repo)
    write_repometa(repo)
    source_root = next(parent for parent in Path(__file__).resolve().parents if (parent / "scripts/repoctl").is_file())
    shutil.copytree(source_root / "tests/fixtures/context-benchmark", tmp_path / "tests/fixtures/context-benchmark")
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    artifact = tmp_path / ".repoctl-state/field-gates/release-candidate.json"

    assert main(["field-gate", "run", "release-candidate", "--repo-id", "main", "--output", artifact.as_posix(), "--json"]) == 0
    capsys.readouterr()
    (tmp_path / "repos/auth/flow.py").write_text("user changed file\n", encoding="utf-8")

    assert main(["field-gate", "cleanup", "--artifact", artifact.as_posix(), "--json"]) == 1
    payload = json.loads(capsys.readouterr().out)
    assert any(problem["code"] == "field_gate_cleanup_digest_mismatch" and problem["path"] == "repos/auth/flow.py" for problem in payload["problems"])
    assert (tmp_path / "repos/auth/flow.py").read_text(encoding="utf-8") == "user changed file\n"


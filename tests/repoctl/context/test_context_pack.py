from __future__ import annotations

import json
from pathlib import Path

from tools.repoctl.cli import main
from tools.repoctl.graph_model import digest_data
from tests.repoctl.context_test_helpers import (
    _setup_context_workspace,
    _write_context_pack_task,
    _write_pack_benchmark_task,
)


def test_context_pack_groups_task_evidence(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622010101Z",
        slug="context-pack",
        title="Use Evidence Context for Graph authority",
        query="Graph authority context",
        goal="Explain why Graph remains non-authoritative.",
        first_command='./scripts/repoctl context query "Graph authority" --json',
    )
    output = tmp_path / ".repoctl-state/context-pack/T-20260622010101Z.json"
    assert main(["context", "pack", "--task", "T-20260622010101Z", "--repo-id", "main", "--budget-tokens", "1200", "--output", output.as_posix(), "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    artifact = json.loads(output.read_text(encoding="utf-8"))
    data = payload["data"]
    assert artifact == payload
    assert payload["command"] == "context pack"
    assert data["authoritative"] is False
    assert data["pack_digest"].startswith("sha256:")
    assert data["artifact"] == {
        "path": ".repoctl-state/context-pack/T-20260622010101Z.json",
        "pack_digest": data["pack_digest"],
    }
    assert data["seed"]["source"] == "task_fields_for_retrieval_only"
    assert any(item["source_ref"]["path"] == "docs/contracts/repoctl-context-contract.md" for item in data["groups"]["must_read"])
    assert data["groups"]["reviewed_knowledge"] == []
    assert data["bundle"]["budget"]["estimated_tokens"] <= 1200
    assert data["metrics"]["group_counts"]["must_read"] == len(data["groups"]["must_read"])
    assert data["metrics"]["group_counts"]["reviewed_knowledge"] == 0
    assert data["metrics"]["unique_must_read_source_count"] >= 1
    assert data["metrics"]["estimated_tokens"] == data["bundle"]["budget"]["estimated_tokens"]
    assert data["metrics"]["requested_tokens"] == 1200
    assert any(ref["path"] == "docs/contracts/repoctl-context-contract.md" for ref in data["metrics"]["must_read_source_refs"])
    assert payload["warnings"][0]["code"] == "context_pack_not_authoritative"


def test_context_pack_markdown_is_agent_consumable(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "auth").mkdir()
    (repo / "auth/flow.py").write_text(
        'def validate_token(token: str) -> bool:\n    return token == "ok"\n\n\ndef login(token: str) -> str:\n    if validate_token(token):\n        return "ok"\n    return "denied"\n',
        encoding="utf-8",
    )
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622010103Z",
        slug="agent-pack",
        title="Change validate token behavior",
        query="What calls validate_token?",
        goal="Change validate_token behavior without missing callers.",
        reviewed="repos/auth/flow.py",
        chosen="repos/auth/flow.py",
        first_command="./scripts/repoctl context pack --task T-20260622010103Z --repo-id main --format markdown",
    )
    output = tmp_path / ".repoctl-state/context-pack/T-20260622010103Z.md"

    assert main(["context", "pack", "--task", "T-20260622010103Z", "--repo-id", "main", "--format", "markdown", "--output", output.as_posix()]) == 0

    stdout = capsys.readouterr().out
    artifact = output.read_text(encoding="utf-8")
    assert stdout == artifact
    assert "# Agent Context Pack" in artifact
    assert "## Task Startup Order" in artifact
    assert "## Definitions, Callers, Imports, Dependents" in artifact
    assert "login --CALLS--> validate_token" in artifact
    assert "Context Pack is read-only evidence" not in artifact

    assert main(["context", "pack", "--task", "T-20260622010103Z", "--repo-id", "main", "--json"]) == 0
    payload = json.loads(capsys.readouterr().out)
    groups = payload["data"]["groups"]
    assert "likely_change" in groups
    assert "impact" in groups
    assert "verification" in groups
    assert "warnings" in groups
    assert any("login --CALLS--> validate_token" in str(item.get("excerpt", "")) for item in groups["impact"])


def test_context_pack_warns_on_incomplete_graph_code_facts(tmp_path: Path, monkeypatch, capsys) -> None:
    repo = _setup_context_workspace(tmp_path, monkeypatch)
    (repo / "broken.py").write_text("def broken(:\n", encoding="utf-8")
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622010102Z",
        slug="context-pack-parse-warning",
        title="Inspect parse warning context",
        query="parse warning",
        goal="Inspect parse warning context.",
        reviewed="repos/broken.py",
        chosen="repos/broken.py",
        first_command="./scripts/repoctl context pack --task T-20260622010102Z --repo-id main --json",
    )
    assert main(["context", "pack", "--task", "T-20260622010102Z", "--repo-id", "main", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["bundle"]["completeness"]["graph_completeness"]["parse_error_count"] == 1
    assert any(warning["code"] == "context_pack_graph_code_facts_incomplete" for warning in payload["warnings"])


def test_context_pack_rejects_output_symlink_escape(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622011111Z",
        slug="context-pack-boundary",
        title="Keep context pack output inside workspace",
        query="context pack boundary",
        goal="Reject context pack output outside the workspace.",
    )
    monkeypatch.setattr("tools.repoctl.cli.find_workspace_root", lambda: tmp_path)
    escape = tmp_path.parent / f"{tmp_path.name}-context-pack-escape"
    escape.mkdir()
    symlink = tmp_path / ".repoctl-state/context-pack"
    symlink.parent.mkdir(parents=True, exist_ok=True)
    symlink.symlink_to(escape, target_is_directory=True)

    assert main(["context", "pack", "--task", "T-20260622011111Z", "--repo-id", "main", "--output", ".repoctl-state/context-pack/out.json", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "context_pack_output_outside_workspace"
    assert not (escape / "out.json").exists()


def test_context_pack_does_not_write_failed_artifact(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622012121Z",
        slug="failed-pack",
        title="Reject failed context pack artifact",
        query="source authority knowledge",
        goal="Do not write failed context pack artifacts.",
    )
    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    event_id = json.loads(capsys.readouterr().out)["data"]["event"]["id"]
    event_path = tmp_path / "docs/knowledge/events" / f"{event_id}.json"
    event = json.loads(event_path.read_text(encoding="utf-8"))
    event["record_digest"] = "sha256:" + "6" * 64
    event["event_digest"] = digest_data({key: value for key, value in event.items() if key != "event_digest"})
    event_path.write_text(json.dumps(event, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    output = tmp_path / ".repoctl-state/context-pack/failed.json"

    assert main(["context", "pack", "--task", "T-20260622012121Z", "--repo-id", "main", "--output", output.as_posix(), "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["problems"][0]["code"] == "knowledge_event_record_digest_mismatch"
    assert not output.exists()


def test_context_pack_groups_reviewed_knowledge(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622020202Z",
        slug="knowledge-pack",
        title="Use reviewed knowledge for source authority",
        query="source authority knowledge",
        goal="Use reviewed knowledge source authority.",
    )
    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    old_record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]
    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    replacement_candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", replacement_candidate_id, "--repo-id", "main", "--supersedes", old_record_id, "--json"]) == 0
    record_id = json.loads(capsys.readouterr().out)["data"]["record"]["id"]

    assert main(["context", "pack", "--task", "T-20260622020202Z", "--repo-id", "main", "--explain", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    reviewed = payload["data"]["groups"]["reviewed_knowledge"]
    assert reviewed[0]["record"]["id"] == record_id
    assert reviewed[0]["record"]["status"] == "reviewed"
    assert reviewed[0]["record"]["lifecycle_relations"]["supersedes"] == [old_record_id]
    assert reviewed[0]["explain"]["source_ref_statuses"][0]["digest_matches"] is True
    assert payload["data"]["metrics"]["group_counts"]["reviewed_knowledge"] == 1
    assert payload["data"]["metrics"]["group_estimated_tokens"]["reviewed_knowledge"] > 0
    assert payload["data"]["bundle"]["query"]["explain"] is True
    assert payload["data"]["bundle"]["completeness"]["knowledge_lifecycle"]["available_statuses"] == {"reviewed": 1, "superseded": 1}
    assert payload["data"]["bundle"]["completeness"]["knowledge_lifecycle"]["returned_statuses"] == {"reviewed": 1}
    assert any(warning["code"] == "context_pack_knowledge_superseded_excluded" for warning in payload["warnings"])


def test_context_pack_compare_artifacts(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _write_context_pack_task(
        tmp_path,
        task_id="T-20260622030303Z",
        slug="pack-compare",
        title="Compare context pack artifacts",
        query="source authority knowledge",
        goal="Use reviewed knowledge source authority.",
    )
    assert main(["knowledge", "candidate", "build", "--source", "docs/contracts/repoctl-context-contract.md", "--repo-id", "main", "--json"]) == 0
    candidate_id = json.loads(capsys.readouterr().out)["data"]["candidate"]["id"]
    assert main(["knowledge", "approve", candidate_id, "--repo-id", "main", "--json"]) == 0
    capsys.readouterr()
    baseline = tmp_path / ".repoctl-state/context-pack/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-pack/candidate.json"

    assert main(["context", "pack", "--task", "T-20260622030303Z", "--repo-id", "main", "--explain", "--output", baseline.as_posix(), "--json"]) == 0
    capsys.readouterr()
    candidate.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-must-read-drop", "0", "--max-reviewed-knowledge-drop", "0", "--json"]) == 0

    pass_payload = json.loads(capsys.readouterr().out)
    assert pass_payload["data"]["count_deltas"]["must_read"]["delta"] == 0
    assert pass_payload["data"]["count_deltas"]["reviewed_knowledge"]["delta"] == 0
    assert pass_payload["data"]["metric_deltas"]["unique_must_read_source_count"]["delta"] == 0
    assert pass_payload["data"]["metric_deltas"]["estimated_tokens"]["delta"] == 0
    assert pass_payload["data"]["warning_deltas"]["missing_codes"] == []
    assert pass_payload["data"]["warning_deltas"]["added_codes"] == []
    assert "context_pack_not_authoritative" in pass_payload["data"]["warning_deltas"]["baseline_codes"]
    assert pass_payload["data"]["missing_must_read_refs"] == []
    assert pass_payload["data"]["missing_reviewed_knowledge_ids"] == []
    assert pass_payload["problems"] == []

    changed_warnings = json.loads(baseline.read_text(encoding="utf-8"))
    original_warning_code = changed_warnings["data"]["warnings"][0]["code"]
    changed_warnings["data"]["warnings"] = []
    digest_basis = {key: value for key, value in changed_warnings["data"].items() if key not in {"pack_digest", "artifact", "repository", "graph"}}
    changed_warnings["data"]["pack_digest"] = digest_data(digest_basis)
    changed_warnings["data"]["artifact"]["pack_digest"] = changed_warnings["data"]["pack_digest"]
    candidate.write_text(json.dumps(changed_warnings, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--json"]) == 0

    warning_info_payload = json.loads(capsys.readouterr().out)
    assert original_warning_code in warning_info_payload["data"]["warning_deltas"]["missing_codes"]
    assert warning_info_payload["problems"] == []

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--require-warning-stability", "--json"]) == 1

    warning_gate_payload = json.loads(capsys.readouterr().out)
    assert warning_gate_payload["data"]["gates"]["require_warning_stability"] is True
    assert any(problem["code"] == "context_pack_warning_missing" and problem["path"] == original_warning_code for problem in warning_gate_payload["problems"])

    swapped_knowledge = json.loads(baseline.read_text(encoding="utf-8"))
    original_record_id = swapped_knowledge["data"]["groups"]["reviewed_knowledge"][0]["record"]["id"]
    swapped_knowledge["data"]["groups"]["reviewed_knowledge"][0]["record"]["id"] = "K-20260622000000Z--other"
    digest_basis = {key: value for key, value in swapped_knowledge["data"].items() if key not in {"pack_digest", "artifact", "repository", "graph"}}
    swapped_knowledge["data"]["pack_digest"] = digest_data(digest_basis)
    swapped_knowledge["data"]["artifact"]["pack_digest"] = swapped_knowledge["data"]["pack_digest"]
    candidate.write_text(json.dumps(swapped_knowledge, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-reviewed-knowledge-drop", "0", "--json"]) == 1

    swapped_knowledge_payload = json.loads(capsys.readouterr().out)
    assert swapped_knowledge_payload["data"]["count_deltas"]["reviewed_knowledge"]["delta"] == 0
    assert original_record_id in swapped_knowledge_payload["data"]["missing_reviewed_knowledge_ids"]
    assert any(problem["code"] == "context_pack_reviewed_knowledge_missing" for problem in swapped_knowledge_payload["problems"])

    swapped = json.loads(baseline.read_text(encoding="utf-8"))
    assert len(swapped["data"]["groups"]["must_read"]) > 1
    missing_ref = swapped["data"]["groups"]["must_read"][0]["source_ref"]
    swapped["data"]["groups"]["must_read"][0] = swapped["data"]["groups"]["must_read"][1]
    digest_basis = {key: value for key, value in swapped["data"].items() if key not in {"pack_digest", "artifact", "repository", "graph"}}
    swapped["data"]["pack_digest"] = digest_data(digest_basis)
    swapped["data"]["artifact"]["pack_digest"] = swapped["data"]["pack_digest"]
    candidate.write_text(json.dumps(swapped, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-must-read-drop", "0", "--json"]) == 1

    swapped_payload = json.loads(capsys.readouterr().out)
    assert swapped_payload["data"]["count_deltas"]["must_read"]["delta"] == 0
    assert any(ref["path"] == missing_ref["path"] and ref["section"] == missing_ref.get("section", "") for ref in swapped_payload["data"]["missing_must_read_refs"])
    assert any(problem["code"] == "context_pack_must_read_ref_missing" for problem in swapped_payload["problems"])

    regressed = json.loads(candidate.read_text(encoding="utf-8"))
    regressed["data"]["groups"]["must_read"] = []
    regressed["data"]["groups"]["reviewed_knowledge"] = []
    digest_basis = {key: value for key, value in regressed["data"].items() if key not in {"pack_digest", "artifact", "repository", "graph"}}
    regressed["data"]["pack_digest"] = digest_data(digest_basis)
    regressed["data"]["artifact"]["pack_digest"] = regressed["data"]["pack_digest"]
    candidate.write_text(json.dumps(regressed, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-must-read-drop", "0", "--max-reviewed-knowledge-drop", "0", "--json"]) == 1

    fail_payload = json.loads(capsys.readouterr().out)
    assert fail_payload["data"]["count_deltas"]["must_read"]["delta"] < 0
    assert fail_payload["data"]["count_deltas"]["reviewed_knowledge"]["delta"] < 0
    assert any(problem["code"] == "context_pack_must_read_regressed" for problem in fail_payload["problems"])
    assert any(problem["code"] == "context_pack_reviewed_knowledge_regressed" for problem in fail_payload["problems"])

    failed_artifact = json.loads(baseline.read_text(encoding="utf-8"))
    failed_artifact["ok"] = False
    failed_artifact["problems"] = [{"severity": "error", "code": "synthetic_failure", "message": "failed"}]
    candidate.write_text(json.dumps(failed_artifact, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--json"]) == 1
    failed_artifact_payload = json.loads(capsys.readouterr().out)
    assert failed_artifact_payload["problems"][0]["code"] == "context_pack_artifact_failed"


def test_context_pack_benchmark_scores_required_must_read_refs(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _write_pack_benchmark_task(tmp_path)
    fixture = Path("tests/fixtures/context-pack-benchmark").resolve()

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-must-read-recall", "1.0", "--json"]) == 0

    payload = json.loads(capsys.readouterr().out)
    assert payload["command"] == "context pack-benchmark"
    assert payload["data"]["case_count"] == 5
    assert payload["data"]["summary"]["mean_must_read_recall"] == 1.0
    found_paths = {
        ref["path"]
        for result in payload["data"]["results"]
        for ref in result["required_must_read_found"]
    }
    assert found_paths == {
        "AGENTS.md",
        "docs/contracts/repoctl-context-contract.md",
        "docs/contracts/repoctl-graph-contract.md",
        "docs/contracts/repoctl-module-boundaries.md",
    }
    assert payload["data"]["gates"]["min_must_read_recall"] == 1.0
    assert payload["warnings"][0]["code"] == "context_pack_benchmark_retrieval_only"


def test_context_pack_benchmark_materialize_makes_shipped_fixture_runnable(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    fixture = Path("tests/fixtures/context-pack-benchmark").resolve()

    assert main(["context", "pack-benchmark-materialize", "--fixture", fixture.as_posix(), "--json"]) == 0
    materialize_payload = json.loads(capsys.readouterr().out)
    assert materialize_payload["command"] == "context pack-benchmark-materialize"
    assert materialize_payload["data"]["totals"]["created"] == 5

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-must-read-recall", "1.0", "--json"]) == 0
    benchmark_payload = json.loads(capsys.readouterr().out)
    assert benchmark_payload["data"]["case_count"] == 5
    assert benchmark_payload["data"]["summary"]["mean_must_read_recall"] == 1.0


def test_context_pack_benchmark_compare_gates_recall_regression(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _write_pack_benchmark_task(tmp_path)
    fixture = Path("tests/fixtures/context-pack-benchmark").resolve()
    baseline = tmp_path / ".repoctl-state/context-pack-benchmark/baseline.json"
    candidate = tmp_path / ".repoctl-state/context-pack-benchmark/candidate.json"

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--output", baseline.as_posix(), "--json"]) == 0
    benchmark_payload = json.loads(capsys.readouterr().out)
    artifact = json.loads(baseline.read_text(encoding="utf-8"))
    assert artifact["command"] == "context pack-benchmark"
    assert artifact["data"]["artifact"] == {
        "path": ".repoctl-state/context-pack-benchmark/baseline.json",
        "benchmark_digest": benchmark_payload["data"]["benchmark_digest"],
    }
    candidate.write_text(baseline.read_text(encoding="utf-8"), encoding="utf-8")

    assert main(["context", "pack-benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-mean-must-read-recall-drop", "0", "--json"]) == 0

    pass_payload = json.loads(capsys.readouterr().out)
    assert pass_payload["command"] == "context pack-benchmark-compare"
    assert pass_payload["data"]["metric_deltas"]["mean_must_read_recall"] == {"baseline": 1.0, "candidate": 1.0, "delta": 0.0}
    assert pass_payload["data"]["case_deltas"][0]["id"] == "CP-001"
    assert pass_payload["data"]["regressions"] == []

    candidate_payload = json.loads(baseline.read_text(encoding="utf-8"))
    candidate_data = candidate_payload["data"]
    candidate_data["summary"]["mean_must_read_recall"] = 0.0
    candidate_data["results"][0]["metrics"]["must_read_recall"] = 0.0
    candidate_data["results"][0]["required_must_read_found"] = []
    candidate_data["results"][0]["missing_required_must_read"] = [{"kind": "document", "path": "docs/contracts/repoctl-context-contract.md", "section": "repoctl Context contract"}]
    candidate_data["benchmark_digest"] = digest_data({key: value for key, value in candidate_data.items() if key not in {"benchmark_digest", "artifact"}})
    candidate.write_text(json.dumps(candidate_payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")

    assert main(["context", "pack-benchmark-compare", "--baseline", baseline.as_posix(), "--candidate", candidate.as_posix(), "--max-mean-must-read-recall-drop", "0.1", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["metric_deltas"]["mean_must_read_recall"] == {"baseline": 1.0, "candidate": 0.0, "delta": -1.0}
    assert payload["problems"][0]["code"] == "context_pack_benchmark_must_read_recall_regressed"


def test_context_pack_benchmark_gate_fails_missing_required_ref(tmp_path: Path, monkeypatch, capsys) -> None:
    _setup_context_workspace(tmp_path, monkeypatch)
    _write_pack_benchmark_task(tmp_path)
    fixture = tmp_path / "pack-fixture"
    fixture.mkdir()
    (fixture / "cases.json").write_text(
        json.dumps(
            {
                "schema": "repoctl.context.task_pack.benchmark.cases",
                "schema_version": 1,
                "cases": [
                    {
                        "id": "CP-MISSING",
                        "task_id": "T-20260624020202Z",
                        "required_must_read_refs": [{"kind": "document", "path": "docs/adr/missing.md"}],
                    }
                ],
            },
            indent=2,
            sort_keys=True,
        )
        + "\n",
        encoding="utf-8",
    )

    assert main(["context", "pack-benchmark", "--fixture", fixture.as_posix(), "--repo-id", "main", "--min-must-read-recall", "1.0", "--json"]) == 1

    payload = json.loads(capsys.readouterr().out)
    assert payload["data"]["summary"]["mean_must_read_recall"] == 0.0
    assert payload["data"]["results"][0]["missing_required_must_read"][0]["path"] == "docs/adr/missing.md"
    assert payload["problems"][0]["code"] == "context_pack_benchmark_must_read_recall_failed"

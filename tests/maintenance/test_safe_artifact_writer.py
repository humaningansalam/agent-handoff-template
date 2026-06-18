from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.agent_harness import safe_artifact_writer
from tools.agent_harness.policy import plan_contract_hash
from tools.runtime.json_io import read_json_object, write_json_atomic_under_root

VALID_STRUCTURED_EVIDENCE = True


def worker_row(worker: str) -> dict[str, object]:
    return {
        "required": True,
        "invoked": True,
        "worker": worker,
        "evidence_kind": "structured-json",
        "status": "passed",
        "blocking_findings": [],
        "evidence": f"{worker} structured evidence",
        "artifact_path": f"ops/agent-harness/{worker}.json",
        "artifact_sha256": f"sha-{worker}",
        "schema_version": 1,
        "structured_evidence_valid": VALID_STRUCTURED_EVIDENCE,
    }


def test_safe_artifact_writer_writes_latest_and_canonical_archive(tmp_path: Path) -> None:
    result = safe_artifact_writer.write_artifact(
        tmp_path,
        kind="plan",
        status="passed",
            summary="plan structured evidence",
        workflow_id="mw-safe-writer",
        candidate_id="O1",
        affected_surfaces=("docs/example.md",),
        acceptance_criteria_ids=("AC1",),
        revision=2,
    )

    latest = tmp_path / "ops" / "agent-harness" / "evidence/plan.json"
    canonical = tmp_path / result["canonical_path"]
    assert json.loads(latest.read_text(encoding="utf-8"))["worker"] == "maintenance-planner"
    assert canonical.read_text(encoding="utf-8") == latest.read_text(encoding="utf-8")
    assert result["latest_path"] == "ops/agent-harness/evidence/plan.json"
    assert result["canonical_path"] == "ops/agent-harness/runs/mw-safe-writer/candidates/O1/r002-plan.json"
    assert len(result["sha256"]) == 64
    metadata = read_json_object(tmp_path / "ops" / "agent-harness" / "latest-plan-metadata.json")
    assert metadata["affected_surfaces"] == ["docs/example.md"]
    assert metadata["acceptance_criteria_ids"] == ["AC1"]
    assert metadata["surface_classes"] == ["low_risk_prose"]
    assert metadata["profile"] == "TINY_DOC"
    assert metadata["route"] == ["maintenance-planner", "maintenance-implementer", "host-verifier"]
    assert metadata["verification_mode"] == "semantic"
    assert "route_changed" in metadata["reapproval_triggers"]
    assert "verification_mode_changed" in metadata["reapproval_triggers"]
    assert metadata["plan_body_sha256"] == result["sha256"]
    assert len(metadata["plan_contract_hash"]) == 64
    assert result["plan_contract_hash"] == metadata["plan_contract_hash"]
    assert result["approval_phrase"] == f"승인: O1 {metadata['plan_contract_hash'][:12]}"
    assert metadata["approval_phrase"] == result["approval_phrase"]
    assert metadata["approval_phrase_hash_prefix_length"] == 12
    assert result["approval_phrase"] != f"승인: O1 {result['sha256'][:8]}"
    assert metadata["plan_sha256"] == result["sha256"]
    index = read_json_object(tmp_path / "ops" / "agent-harness" / "latest-artifact-index.json")
    assert index["artifacts"] == [
        {
            "kind": "plan",
            "latest_path": "ops/agent-harness/evidence/plan.json",
            "canonical_path": "ops/agent-harness/runs/mw-safe-writer/candidates/O1/r002-plan.json",
            "workflow_id": "mw-safe-writer",
            "candidate_id": "O1",
            "revision": 2,
            "sha256": result["sha256"],
        }
    ]


def test_plan_contract_hash_includes_cartography_ambiguity_route(tmp_path: Path) -> None:
    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="cartography",
        status="passed",
        summary="ambiguous instruction doc mapped before planning",
        workflow_id="mw-ambiguous-route",
        active_candidate_id="DOCS-001",
    )

    result = safe_artifact_writer.write_artifact(
        tmp_path,
        kind="plan",
        status="passed",
        summary="plan for ambiguous instruction doc",
        workflow_id="mw-ambiguous-route",
        candidate_id="DOCS-001",
        affected_surfaces=("CLAUDE.md",),
        acceptance_criteria_ids=("AC-001",),
    )

    metadata = read_json_object(tmp_path / "ops" / "agent-harness" / "latest-plan-metadata.json")
    assert metadata["route"][0] == "maintenance-cartographer"
    assert result["plan_contract_hash"] == plan_contract_hash(
        candidate_id="DOCS-001",
        affected_surfaces=("CLAUDE.md",),
        acceptance_criteria_ids=("AC-001",),
        ambiguity=True,
    )


def test_plan_contract_hash_includes_mechanical_verification_route(tmp_path: Path) -> None:
    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="cartography",
        status="passed",
        summary="critical harness doc typo mapped before planning",
        workflow_id="mw-mechanical-route",
        active_candidate_id="DOCS-001",
    )

    semantic = safe_artifact_writer.write_artifact(
        tmp_path,
        kind="plan",
        status="passed",
        summary="semantic plan for critical harness doc",
        workflow_id="mw-mechanical-route",
        candidate_id="DOCS-001",
        affected_surfaces=("docs/MAINTENANCE_HARNESS_CONTRACT.md",),
        acceptance_criteria_ids=("AC-001",),
    )
    mechanical = safe_artifact_writer.write_artifact(
        tmp_path,
        kind="plan",
        status="passed",
        summary="mechanical plan for critical harness doc typo",
        workflow_id="mw-mechanical-route",
        candidate_id="DOCS-001",
        affected_surfaces=("docs/MAINTENANCE_HARNESS_CONTRACT.md",),
        acceptance_criteria_ids=("AC-001",),
        verification_mode="mechanical",
        revision=2,
    )

    metadata = read_json_object(tmp_path / "ops" / "agent-harness" / "latest-plan-metadata.json")
    assert metadata["verification_mode"] == "mechanical"
    assert metadata["profile"] == "CRITICAL_HARNESS"
    assert metadata["route"] == [
        "maintenance-cartographer",
        "maintenance-planner",
        "maintenance-plan-critic",
        "maintenance-implementer",
        "maintenance-evaluator",
    ]
    assert "maintenance-skeptic" not in metadata["route"]
    assert mechanical["plan_contract_hash"] != semantic["plan_contract_hash"]
    assert mechanical["plan_contract_hash"] == plan_contract_hash(
        candidate_id="DOCS-001",
        affected_surfaces=("docs/MAINTENANCE_HARNESS_CONTRACT.md",),
        acceptance_criteria_ids=("AC-001",),
        ambiguity=True,
        verification_mode="mechanical",
    )


def test_mechanical_verification_rejects_broad_or_p1_plan(tmp_path: Path) -> None:
    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="mechanical verification requires"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="too broad for mechanical verification",
            workflow_id="mw-mechanical-broad",
            candidate_id="DOCS-001",
            affected_surfaces=("README.md", "docs/guide.md"),
            acceptance_criteria_ids=("AC-001",),
            verification_mode="mechanical",
        )

    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="mechanical verification is limited"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="settings typo should not skip skeptic",
            workflow_id="mw-mechanical-settings",
            candidate_id="SETTINGS-001",
            affected_surfaces=(".claude/settings.json",),
            acceptance_criteria_ids=("AC-001",),
            verification_mode="mechanical",
        )


def test_safe_artifact_writer_rejects_forbidden_repo_surface(tmp_path: Path) -> None:
    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="forbidden affected surfaces"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="repo product files are out of maintenance scope",
            workflow_id="mw-forbidden-repo",
            candidate_id="REPO-001",
            affected_surfaces=("repo/src/app.py",),
            acceptance_criteria_ids=("AC-001",),
        )

    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="mechanical verification requires"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="too severe for mechanical verification",
            workflow_id="mw-mechanical-severe",
            candidate_id="DOCS-002",
            affected_surfaces=("README.md",),
            acceptance_criteria_ids=("AC-001",),
            failure_mode_severity="P1",
            verification_mode="mechanical",
        )


def test_safe_artifact_writer_rejects_candidate_artifact_without_candidate(tmp_path: Path) -> None:
    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="candidate-id is required"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="plan structured evidence",
            workflow_id="mw-safe-writer",
        )


def test_safe_artifact_writer_cli_root_must_match_claude_project_dir(tmp_path: Path, monkeypatch) -> None:
    outside = tmp_path / "outside"
    outside.mkdir()
    monkeypatch.setenv("CLAUDE_PROJECT_DIR", str(tmp_path))

    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="CLAUDE_PROJECT_DIR"):
        safe_artifact_writer.run(
            safe_artifact_writer.parse_args(
                [
                    "write",
                    "--root",
                    str(outside),
                    "--kind",
                    "cartography",
                    "--status",
                    "passed",
                    "--summary",
                    "outside root should be rejected",
                    "--workflow-id",
                    "mw-root-boundary",
                ]
            )
        )


def test_safe_artifact_writer_cli_outputs_json(tmp_path: Path, capsys) -> None:
    safe_artifact_writer.main(
        [
            "write",
            "--root",
            str(tmp_path),
            "--kind",
            "cartography",
            "--status",
            "passed",
            "--summary",
            "cartography structured evidence",
            "--workflow-id",
            "mw-cli-writer",
        ]
    )

    output = json.loads(capsys.readouterr().out)
    assert output["kind"] == "cartography"
    assert output["latest_path"] == "ops/agent-harness/evidence/cartography.json"


def test_safe_artifact_writer_records_optional_finding_matrix(tmp_path: Path) -> None:
    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="execution-review",
        status="failed",
        summary="verification found a policy mismatch",
        workflow_id="mw-finding-matrix",
        candidate_id="DOCS-001",
        verification_passed=False,
        finding_ids=("F-001",),
        finding_surfaces=("docs/MAINTENANCE_HARNESS_CONTRACT.md",),
        finding_expected=("contract docs require critical route",),
        finding_observed=("route was too light",),
        finding_verdicts=("fail",),
        finding_severities=("P1",),
        retry_target_value="retry-plan",
        checked_surfaces=("docs/MAINTENANCE_HARNESS_CONTRACT.md",),
        evidence_refs=("ops/agent-harness/evidence/execution.json",),
    )

    evidence = json.loads((tmp_path / "ops" / "agent-harness" / "evidence/execution-review.json").read_text(encoding="utf-8"))
    assert evidence["findings"] == [
        {
            "id": "F-001",
            "surface": "docs/MAINTENANCE_HARNESS_CONTRACT.md",
            "expected": "contract docs require critical route",
            "observed": "route was too light",
            "verdict": "fail",
            "severity": "P1",
        }
    ]
    assert evidence["retry_target"] == "retry-plan"
    assert evidence["checked_surfaces"] == ["docs/MAINTENANCE_HARNESS_CONTRACT.md"]
    assert evidence["evidence_refs"] == ["ops/agent-harness/evidence/execution.json"]


def test_safe_artifact_writer_accepts_typed_retry_targets(tmp_path: Path) -> None:
    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="execution-review",
        status="failed",
        summary="approval metadata retry evidence",
        workflow_id="mw-typed-retry",
        candidate_id="DOCS-001",
        verification_passed=False,
        retry_target_value="retry-approval-metadata",
    )

    evidence = json.loads((tmp_path / "ops" / "agent-harness" / "evidence/execution-review.json").read_text(encoding="utf-8"))
    assert evidence["retry_target"] == "retry-approval-metadata"


def test_safe_artifact_writer_records_structured_candidate_queue(tmp_path: Path) -> None:
    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="cartography",
        status="passed",
            summary="cartography structured evidence",
        workflow_id="mw-1",
        active_candidate_id="DOCS-001",
        queued_candidate_ids=("DOCS-002,DOCS-003",),
        queue_policy="human-decision",
    )

    state = read_json_object(tmp_path / "ops" / "agent-harness" / "latest-candidate-state.json")
    assert state["active_candidate_id"] == "DOCS-001"
    assert state["queued_candidate_ids"] == ["DOCS-002", "DOCS-003"]
    assert state["queue_policy"] == "human-decision"


def test_safe_artifact_writer_does_not_record_none_as_queued_candidate(tmp_path: Path) -> None:
    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="cartography",
        status="passed",
            summary="cartography structured evidence",
        workflow_id="mw-1",
        active_candidate_id="DOCS-001",
        queued_candidate_ids=("none", "없음"),
        queue_policy="auto-continuation",
    )

    state = read_json_object(tmp_path / "ops" / "agent-harness" / "latest-candidate-state.json")
    assert state["queued_candidate_ids"] == []
    assert state["queue_policy"] == ""
    assert state["terminal_candidate"] is True


def test_safe_artifact_writer_rejects_candidate_id_that_does_not_match_active_state(tmp_path: Path) -> None:
    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "latest-candidate-state.json",
        {
            "schema_version": 1,
            "workflow_id": "mw-candidate-lineage",
            "active_candidate_id": "DOCS-POLISH-001",
            "queued_candidate_ids": [],
            "queue_policy": "",
            "terminal_candidate": True,
        },
        tmp_path,
    )

    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="candidate-id must match active candidate"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="plan structured evidence",
            workflow_id="mw-candidate-lineage",
            candidate_id="DOC-POLISH-001",
            affected_surfaces=("docs/PRD.md",),
            acceptance_criteria_ids=("AC-001",),
        )


def test_safe_artifact_writer_rejects_plan_without_structured_metadata(tmp_path: Path) -> None:
    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="affected-surface"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="plan structured evidence",
            workflow_id="mw-1",
            candidate_id="DOCS-001",
            acceptance_criteria_ids=("AC1",),
        )


def test_safe_artifact_writer_requires_cartography_before_critical_harness_plan(tmp_path: Path) -> None:
    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="requires cartography evidence before writing plan"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="critical harness plan structured evidence",
            workflow_id="mw-critical-plan",
            candidate_id="DOCS-001",
            affected_surfaces=("docs/MAINTENANCE_HARNESS_CONTRACT.md",),
            acceptance_criteria_ids=("AC-001",),
        )


def test_safe_artifact_writer_allows_critical_harness_plan_after_cartography(tmp_path: Path) -> None:
    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="cartography",
        status="passed",
        summary="cartography structured evidence",
        workflow_id="mw-critical-plan-ok",
        active_candidate_id="DOCS-001",
    )

    result = safe_artifact_writer.write_artifact(
        tmp_path,
        kind="plan",
        status="passed",
        summary="critical harness plan structured evidence",
        workflow_id="mw-critical-plan-ok",
        candidate_id="DOCS-001",
        affected_surfaces=("docs/MAINTENANCE_HARNESS_CONTRACT.md",),
        acceptance_criteria_ids=("AC-001",),
    )

    metadata = read_json_object(tmp_path / "ops" / "agent-harness" / "latest-plan-metadata.json")
    assert result["kind"] == "plan"
    assert metadata["profile"] == "CRITICAL_HARNESS"
    assert metadata["route"][0] == "maintenance-cartographer"


def test_safe_artifact_writer_requires_shards_for_broad_critical_plan(tmp_path: Path) -> None:
    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="cartography",
        status="passed",
        summary="cartography structured evidence",
        workflow_id="mw-critical-broad",
        active_candidate_id="CAND-001",
    )

    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="require cartography shard queue"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="broad critical plan structured evidence",
            workflow_id="mw-critical-broad",
            candidate_id="CAND-001",
            affected_surfaces=(
                "tools/agent_harness/safe_artifact_writer.py",
                "tools/hooks/maintenance/prompt_approval.py",
                "docs/MAINTENANCE_HARNESS_CONTRACT.md",
                "tests/maintenance/test_safe_artifact_writer.py",
            ),
            acceptance_criteria_ids=("AC-001",),
        )


def test_safe_artifact_writer_allows_broad_critical_plan_with_shard_queue(tmp_path: Path) -> None:
    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="cartography",
        status="passed",
        summary="cartography structured evidence",
        workflow_id="mw-critical-sharded",
        active_candidate_id="CAND-001",
        queued_candidate_ids=("CAND-002",),
        queue_policy="human-decision",
    )

    result = safe_artifact_writer.write_artifact(
        tmp_path,
        kind="plan",
        status="passed",
        summary="broad critical plan structured evidence",
        workflow_id="mw-critical-sharded",
        candidate_id="CAND-001",
        affected_surfaces=(
            "tools/agent_harness/safe_artifact_writer.py",
            "tools/hooks/maintenance/prompt_approval.py",
            "docs/MAINTENANCE_HARNESS_CONTRACT.md",
            "tests/maintenance/test_safe_artifact_writer.py",
        ),
        acceptance_criteria_ids=("AC-001",),
    )

    metadata = read_json_object(tmp_path / "ops" / "agent-harness" / "latest-plan-metadata.json")
    assert result["kind"] == "plan"
    assert metadata["profile"] == "CRITICAL_HARNESS"


def test_safe_artifact_writer_rejects_workflow_id_outside_active_session(tmp_path: Path) -> None:
    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "active-sessions" / "session-1.json",
        {"schema_version": 1, "session_id": "session-1", "workflow_id": "mw-active"},
        tmp_path,
    )

    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="active maintenance session"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="cartography",
            status="passed",
            summary="cartography structured evidence",
            workflow_id="mw-wrong",
        )


def test_safe_artifact_writer_rejects_workflow_id_outside_current_state(tmp_path: Path) -> None:
    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "current-run-state.json",
        {"schema_version": 1, "workflow_id": "mw-current", "phase": "cartographed"},
        tmp_path,
    )

    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="current maintenance state"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="cartography",
            status="passed",
            summary="cartography structured evidence",
            workflow_id="mw-wrong",
        )


def test_safe_artifact_writer_blocks_replanning_after_critic_without_structured_review(tmp_path: Path) -> None:
    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "current-run-state.json",
        {
            "schema_version": 1,
            "workflow_id": "mw-review-route",
            "phase": "plan_reviewed",
            "worker_status": {
                "maintenance-plan-critic": worker_row("maintenance-plan-critic"),
                "maintenance-planner": worker_row("maintenance-planner"),
            },
        },
        tmp_path,
    )

    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="structured approval-ready metadata"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="plan structured evidence",
            workflow_id="mw-review-route",
            candidate_id="DOCS-001",
            affected_surfaces=("docs/OPERATIONS_CONTRACT.md",),
            acceptance_criteria_ids=("AC-001",),
        )


def test_safe_artifact_writer_blocks_replanning_after_approval_ready_review(tmp_path: Path) -> None:
    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "current-run-state.json",
        {
            "schema_version": 1,
            "workflow_id": "mw-approval-ready",
            "phase": "awaiting_human_approval",
            "worker_status": {
                "maintenance-plan-critic": worker_row("maintenance-plan-critic"),
                "maintenance-planner": worker_row("maintenance-planner"),
            },
        },
        tmp_path,
    )
    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "latest-plan-review-metadata.json",
        {"schema_version": 1, "workflow_id": "mw-approval-ready", "candidate_id": "DOCS-001", "approval_ready": True},
        tmp_path,
    )

    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="before human approval"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="cartography",
            status="passed",
            summary="cartography structured evidence",
            workflow_id="mw-approval-ready",
            active_candidate_id="DOCS-002",
        )


def test_safe_artifact_writer_allows_replanning_after_nonready_review(tmp_path: Path) -> None:
    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "current-run-state.json",
        {
            "schema_version": 1,
            "workflow_id": "mw-retry-plan",
            "phase": "plan_reviewed",
            "retry": {"target": "retry-plan", "blockers": ["plan review is not ready"]},
            "worker_status": {
                "maintenance-plan-critic": worker_row("maintenance-plan-critic"),
                "maintenance-planner": worker_row("maintenance-planner"),
            },
        },
        tmp_path,
    )
    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "latest-plan-review-metadata.json",
        {"schema_version": 1, "workflow_id": "mw-retry-plan", "candidate_id": "DOCS-001", "approval_ready": False},
        tmp_path,
    )


    result = safe_artifact_writer.write_artifact(
        tmp_path,
        kind="plan",
        status="passed",
            summary="plan structured evidence",
        workflow_id="mw-retry-plan",
        candidate_id="DOCS-001",
        affected_surfaces=("docs/OPERATIONS_CONTRACT.md",),
        acceptance_criteria_ids=("AC-001",),
    )

    assert result["kind"] == "plan"


def test_safe_artifact_writer_blocks_nonready_replanning_without_planner_retry(tmp_path: Path) -> None:
    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "current-run-state.json",
        {
            "schema_version": 1,
            "workflow_id": "mw-missing-planner-retry",
            "phase": "plan_reviewed",
            "worker_status": {
                "maintenance-plan-critic": worker_row("maintenance-plan-critic")
            },
        },
        tmp_path,
    )
    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "latest-plan-review-metadata.json",
        {"schema_version": 1, "workflow_id": "mw-missing-planner-retry", "candidate_id": "DOCS-001", "approval_ready": False},
        tmp_path,
    )


    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="maintenance-planner before writing plan"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="plan structured evidence",
            workflow_id="mw-missing-planner-retry",
            candidate_id="DOCS-001",
            affected_surfaces=("docs/OPERATIONS_CONTRACT.md",),
            acceptance_criteria_ids=("AC-001",),
        )

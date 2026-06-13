from __future__ import annotations

import json
from pathlib import Path

import pytest

from tools.agent_harness import safe_artifact_writer
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


def test_safe_artifact_writer_rejects_candidate_artifact_without_candidate(tmp_path: Path) -> None:
    with pytest.raises(safe_artifact_writer.SafeArtifactWriterError, match="candidate-id is required"):
        safe_artifact_writer.write_artifact(
            tmp_path,
            kind="plan",
            status="passed",
            summary="plan structured evidence",
            workflow_id="mw-safe-writer",
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

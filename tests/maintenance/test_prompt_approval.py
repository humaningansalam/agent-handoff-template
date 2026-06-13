from __future__ import annotations

import hashlib
from pathlib import Path

from tools.agent_harness import paths as harness_paths
from tools.agent_harness.harness import MaintenanceHarness, Phase
from tools.hooks.maintenance.prompt_approval import approval_context_for_prompt
from tools.hooks.maintenance.scope import write_marker
from tools.runtime.json_io import read_json_object, write_json_atomic_under_root


def _write(path: Path, body: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(body, encoding="utf-8")


def _awaiting_approval_state(root: Path, *, workflow_id: str, candidate_id: str, plan_body: str) -> None:
    plan_path = root / harness_paths.ARTIFACT_ROOT / "evidence/plan.json"
    _write(plan_path, plan_body)
    plan_sha = hashlib.sha256(plan_body.encode("utf-8")).hexdigest()
    state = {
        "schema_version": 1,
        "workflow_id": workflow_id,
        "phase": Phase.AWAITING_HUMAN_APPROVAL.value,
        "active_candidate_id": candidate_id,
        "queued_candidate_ids": [],
        "terminal_candidate": True,
        "queue_policy": "human-decision",
        "approval_gate": {
            "status": "awaiting-human-approval",
            "requires_human_approval": True,
            "approval_evidence_recorded": False,
            "freeze": {},
        },
        "artifacts": [
            {
                "path": "ops/agent-harness/evidence/plan.json",
                "canonical_path": f"ops/agent-harness/runs/{workflow_id}/candidates/{candidate_id}/r001-plan.json",
                "workflow_id": workflow_id,
                "candidate_id": candidate_id,
                "phase": Phase.AWAITING_HUMAN_APPROVAL.value,
                "revision": 1,
                "content_sha256": plan_sha,
            }
        ],
        "pass_eligibility": {"eligible": False, "blocked_by": [], "calculated": {"eligible": False}},
        "worker_status": {},
    }
    write_json_atomic_under_root(root / harness_paths.STATE_JSON, state, root)


def _plan_metadata(
    root: Path,
    *,
    workflow_id: str,
    candidate_id: str,
    surfaces: list[str],
    criteria: list[str],
    plan_body: str,
) -> None:
    write_json_atomic_under_root(
        root / harness_paths.PLAN_METADATA_JSON,
        {
            "schema_version": 1,
            "workflow_id": workflow_id,
            "candidate_id": candidate_id,
            "affected_surfaces": surfaces,
            "acceptance_criteria_ids": criteria,
            "plan_sha256": hashlib.sha256(plan_body.encode("utf-8")).hexdigest(),
        },
        root,
    )


def test_explicit_approval_turn_freezes_reviewed_plan_from_structured_metadata(tmp_path: Path) -> None:
    session_id = "approval-session"
    workflow_id = f"mw-{session_id}"
    candidate_id = "CAND-doc-typo"
    plan_body = "# Plan\nThis prose is not parsed for approval metadata.\n"
    write_marker(tmp_path, {"session_id": session_id, "workflow_id": workflow_id}, prompt="/maintenance-workflow doc typo")
    _awaiting_approval_state(tmp_path, workflow_id=workflow_id, candidate_id=candidate_id, plan_body=plan_body)
    _plan_metadata(
        tmp_path,
        workflow_id=workflow_id,
        candidate_id=candidate_id,
        surfaces=["docs/example.md"],
        criteria=["AC1"],
        plan_body=plan_body,
    )

    context = approval_context_for_prompt(tmp_path, {"session_id": session_id, "prompt": "I approve this plan. Continue.", "maintenance_approval": True})

    state = read_json_object(tmp_path / harness_paths.STATE_JSON)
    MaintenanceHarness.validate_state_checkpoint(state)
    assert "Explicit approval frozen" in context
    assert state["phase"] == "approved_frozen"
    freeze = state["approval_gate"]["freeze"]
    assert freeze["candidate_id"] == candidate_id
    assert freeze["affected_surfaces"] == ["docs/example.md"]
    assert freeze["acceptance_criteria_ids"] == ["AC1"]
    assert len(freeze["plan_sha256"]) == 64


def test_approval_turn_without_plan_metadata_fails_closed(tmp_path: Path) -> None:
    session_id = "approval-missing-metadata"
    workflow_id = f"mw-{session_id}"
    candidate_id = "CAND-doc-typo"
    plan_body = "# Plan\nNo structured approval metadata exists.\n"
    write_marker(tmp_path, {"session_id": session_id, "workflow_id": workflow_id}, prompt="/maintenance-workflow doc typo")
    _awaiting_approval_state(tmp_path, workflow_id=workflow_id, candidate_id=candidate_id, plan_body=plan_body)

    context = approval_context_for_prompt(tmp_path, {"session_id": session_id, "prompt": "approved", "maintenance_approval": True})

    state = read_json_object(tmp_path / harness_paths.STATE_JSON)
    assert "latest-plan-metadata.json is missing" in context
    assert state["phase"] == "awaiting_human_approval"


def test_approval_turn_rejects_metadata_for_wrong_workflow(tmp_path: Path) -> None:
    session_id = "approval-wrong-workflow"
    workflow_id = f"mw-{session_id}"
    candidate_id = "CAND-doc-typo"
    plan_body = "# Plan\nMetadata belongs to another run.\n"
    write_marker(tmp_path, {"session_id": session_id, "workflow_id": workflow_id}, prompt="/maintenance-workflow doc typo")
    _awaiting_approval_state(tmp_path, workflow_id=workflow_id, candidate_id=candidate_id, plan_body=plan_body)
    _plan_metadata(
        tmp_path,
        workflow_id="mw-other",
        candidate_id=candidate_id,
        surfaces=["docs/example.md"],
        criteria=["AC1"],
        plan_body=plan_body,
    )

    context = approval_context_for_prompt(tmp_path, {"session_id": session_id, "prompt": "approved", "maintenance_approval": True})

    state = read_json_object(tmp_path / harness_paths.STATE_JSON)
    assert "plan metadata workflow does not match active session" in context
    assert state["phase"] == "awaiting_human_approval"


def test_approval_turn_freezes_even_if_marker_hook_runs_after_prompt_context(tmp_path: Path) -> None:
    session_id = "approval-markerless"
    workflow_id = f"mw-{session_id}"
    candidate_id = "CAND-doc-typo"
    plan_body = "# Plan\nPrompt context may run before marker creation.\n"
    _awaiting_approval_state(tmp_path, workflow_id=workflow_id, candidate_id=candidate_id, plan_body=plan_body)
    _plan_metadata(
        tmp_path,
        workflow_id=workflow_id,
        candidate_id=candidate_id,
        surfaces=["docs/example.md"],
        criteria=["AC1"],
        plan_body=plan_body,
    )

    context = approval_context_for_prompt(tmp_path, {"session_id": session_id, "prompt": "승인", "maintenance_approval": True})

    state = read_json_object(tmp_path / harness_paths.STATE_JSON)
    assert "Explicit approval frozen" in context
    assert state["phase"] == "approved_frozen"
    marker = read_json_object(tmp_path / "ops" / "agent-harness" / "active-sessions" / f"{session_id}.json")
    assert marker["workflow_id"] == workflow_id

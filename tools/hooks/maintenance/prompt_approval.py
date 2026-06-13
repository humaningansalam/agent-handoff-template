from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.agent_harness import paths as harness_paths
from tools.agent_harness.harness import ApprovalRequired, ArtifactRecord, MaintenanceHarness, Phase
from tools.hooks.maintenance.scope import active_marker_for_session, write_marker_record
from tools.runtime.json_io import read_json_object, write_json_atomic_under_root


STATE_PATH = harness_paths.STATE_JSON
PLAN_METADATA_PATH = harness_paths.PLAN_METADATA_JSON
APPROVAL_PROMPTS = {"승인", "approve", "approved", "yes", "go"}


def is_explicit_approval_prompt(prompt: str) -> bool:
    return prompt.strip().casefold() in APPROVAL_PROMPTS

def approval_context_for_prompt(root: Path, payload: dict[str, Any]) -> str:
    """Freeze reviewed maintenance approval from structured plan metadata."""
    prompt = str(payload.get("prompt") or "").strip()
    if payload.get("maintenance_approval") is not True and not is_explicit_approval_prompt(prompt):
        return ""
    state = read_json_object(root / STATE_PATH, missing_ok=True)
    if str(state.get("phase") or "") != Phase.AWAITING_HUMAN_APPROVAL.value:
        return ""
    session_id = str(payload.get("session_id") or "default")
    marker = active_marker_for_session(root, session_id)
    if not marker:
        marker = write_marker_record(
            root,
            session_id=session_id,
            workflow_id=str(state.get("workflow_id") or f"mw-{session_id}"),
            prompt=prompt,
        )

    metadata = read_json_object(root / PLAN_METADATA_PATH, missing_ok=True)
    if not metadata:
        return "[MAINTENANCE_APPROVAL] Approval was not frozen: latest-plan-metadata.json is missing."
    try:
        checkpoint = _freeze_approval(root, state, marker, metadata, prompt)
    except ApprovalRequired as exc:
        return f"[MAINTENANCE_APPROVAL] Approval was not frozen: {exc}"
    except Exception as exc:  # pragma: no cover - defensive hook path
        return f"[MAINTENANCE_APPROVAL] Approval freeze failed closed: {exc}"

    freeze = checkpoint.get("approval_gate", {}).get("freeze", {}) if isinstance(checkpoint, dict) else {}
    surfaces = freeze.get("affected_surfaces") if isinstance(freeze.get("affected_surfaces"), list) else []
    criteria = freeze.get("acceptance_criteria_ids") if isinstance(freeze.get("acceptance_criteria_ids"), list) else []
    return (
        "[MAINTENANCE_APPROVAL] Explicit approval frozen. "
        f"phase=approved_frozen candidate={freeze.get('candidate_id', '')} "
        f"surfaces={','.join(str(surface) for surface in surfaces)} "
        f"criteria={','.join(str(criterion) for criterion in criteria)}. "
        "Continue via maintenance-implementer only within approved surfaces."
    )


def _freeze_approval(
    root: Path,
    state: dict[str, Any],
    marker: dict[str, Any],
    metadata: dict[str, Any],
    prompt: str,
) -> dict[str, Any]:
    workflow_id = str(state.get("workflow_id") or marker.get("workflow_id") or "").strip()
    active_candidate = str(state.get("active_candidate_id") or metadata.get("candidate_id") or "").strip()
    if not workflow_id:
        raise ApprovalRequired("active workflow id is missing")
    if str(metadata.get("workflow_id") or "").strip() != workflow_id:
        raise ApprovalRequired("plan metadata workflow does not match active session")
    if str(metadata.get("candidate_id") or "").strip() != active_candidate:
        raise ApprovalRequired("plan metadata candidate does not match active candidate")

    affected_surfaces = _metadata_list(metadata, "affected_surfaces")
    acceptance_criteria_ids = _metadata_list(metadata, "acceptance_criteria_ids")
    if not affected_surfaces or not acceptance_criteria_ids:
        raise ApprovalRequired("plan metadata must include affected surfaces and acceptance criteria ids")

    harness = MaintenanceHarness(workflow_id=workflow_id, phase=Phase.AWAITING_HUMAN_APPROVAL)
    harness.active_candidate_id = active_candidate
    queued = state.get("queued_candidate_ids")
    if isinstance(queued, list):
        harness.queued_candidate_ids = tuple(str(item) for item in queued if str(item).strip())
    harness.terminal_candidate = bool(state.get("terminal_candidate", not harness.queued_candidate_ids))
    harness.queue_policy = str(state.get("queue_policy") or "human-decision")
    for row in state.get("artifacts", []):
        if not isinstance(row, dict):
            continue
        path = str(row.get("path") or "")
        if path not in MaintenanceHarness.TRACE_ARTIFACT_PATHS:
            continue
        harness.artifacts[path] = ArtifactRecord(
            path=path,
            workflow_id=str(row.get("workflow_id") or workflow_id),
            candidate_id=str(row.get("candidate_id") or ""),
            phase=_phase(str(row.get("phase") or Phase.AWAITING_HUMAN_APPROVAL.value)),
            revision=int(row.get("revision") or 1),
            content_sha256=str(row.get("content_sha256") or ""),
        )
    harness.record_human_approval(
        prompt[:500],
        approved_plan_sha256=str(metadata.get("plan_sha256") or ""),
        approved_affected_surfaces=affected_surfaces,
        approved_acceptance_criteria_ids=acceptance_criteria_ids,
    )
    checkpoint = harness.state_checkpoint()
    write_json_atomic_under_root(root / STATE_PATH, checkpoint, root)
    return checkpoint


def _metadata_list(metadata: dict[str, Any], key: str) -> list[str]:
    value = metadata.get(key)
    if not isinstance(value, list):
        return list()
    seen: set[str] = set()
    result: list[str] = []
    for item in value:
        normalized = str(item).strip()
        if normalized and normalized not in seen:
            seen.add(normalized)
            result.append(normalized)
    return result


def _phase(value: str) -> Phase:
    try:
        return Phase(value)
    except ValueError:
        return Phase.INTAKE

from __future__ import annotations

from typing import Any


def worker_ready(row: dict[str, Any]) -> bool:
    blocking_findings = row.get("blocking_findings")
    if not isinstance(blocking_findings, list):
        blocking_findings = []
    return bool(
        row.get("required") is True
        and row.get("invoked") is True
        and str(row.get("worker") or "").strip()
        and str(row.get("evidence_kind") or "").strip()
        and row.get("status") == "passed"
        and not [str(item).strip() for item in blocking_findings if str(item).strip()]
        and ((row.get("artifact_path") and row.get("artifact_sha256")) or row.get("schema_version"))
        and str(row.get("evidence") or row.get("summary") or "").strip()
        and row.get("structured_evidence_valid") is True
    )


def calculate_pass_eligibility(
    *,
    required_artifact_paths: tuple[str, ...],
    available_artifact_paths: set[str],
    mandatory_workers: tuple[str, ...],
    worker_status: dict[str, dict[str, Any]],
    approval_hash_ok: bool,
    tests_passed: bool,
    scope_ok: bool,
    pass_candidate: bool = True,
    state_confirmed: bool = True,
    state_blockers: list[str] | tuple[str, ...] = (),
    workflow_path: str = "",
) -> dict[str, Any]:
    missing_artifacts = sorted(set(required_artifact_paths) - available_artifact_paths)
    missing_workers = [worker for worker in mandatory_workers if not worker_ready(worker_status.get(worker, {}))]
    blockers: list[str] = []
    if missing_artifacts:
        blockers.append("missing_artifacts")
    if missing_workers:
        blockers.append("worker_status_incomplete")
    if not approval_hash_ok:
        blockers.append("approval_hash_mismatch")
    if not tests_passed:
        blockers.append("tests_not_passed")
    if not scope_ok:
        blockers.append("scope_violation")
    if not pass_candidate:
        blockers.append("evaluation_not_pass_candidate")
    if state_blockers:
        blockers.append("state_blocked_by")
    if not state_confirmed:
        blockers.append("state_not_confirmed")
    result: dict[str, Any] = {
        "eligible": not blockers,
        "missing_artifacts": missing_artifacts,
        "missing_workers": missing_workers,
        "worker_status_complete": not missing_workers,
        "approval_hash_ok": approval_hash_ok,
        "tests_passed": tests_passed,
        "scope_ok": scope_ok,
        "blocked_by": blockers,
    }
    if workflow_path:
        result["workflow_path"] = workflow_path
    return result


def read_calculated_pass_eligibility(state: dict[str, Any]) -> dict[str, Any]:
    value = state.get("pass_eligibility") if isinstance(state, dict) else {}
    if isinstance(value, dict) and isinstance(value.get("calculated"), dict):
        return dict(value["calculated"])
    return {"eligible": False, "blocked_by": ["missing pass eligibility"]}

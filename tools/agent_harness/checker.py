from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path
from typing import Any

from tools.agent_harness import paths as harness_paths
from tools.agent_harness.harness import MaintenanceHarness, Phase
from tools.agent_harness.pass_gate import calculate_pass_eligibility, worker_ready
from tools.agent_harness.policy import policy_for_surfaces
from tools.agent_harness.retry_policy import RETRY_AGENT_PRIMARY, RETRY_ARTIFACT_PRIMARY
from tools.hooks.maintenance.scope import relative_to_root
from tools.runtime.json_io import read_json_object, write_json_atomic_under_root, write_text_atomic_under_root

TRACE_ROOT = harness_paths.ARTIFACT_ROOT
EVENTS_JSONL = harness_paths.EVENTS_JSONL
STATE_JSON = harness_paths.STATE_JSON

PATH_TO_WORKER = {
    str(harness_paths.ARTIFACT_ROOT / harness_paths.LATEST_ARTIFACTS["cartography"]): "maintenance-cartographer",
    str(harness_paths.ARTIFACT_ROOT / harness_paths.LATEST_ARTIFACTS["plan"]): "maintenance-planner",
    str(harness_paths.ARTIFACT_ROOT / harness_paths.LATEST_ARTIFACTS["plan-review"]): "maintenance-plan-critic",
    str(harness_paths.ARTIFACT_ROOT / harness_paths.LATEST_ARTIFACTS["execution"]): "maintenance-implementer",
    str(harness_paths.ARTIFACT_ROOT / harness_paths.LATEST_ARTIFACTS["execution-review"]): "maintenance-evaluator",
    str(harness_paths.ARTIFACT_ROOT / harness_paths.LATEST_ARTIFACTS["skeptic-review"]): "maintenance-skeptic",
}

PHASE_ORDER = {
    Phase.INTAKE: 0,
    Phase.CARTOGRAPHED: 1,
    Phase.DRAFT_PLANNED: 2,
    Phase.PLAN_REVIEWED: 3,
    Phase.AWAITING_HUMAN_APPROVAL: 4,
    Phase.APPROVED_FROZEN: 5,
    Phase.IMPLEMENTED: 6,
    Phase.EVALUATED: 7,
    Phase.SKEPTIC_REVIEWED: 8,
    Phase.DECIDED: 9,
}


def reconcile_current_state(root: Path) -> dict[str, Any]:
    state_path = root / STATE_JSON
    existing = read_json_object(state_path, missing_ok=True)
    workflow_id = str(existing.get("workflow_id") or "")
    if not workflow_id:
        return existing
    events = _read_recent_events(root / EVENTS_JSONL)
    latest = events[-1] if events else dict(existing.get("latest_event") or {})
    reconciled = _reconcile_state_with_evidence(root, existing, latest, workflow_id=workflow_id)
    if reconciled != existing:
        write_json_atomic_under_root(state_path, reconciled, root)
    return reconciled


def ensure_state(root: Path, marker: dict[str, Any], latest: dict[str, Any]) -> None:
    workflow_id = str(marker.get("workflow_id") or marker.get("session_id") or "maintenance-workflow")
    state_path = root / STATE_JSON
    existing = read_json_object(state_path, missing_ok=True)
    if existing.get("workflow_id") == workflow_id:
        try:
            MaintenanceHarness.validate_state_checkpoint(existing)
        except ValueError:
            pass
        else:
            reconciled = _reconcile_state_with_evidence(root, existing, latest, workflow_id=workflow_id)
            if reconciled != existing:
                write_json_atomic_under_root(state_path, reconciled, root)
            return
    base = _canonical_state_checkpoint(marker, latest, workflow_id=workflow_id)
    reconciled = _reconcile_state_with_evidence(root, base, latest, workflow_id=workflow_id)
    write_json_atomic_under_root(state_path, reconciled, root)

def _reconcile_state_with_evidence(root: Path, checkpoint: dict[str, Any], latest: dict[str, Any], *, workflow_id: str) -> dict[str, Any]:
    state = dict(checkpoint)
    original_phase = str(state.get("phase") or "")
    original_active_candidate = str(state.get("active_candidate_id") or "").strip()
    original_changed_files = tuple(str(path) for path in state.get("changed_files", []) if str(path).strip()) if isinstance(state.get("changed_files"), list) else ()
    artifacts = [dict(row) for row in state.get("artifacts", []) if isinstance(row, dict)]
    indexed = {str(row.get("path") or ""): row for row in artifacts}
    original_indexed = {path: dict(row) for path, row in indexed.items()}
    original_artifact_paths = set(indexed)
    original_worker_status = state.get("worker_status") if isinstance(state.get("worker_status"), dict) else {}
    candidate_state = _candidate_state(root)
    active_candidate = str(state.get("active_candidate_id") or "").strip() or str(candidate_state.get("active_candidate_id") or "").strip()
    queued_from_state = tuple(str(candidate) for candidate in candidate_state.get("queued_candidate_ids", []) if str(candidate).strip()) if isinstance(candidate_state.get("queued_candidate_ids"), list) else ()
    queue_policy_from_state = str(candidate_state.get("queue_policy") or "human-decision")
    phase = _checkpoint_phase(str(state.get("phase") or ""))
    evidence_paths = _indexed_evidence_paths(root, workflow_id)

    phase = _max_phase(phase, _phase_from_evidence(evidence_paths, latest))

    state["phase"] = phase.value
    if active_candidate:
        state["active_candidate_id"] = active_candidate
    if candidate_state:
        state["queued_candidate_ids"] = list(queued_from_state)
        state["terminal_candidate"] = not bool(queued_from_state)
        state["queue_policy"] = queue_policy_from_state if queued_from_state else ""
    approval_gate = state.get("approval_gate") if isinstance(state.get("approval_gate"), dict) else {}
    if phase == Phase.AWAITING_HUMAN_APPROVAL:
        state["approval_gate"] = {
            **approval_gate,
            "status": "awaiting-human-approval",
            "requires_human_approval": True,
        }
        state["retry"] = {"target": "", "blockers": []}

    for path in sorted(evidence_paths, key=lambda item: MaintenanceHarness.EVIDENCE_ARTIFACT_PATHS.index(item)):
        absolute = root / path
        if not absolute.exists():
            continue
        candidate_id = active_candidate if path in MaintenanceHarness.CANDIDATE_ARTIFACT_PATHS else ""
        revision = int(indexed.get(path, {}).get("revision") or 1)
        canonical_path = _canonical_evidence_path(workflow_id, candidate_id or "run", path, revision)
        content_sha256 = _archive_evidence_artifact(root, path, canonical_path)
        indexed[path] = {
            "path": path,
            "canonical_path": canonical_path,
            "workflow_id": workflow_id,
            "candidate_id": candidate_id,
            "phase": phase.value,
            "revision": revision,
            "content_sha256": content_sha256,
        }
    state["artifacts"] = list(indexed.values())
    worker_status = _reconciled_worker_status(root, state, latest, evidence_paths)
    changed_files = _implementation_changed_files(root, workflow_id)
    if changed_files:
        state["changed_files"] = list(changed_files)
    policy = _policy_decision(root, state)
    worker_status = _with_policy_required_flags(worker_status, policy.route)
    profile_path = policy.profile.value
    if (
        profile_path == "TINY_DOC"
        and phase == Phase.DRAFT_PLANNED
        and worker_ready(worker_status.get("maintenance-planner", {}))
    ):
        phase = Phase.AWAITING_HUMAN_APPROVAL
        state["phase"] = phase.value
        approval_gate = state.get("approval_gate") if isinstance(state.get("approval_gate"), dict) else {}
        state["approval_gate"] = {
            **approval_gate,
            "status": "awaiting-human-approval",
            "requires_human_approval": True,
        }
        state["retry"] = {"target": "", "blockers": []}
    if (
        profile_path != "TINY_DOC"
        and phase == Phase.PLAN_REVIEWED
        and worker_ready(worker_status.get("maintenance-plan-critic", {}))
        and _plan_review_scope_fit_ready(root)
    ):
        phase = Phase.AWAITING_HUMAN_APPROVAL
        state["phase"] = phase.value
        approval_gate = state.get("approval_gate") if isinstance(state.get("approval_gate"), dict) else {}
        state["approval_gate"] = {
            **approval_gate,
            "status": "awaiting-human-approval",
            "requires_human_approval": True,
        }
        state["retry"] = {"target": "", "blockers": []}
    state["worker_status"] = worker_status
    retry_target = _retry_target_from_worker_status(worker_status)
    if retry_target:
        state["retry"] = {"target": retry_target, "blockers": _retry_blockers_from_worker_status(worker_status)}
    if profile_path != "TINY_DOC" and phase == Phase.AWAITING_HUMAN_APPROVAL and not _plan_review_scope_fit_ready(root):
        phase = Phase.PLAN_REVIEWED
        state["phase"] = phase.value
        approval_gate = state.get("approval_gate") if isinstance(state.get("approval_gate"), dict) else {}
        state["approval_gate"] = {
            **approval_gate,
            "status": "not-ready",
            "requires_human_approval": False,
        }
        retry = state.get("retry") if isinstance(state.get("retry"), dict) else {}
        state["retry"] = {**retry, "target": "retry-plan", "blockers": ["plan review scope fit missing"]}
    if (
        phase == Phase.EVALUATED
        and worker_ready(worker_status.get("maintenance-skeptic", {}))
    ):
        phase = Phase.SKEPTIC_REVIEWED
        state["phase"] = phase.value
    evidence_reconciled = (
        phase.value != original_phase
        or active_candidate != original_active_candidate
        or changed_files != original_changed_files
        or indexed != original_indexed
        or worker_status != original_worker_status
    )
    state["pass_eligibility"] = _reconciled_pass_eligibility(root, state, evidence_paths, worker_status)
    if evidence_reconciled:
        state["latest_event"] = latest
    plan_metadata = _plan_metadata(root)
    if active_candidate and plan_metadata:
        ledger = state.get("failure_mode_ledger") if isinstance(state.get("failure_mode_ledger"), dict) else {}
        severity = str(plan_metadata.get("failure_mode_severity") or "P3").upper()
        mapped = bool(plan_metadata.get("failure_mode_mapped"))
        if severity in {"P0", "P1", "P2"} or mapped:
            state["failure_mode_ledger"] = {
                **ledger,
                "required": severity in {"P0", "P1", "P2"},
                "severity": severity if severity in {"P0", "P1", "P2", "P3"} else "P3",
                "full_replay_required": severity in {"P0", "P1"},
                "mapped": mapped,
                "direct_evidence": mapped,
            }
    MaintenanceHarness.validate_state_checkpoint(state)
    return state


def _plan_review_scope_fit_ready(root: Path) -> bool:
    metadata = _plan_review_metadata(root)
    return bool(metadata.get("approval_ready") is True)


def _phase_from_evidence(evidence_paths: set[str], latest: dict[str, Any]) -> Phase:
    phase = Phase.INTAKE
    if "ops/agent-harness/evidence/cartography.json" in evidence_paths:
        phase = Phase.CARTOGRAPHED
    if "ops/agent-harness/evidence/plan.json" in evidence_paths:
        phase = Phase.DRAFT_PLANNED
    if "ops/agent-harness/evidence/plan-review.json" in evidence_paths:
        phase = Phase.PLAN_REVIEWED
    if "ops/agent-harness/evidence/execution.json" in evidence_paths:
        phase = Phase.IMPLEMENTED
    if "ops/agent-harness/evidence/execution-review.json" in evidence_paths:
        phase = Phase.EVALUATED
    if "ops/agent-harness/evidence/skeptic-review.json" in evidence_paths:
        phase = Phase.SKEPTIC_REVIEWED
    return phase


def _indexed_evidence_paths(root: Path, workflow_id: str) -> set[str]:
    index = read_json_object(root / harness_paths.ARTIFACT_INDEX_JSON, missing_ok=True)
    if str(index.get("workflow_id") or "").strip() != workflow_id:
        return set()
    artifacts = index.get("artifacts") if isinstance(index.get("artifacts"), list) else []
    paths: set[str] = set()
    for row in artifacts:
        if not isinstance(row, dict):
            continue
        if str(row.get("workflow_id") or "").strip() != workflow_id:
            continue
        latest_path = str(row.get("latest_path") or "").strip()
        if latest_path in MaintenanceHarness.EVIDENCE_ARTIFACT_PATHS:
            paths.add(latest_path)
    return paths


def _max_phase(current: Phase, observed: Phase) -> Phase:
    return observed if PHASE_ORDER[observed] > PHASE_ORDER[current] else current


def _reconciled_worker_status(root: Path, state: dict[str, Any], latest: dict[str, Any], evidence_paths: set[str]) -> dict[str, dict[str, bool]]:
    worker_status = {
        str(worker): _structured_worker_row(row)
        for worker, row in (state.get("worker_status") or {}).items()
        if isinstance(row, dict)
    }
    for path in sorted(evidence_paths):
        row = _worker_row_from_evidence_file(root, path, state)
        if row:
            worker_status[str(row["worker"])] = row
    return worker_status


def _structured_worker_row(row: dict[str, Any]) -> dict[str, bool]:
    blocking_findings = row.get("blocking_findings", [])
    if not isinstance(blocking_findings, list):
        blocking_findings = []
    return {
        "required": bool(row.get("required", True)),
        "invoked": bool(row.get("invoked", False)),
        "worker": str(row.get("worker", "")).strip(),
        "evidence_kind": str(row.get("evidence_kind", "")).strip(),
        "status": str(row.get("status", "")).strip(),
        "blocking_findings": [str(item).strip() for item in blocking_findings if str(item).strip()],
        "evidence": str(row.get("evidence") or row.get("summary") or "").strip(),
        "artifact_path": str(row.get("artifact_path", "")).strip(),
        "artifact_sha256": str(row.get("artifact_sha256", "")).strip(),
        "retry_target": str(row.get("retry_target", "")).strip(),
        "schema_version": int(row.get("schema_version") or 0),
        "structured_evidence_valid": bool(row.get("structured_evidence_valid", False)),
    }


def _worker_row_from_evidence_file(root: Path, path: str, state: dict[str, Any]) -> dict[str, Any]:
    expected_worker = _expected_worker_for_evidence(root, path, state)
    if not expected_worker:
        return {}
    absolute = root / path
    if not absolute.is_file() or absolute.is_symlink():
        return {}
    try:
        raw = absolute.read_text(encoding="utf-8")
        loaded = json.loads(raw)
    except (OSError, ValueError):
        return {}
    if not isinstance(loaded, dict):
        return {}
    blocking_findings = loaded.get("blocking_findings")
    valid = (
        loaded.get("schema_version") == 1
        and loaded.get("worker") == expected_worker
        and loaded.get("evidence_kind") == "structured-json"
        and loaded.get("status") in {"passed", "failed"}
        and isinstance(blocking_findings, list)
        and isinstance(loaded.get("summary"), str)
        and bool(str(loaded.get("summary") or "").strip())
    )
    if not valid:
        return {}
    structured_evidence_valid = valid
    return {
        "required": True,
        "invoked": True,
        "worker": expected_worker,
        "evidence_kind": "structured-json",
        "status": str(loaded.get("status") or ""),
        "blocking_findings": [str(item).strip() for item in blocking_findings if str(item).strip()],
        "evidence": str(loaded.get("summary") or "").strip(),
        "artifact_path": path,
        "artifact_sha256": _sha256_text(raw),
        "retry_target": str(loaded.get("retry_target") or "").strip(),
        "schema_version": 1,
        "structured_evidence_valid": structured_evidence_valid,
    }


def _with_policy_required_flags(
    worker_status: dict[str, dict[str, Any]], route: tuple[str, ...]
) -> dict[str, dict[str, Any]]:
    route_workers = set(route)
    normalized: dict[str, dict[str, Any]] = {}
    for worker, row in worker_status.items():
        copied = dict(row)
        copied["required"] = worker in route_workers
        normalized[worker] = copied
    return normalized


def _expected_worker_for_evidence(root: Path, path: str, state: dict[str, Any]) -> str:
    if path == "ops/agent-harness/evidence/execution-review.json" and _workflow_profile_path(root, state) == "TINY_DOC":
        return "host-verifier"
    return PATH_TO_WORKER.get(path, "")


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _worker_ready(row: dict[str, Any]) -> bool:
    return worker_ready(row)


def _implementation_changed_files(root: Path, workflow_id: str) -> tuple[str, ...]:
    seen: set[str] = set()
    changed: list[str] = []
    for event in _read_recent_events(root / EVENTS_JSONL):
        if event.get("workflow_id") != workflow_id or event.get("event") != "implementation-allow":
            continue
        raw_path = str(event.get("artifact_path") or event.get("path") or "")
        rel = relative_to_root(root, raw_path) or raw_path
        if not rel or rel.startswith("ops/agent-harness/"):
            continue
        if rel not in seen:
            seen.add(rel)
            changed.append(rel)
    return tuple(changed)


def _reconciled_pass_eligibility(root: Path, state: dict[str, Any], evidence_paths: set[str], worker_status: dict[str, dict[str, bool]]) -> dict[str, Any]:
    existing = state.get("pass_eligibility") if isinstance(state.get("pass_eligibility"), dict) else {}
    blockers = [blocker for blocker in _pass_blockers(state) if blocker != "mandatory worker evidence pending"]
    policy = _policy_decision(root, state)
    mandatory_workers = policy.required_workers
    required_artifacts = policy.required_artifacts
    all_workers_ready = all(
        _worker_ready(worker_status.get(worker, {}))
        for worker in mandatory_workers
    )
    all_artifacts_ready = set(required_artifacts) <= (evidence_paths | {MaintenanceHarness.STATE_ARTIFACT_PATH})
    if not (all_workers_ready and all_artifacts_ready):
        blockers.append("mandatory worker evidence pending")
    route_cursor = _route_cursor(policy.route, worker_status, evidence_paths, policy.required_artifacts, retry_target=_retry_target_from_worker_status(worker_status))
    calculated = _calculated_pass_eligibility(root, state, evidence_paths, worker_status, blockers)
    return {
        "eligible": calculated["eligible"],
        "blocked_by": blockers,
        "workflow_profile": _policy_checkpoint(policy),
        "route_cursor": route_cursor,
        "calculated": calculated,
    }


def _policy_checkpoint(policy) -> dict[str, Any]:
    return {
        "path": policy.profile.value,
        "route": list(policy.route),
        "surface_classes": [surface_class.value for surface_class in policy.surface_classes],
        "required_workers": list(policy.required_workers),
        "required_artifacts": list(policy.required_artifacts),
        "host_verifier_allowed": policy.host_verifier_allowed,
        "verification_mode": policy.verification_mode.value,
        "reason": policy.reason,
    }


def _route_cursor(
    route: tuple[str, ...],
    worker_status: dict[str, dict[str, bool]],
    evidence_paths: set[str],
    required_artifacts: tuple[str, ...],
    *,
    retry_target: str = "",
) -> dict[str, Any]:
    retry_worker = _retry_worker_for_route(route, retry_target)
    if retry_worker:
        retry_artifact = _retry_artifact_path(retry_target)
        return {
            "route": list(route),
            "completed_workers": [worker for worker in route if route.index(worker) < route.index(retry_worker) and _worker_ready(worker_status.get(worker, {}))],
            "next_required_worker": retry_worker,
            "remaining_required_artifacts": [retry_artifact] if retry_artifact else [],
            "retry_target": retry_target,
        }
    completed = []
    next_worker = ""
    for worker in route:
        if _worker_ready(worker_status.get(worker, {})) or (
            worker == "host-verifier" and "ops/agent-harness/evidence/execution-review.json" in evidence_paths
        ):
            completed.append(worker)
            continue
        else:
            next_worker = worker
            break
    missing_artifacts = _missing_artifacts_for_route(route, evidence_paths, required_artifacts)
    return {
        "route": list(route),
        "completed_workers": completed,
        "next_required_worker": next_worker,
        "remaining_required_artifacts": missing_artifacts,
    }


def _missing_artifacts_for_route(route: tuple[str, ...], evidence_paths: set[str], required_artifacts: tuple[str, ...]) -> list[str]:
    expected = set(required_artifacts)
    missing: list[str] = []
    for worker in route:
        artifact = _artifact_for_worker(worker)
        if artifact and artifact in expected and artifact not in evidence_paths and artifact not in missing:
            missing.append(artifact)
    return missing


def _artifact_for_worker(worker: str) -> str:
    return {
        "maintenance-cartographer": "ops/agent-harness/evidence/cartography.json",
        "maintenance-planner": "ops/agent-harness/evidence/plan.json",
        "maintenance-plan-critic": "ops/agent-harness/evidence/plan-review.json",
        "maintenance-implementer": "ops/agent-harness/evidence/execution.json",
        "maintenance-evaluator": "ops/agent-harness/evidence/execution-review.json",
        "maintenance-skeptic": "ops/agent-harness/evidence/skeptic-review.json",
        "host-verifier": "ops/agent-harness/evidence/execution-review.json",
    }.get(worker, "")


def _retry_target_from_worker_status(worker_status: dict[str, dict[str, Any]]) -> str:
    for row in worker_status.values():
        target = str(row.get("retry_target") or "").strip()
        if target.startswith("retry-") and str(row.get("status") or "") == "failed":
            return target
    return ""


def _retry_blockers_from_worker_status(worker_status: dict[str, dict[str, Any]]) -> list[str]:
    blockers: list[str] = []
    for row in worker_status.values():
        if str(row.get("status") or "") != "failed" or not str(row.get("retry_target") or "").startswith("retry-"):
            continue
        findings = row.get("blocking_findings") if isinstance(row.get("blocking_findings"), list) else []
        blockers.extend(str(item) for item in findings if str(item).strip())
    return blockers


def _retry_worker_for_route(route: tuple[str, ...], retry_target: str) -> str:
    worker = RETRY_AGENT_PRIMARY.get(retry_target, "")
    return worker if worker in route else ""


def _retry_artifact_path(retry_target: str) -> str:
    rule = RETRY_ARTIFACT_PRIMARY.get(retry_target)
    if not rule:
        return ""
    kind, _worker = rule
    artifact_name = harness_paths.LATEST_ARTIFACTS.get(kind)
    return str(harness_paths.ARTIFACT_ROOT / artifact_name) if artifact_name else ""


def _calculated_pass_eligibility(
    root: Path,
    state: dict[str, Any],
    evidence_paths: set[str],
    worker_status: dict[str, dict[str, bool]],
    state_blockers: list[str],
) -> dict[str, Any]:
    verification_passed = _execution_review_records_verification(root, evidence_paths)
    tests_passed = verification_passed
    policy = _policy_decision(root, state)
    profile_path = policy.profile.value
    tiny_doc = profile_path == "TINY_DOC"
    pass_candidate = bool(
        (tiny_doc and _worker_ready(worker_status.get("maintenance-implementer", {})))
        or (tiny_doc and _worker_ready(worker_status.get("host-verifier", {})))
        or (verification_passed and _worker_ready(worker_status.get("maintenance-evaluator", {})))
    )
    return calculate_pass_eligibility(
        required_artifact_paths=policy.required_artifacts,
        available_artifact_paths=evidence_paths | {MaintenanceHarness.STATE_ARTIFACT_PATH},
        mandatory_workers=policy.required_workers,
        worker_status=worker_status,
        approval_hash_ok=_approval_hash_ok(root, state),
        tests_passed=tests_passed,
        scope_ok=_changed_files_within_approval(root, state),
        pass_candidate=pass_candidate,
        state_confirmed=not bool(state_blockers),
        state_blockers=state_blockers,
        workflow_path=profile_path,
    )


def _workflow_profile_path(root: Path, state: dict[str, Any]) -> str:
    return _policy_decision(root, state).profile.value


def _policy_decision(root: Path, state: dict[str, Any]):
    metadata = _plan_metadata(root)
    surfaces = tuple(str(path) for path in metadata.get("affected_surfaces", []) if str(path).strip()) if isinstance(metadata.get("affected_surfaces"), list) else ()
    if not surfaces:
        approval_gate = state.get("approval_gate") if isinstance(state.get("approval_gate"), dict) else {}
        freeze = approval_gate.get("freeze") if isinstance(approval_gate.get("freeze"), dict) else {}
        surfaces = tuple(str(path) for path in freeze.get("affected_surfaces", []) if str(path).strip()) if isinstance(freeze.get("affected_surfaces"), list) else ()
    if not surfaces:
        surfaces = tuple(str(path) for path in state.get("changed_files", []) if str(path).strip()) if isinstance(state.get("changed_files"), list) else ()
    severity = str(metadata.get("failure_mode_severity") or state.get("failure_mode_ledger", {}).get("severity") or "P3").upper()
    ambiguity = _cartography_artifact_exists(root)
    verification_mode = str(metadata.get("verification_mode") or "semantic")
    return policy_for_surfaces(surfaces, severity=severity, ambiguity=ambiguity, verification_mode=verification_mode)


def _cartography_artifact_exists(root: Path) -> bool:
    path = root / "ops/agent-harness/evidence/cartography.json"
    return path.is_file() and not path.is_symlink()


def _changed_files_within_approval(root: Path, state: dict[str, Any]) -> bool:
    changed_files = tuple(str(path) for path in state.get("changed_files", []) if str(path).strip()) if isinstance(state.get("changed_files"), list) else ()
    approval_gate = state.get("approval_gate") if isinstance(state.get("approval_gate"), dict) else {}
    freeze = approval_gate.get("freeze") if isinstance(approval_gate.get("freeze"), dict) else {}
    surfaces = tuple(str(path) for path in freeze.get("affected_surfaces", []) if str(path).strip()) if isinstance(freeze.get("affected_surfaces"), list) else ()
    if not surfaces:
        return False
    baseline = freeze.get("pre_existing_dirty_files") if isinstance(freeze.get("pre_existing_dirty_files"), list) else []
    pre_existing = {str(path).strip() for path in baseline if str(path).strip()}
    baseline_fingerprints = freeze.get("pre_existing_dirty_fingerprints") if isinstance(freeze.get("pre_existing_dirty_fingerprints"), dict) else {}
    current_dirty = _current_dirty_files(root)
    candidate_pool = set(current_dirty) if (root / ".git").exists() else set(changed_files) | set(current_dirty)
    candidate_changed_files = tuple(
        path
        for path in sorted(candidate_pool)
        if not path.startswith("ops/agent-harness/")
        and (path not in pre_existing or _dirty_fingerprint(root, path) != str(baseline_fingerprints.get(path) or ""))
    )
    return bool(candidate_changed_files) and all(MaintenanceHarness._path_in_approved_surfaces(path, surfaces) for path in candidate_changed_files)


def _approval_hash_ok(root: Path, state: dict[str, Any]) -> bool:
    approval_gate = state.get("approval_gate") if isinstance(state.get("approval_gate"), dict) else {}
    freeze = approval_gate.get("freeze") if isinstance(approval_gate.get("freeze"), dict) else {}
    frozen_contract_hash = str(freeze.get("plan_contract_hash") or "").strip()
    current_contract_hash = str(_plan_metadata(root).get("plan_contract_hash") or "").strip()
    return bool(
        approval_gate.get("status") == "approved-frozen"
        and frozen_contract_hash
        and current_contract_hash
        and frozen_contract_hash == current_contract_hash
    )


def _current_dirty_files(root: Path) -> tuple[str, ...]:
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain", "--untracked-files=all"],
            cwd=root,
            check=False,
            capture_output=True,
            text=True,
            timeout=5,
        )
    except (OSError, subprocess.TimeoutExpired):
        return tuple()
    if result.returncode != 0:
        return tuple()
    dirty: list[str] = []
    for line in result.stdout.splitlines():
        path = line[3:].strip() if len(line) > 3 else ""
        if " -> " in path:
            path = path.rsplit(" -> ", 1)[-1].strip()
        if path:
            dirty.append(path)
    return tuple(dict.fromkeys(dirty))


def _dirty_fingerprint(root: Path, path: str) -> str:
    target = root / path
    if not target.is_file() or target.is_symlink():
        return ""
    try:
        return hashlib.sha256(target.read_bytes()).hexdigest()
    except OSError:
        return ""


def _execution_review_records_verification(root: Path, evidence_paths: set[str]) -> bool:
    path = "ops/agent-harness/evidence/execution-review.json"
    if path not in evidence_paths:
        return False
    metadata = read_json_object(root / harness_paths.EXECUTION_REVIEW_METADATA_JSON, missing_ok=True)
    return bool(isinstance(metadata, dict) and metadata.get("schema_version") == 1 and metadata.get("verification_passed") is True)


def _pass_blockers(state: dict[str, Any]) -> list[str]:
    value = state.get("pass_eligibility") if isinstance(state.get("pass_eligibility"), dict) else {}
    blockers = value.get("blocked_by") if isinstance(value, dict) else []
    if not isinstance(blockers, list):
        return list()
    return [str(blocker) for blocker in blockers if str(blocker).strip()]


def _canonical_evidence_path(workflow_id: str, candidate_id: str, latest_path: str, revision: int) -> str:
    artifact_name = latest_path.rsplit("/", 1)[-1]
    return f"{MaintenanceHarness.RUN_ARCHIVE_ROOT}/{workflow_id}/candidates/{candidate_id}/r{revision:03d}-{artifact_name}"


def _archive_evidence_artifact(root: Path, latest_path: str, canonical_path: str) -> str:
    source = root / latest_path
    target = root / canonical_path
    if source.is_symlink() or target.is_symlink():
        raise RuntimeError("maintenance evidence archive refuses symlink paths")
    content = source.read_bytes()
    write_text_atomic_under_root(target, content.decode("utf-8", errors="replace"), root)
    import hashlib

    return hashlib.sha256(content).hexdigest()


def _candidate_state(root: Path) -> dict[str, Any]:
    state = read_json_object(root / "ops" / "agent-harness" / "latest-candidate-state.json", missing_ok=True)
    if not isinstance(state, dict):
        return {}
    if state.get("schema_version") != 1:
        return {}
    return state


def _plan_metadata(root: Path) -> dict[str, Any]:
    metadata = read_json_object(root / harness_paths.PLAN_METADATA_JSON, missing_ok=True)
    return metadata if isinstance(metadata, dict) and metadata.get("schema_version") == 1 else {}


def _plan_review_metadata(root: Path) -> dict[str, Any]:
    metadata = read_json_object(root / harness_paths.PLAN_REVIEW_METADATA_JSON, missing_ok=True)
    return metadata if isinstance(metadata, dict) and metadata.get("schema_version") == 1 else {}


def _canonical_state_checkpoint(marker: dict[str, Any], latest: dict[str, Any], *, workflow_id: str) -> dict[str, Any]:
    harness = MaintenanceHarness(workflow_id=workflow_id)
    harness.phase = _checkpoint_phase(str(latest.get("phase") or ""))
    harness.active_candidate = str(marker.get("prompt_excerpt") or "")
    harness.active_candidate_id = str(marker.get("active_candidate_id") or "")
    queued = marker.get("queued_candidate_ids")
    harness.queued_candidate_ids = tuple(str(candidate) for candidate in queued if str(candidate).strip()) if isinstance(queued, list) else ()
    harness.terminal_candidate = len(harness.queued_candidate_ids) == 0
    harness.queue_policy = str(marker.get("queue_policy") or "human-decision")
    checkpoint = harness.state_checkpoint()
    checkpoint["latest_event"] = latest
    MaintenanceHarness.validate_state_checkpoint(checkpoint)
    return checkpoint


def _checkpoint_phase(value: str) -> Phase:
    try:
        return Phase(value)
    except ValueError:
        return Phase.INTAKE




def _read_recent_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        return list()
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            import json

            loaded = json.loads(line)
        except Exception:
            continue
        if isinstance(loaded, dict):
            events.append(loaded)
    return events

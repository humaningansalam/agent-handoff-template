from __future__ import annotations

import argparse
import hashlib
import json
import os
from pathlib import Path
from typing import Any, Sequence

from tools.agent_harness import paths as harness_paths
from tools.agent_harness.harness import MaintenanceHarness
from tools.agent_harness.pass_gate import worker_ready
from tools.agent_harness.policy import (
    APPROVAL_HASH_PREFIX_LENGTH,
    SurfaceClass,
    VerificationMode,
    WorkflowProfile,
    approval_phrase,
    classify_surface,
    mechanical_verification_allowed,
    plan_contract_hash,
    plan_contract_payload,
    policy_for_surfaces,
)
from tools.agent_harness.retry_policy import retry_artifact_write_block_reason, retry_target
from tools.runtime.json_io import write_json_atomic_under_root, write_text_atomic_under_root

TRACE_ROOT = harness_paths.ARTIFACT_ROOT
KIND_TO_LATEST = harness_paths.LATEST_ARTIFACTS
CANDIDATE_KINDS = {"plan", "plan-review", "execution", "execution-review", "skeptic-review"}
KIND_TO_WORKER = {
    "cartography": "maintenance-cartographer",
    "plan": "maintenance-planner",
    "plan-review": "maintenance-plan-critic",
    "execution": "maintenance-implementer",
    "execution-review": "maintenance-evaluator",
    "skeptic-review": "maintenance-skeptic",
}
VALID_EVIDENCE_STATUSES = {"passed", "failed"}
VALID_RETRY_TARGETS = {
    "retry-plan",
    "retry-plan-metadata",
    "retry-approval-metadata",
    "retry-implementation",
    "retry-artifact-metadata",
    "retry-evaluation",
    "retry-scope-ledger",
    "retry-verification-metadata",
}


class SafeArtifactWriterError(RuntimeError):
    pass


def _canonical_path(workflow_id: str, candidate_id: str, latest_name: str, revision: int) -> str:
    return f"{MaintenanceHarness.RUN_ARCHIVE_ROOT}/{workflow_id}/candidates/{candidate_id}/r{revision:03d}-{Path(latest_name).name}"


def write_artifact(
    root: Path,
    *,
    kind: str,
    status: str = "passed",
    summary: str = "",
    blocking_findings: Sequence[str] = (),
    finding_ids: Sequence[str] = (),
    finding_surfaces: Sequence[str] = (),
    finding_expected: Sequence[str] = (),
    finding_observed: Sequence[str] = (),
    finding_verdicts: Sequence[str] = (),
    finding_severities: Sequence[str] = (),
    retry_target_value: str = "",
    checked_commands: Sequence[str] = (),
    checked_surfaces: Sequence[str] = (),
    evidence_refs: Sequence[str] = (),
    workflow_id: str,
    candidate_id: str = "",
    active_candidate_id: str = "",
    queued_candidate_ids: Sequence[str] = (),
    queue_policy: str = "",
    affected_surfaces: Sequence[str] = (),
    acceptance_criteria_ids: Sequence[str] = (),
    failure_mode_severity: str = "P3",
    failure_mode_mapped: bool = False,
    verification_mode: str = VerificationMode.SEMANTIC.value,
    approval_ready: bool | None = None,
    verification_passed: bool | None = None,
    revision: int = 1,
) -> dict[str, Any]:
    normalized_kind = kind.strip()
    latest_name = KIND_TO_LATEST.get(normalized_kind)
    if latest_name is None:
        raise SafeArtifactWriterError(f"unknown maintenance artifact kind: {kind}")
    workflow = workflow_id.strip()
    if not workflow:
        raise SafeArtifactWriterError("workflow-id is required")
    _require_active_workflow_match(root, workflow)
    candidate = candidate_id.strip() or "run"
    if normalized_kind in CANDIDATE_KINDS and candidate == "run":
        raise SafeArtifactWriterError(f"candidate-id is required for {normalized_kind}")
    if normalized_kind in CANDIDATE_KINDS:
        _require_candidate_lineage_match(root, workflow, candidate)
    if revision < 1:
        raise SafeArtifactWriterError("revision must be >= 1")
    normalized_queue_policy = queue_policy.strip()
    if normalized_queue_policy and normalized_queue_policy not in {"human-decision", "auto-continuation"}:
        raise SafeArtifactWriterError("queue-policy must be human-decision or auto-continuation")
    _require_plan_review_route_before_replanning(root, workflow, normalized_kind)
    _require_policy_route_prerequisites(
        root,
        kind=normalized_kind,
        affected_surfaces=affected_surfaces,
        acceptance_criteria_ids=acceptance_criteria_ids,
        failure_mode_severity=failure_mode_severity,
        verification_mode=verification_mode,
    )

    evidence = _structured_evidence(
        normalized_kind,
        worker=_worker_for_evidence(root, normalized_kind),
        status=status,
        summary=summary,
        blocking_findings=blocking_findings,
        findings=_structured_findings(
            finding_ids=finding_ids,
            finding_surfaces=finding_surfaces,
            finding_expected=finding_expected,
            finding_observed=finding_observed,
            finding_verdicts=finding_verdicts,
            finding_severities=finding_severities,
        ),
        retry_target_value=retry_target_value,
        checked_commands=checked_commands,
        checked_surfaces=checked_surfaces,
        evidence_refs=evidence_refs,
    )
    content = _canonical_evidence_text(evidence)
    latest_path = TRACE_ROOT / latest_name
    canonical = _canonical_path(workflow, candidate, latest_name, revision)
    root = root.resolve()
    write_text_atomic_under_root(root / latest_path, content, root)
    write_text_atomic_under_root(root / canonical, content, root)
    candidate_state = _candidate_state_payload(
        workflow_id=workflow,
        active_candidate_id=active_candidate_id or candidate_id,
        queued_candidate_ids=queued_candidate_ids,
        queue_policy=normalized_queue_policy,
    )
    if candidate_state:
        write_json_atomic_under_root(root / harness_paths.CANDIDATE_STATE_JSON, candidate_state, root)
    sha256 = hashlib.sha256(content.encode("utf-8")).hexdigest()
    if normalized_kind == "plan":
        plan_metadata = _plan_metadata_payload(
            root=root,
            workflow_id=workflow,
            candidate_id=candidate,
            affected_surfaces=affected_surfaces,
            acceptance_criteria_ids=acceptance_criteria_ids,
            failure_mode_severity=failure_mode_severity,
            failure_mode_mapped=failure_mode_mapped,
            verification_mode=verification_mode,
            plan_sha256=sha256,
        )
        write_json_atomic_under_root(root / harness_paths.PLAN_METADATA_JSON, plan_metadata, root)
    if normalized_kind == "plan-review":
        if approval_ready is None:
            raise SafeArtifactWriterError("plan-review artifacts require approval-ready metadata")
        write_json_atomic_under_root(
            root / harness_paths.PLAN_REVIEW_METADATA_JSON,
            {
                "schema_version": 1,
                "workflow_id": workflow,
                "candidate_id": candidate,
                "approval_ready": approval_ready,
            },
            root,
        )
    if normalized_kind == "execution-review":
        if verification_passed is None:
            raise SafeArtifactWriterError("execution-review artifacts require verification-passed metadata")
        write_json_atomic_under_root(
            root / harness_paths.EXECUTION_REVIEW_METADATA_JSON,
            {
                "schema_version": 1,
                "workflow_id": workflow,
                "candidate_id": candidate,
                "verification_passed": verification_passed,
            },
            root,
        )
    _upsert_artifact_index(
        root,
        {
            "kind": normalized_kind,
            "latest_path": latest_path.as_posix(),
            "canonical_path": canonical,
            "workflow_id": workflow,
            "candidate_id": "" if normalized_kind == "cartography" and not candidate_id.strip() else candidate,
            "revision": revision,
            "sha256": sha256,
        },
    )
    result = {
        "kind": normalized_kind,
        "latest_path": latest_path.as_posix(),
        "canonical_path": canonical,
        "workflow_id": workflow,
        "candidate_id": "" if normalized_kind == "cartography" and not candidate_id.strip() else candidate,
        "revision": revision,
        "sha256": sha256,
    }
    if normalized_kind == "plan":
        contract_hash = str(plan_metadata["plan_contract_hash"])
        result["plan_contract_hash"] = contract_hash
        result["approval_phrase"] = approval_phrase(candidate, contract_hash)
    return result


def _require_active_workflow_match(root: Path, workflow_id: str) -> None:
    state_path = root / harness_paths.STATE_JSON
    if state_path.is_file() and not state_path.is_symlink():
        try:
            state = json.loads(state_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            state = {}
        state_workflow = str(state.get("workflow_id") or "").strip() if isinstance(state, dict) else ""
        if state_workflow and state_workflow != workflow_id:
            raise SafeArtifactWriterError(f"workflow-id must match current maintenance state: {state_workflow}")
    active_dir = root / harness_paths.ARTIFACT_ROOT / "active-sessions"
    if not active_dir.is_dir() or active_dir.is_symlink():
        return
    workflow_ids: set[str] = set()
    for marker_path in active_dir.glob("*.json"):
        if marker_path.is_symlink() or not marker_path.is_file():
            continue
        try:
            marker = json.loads(marker_path.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            continue
        marker_workflow = str(marker.get("workflow_id") or "").strip() if isinstance(marker, dict) else ""
        if marker_workflow:
            workflow_ids.add(marker_workflow)
    if workflow_ids and workflow_id not in workflow_ids:
        expected = ", ".join(sorted(workflow_ids))
        raise SafeArtifactWriterError(f"workflow-id must match active maintenance session: {expected}")


def _require_plan_review_route_before_replanning(root: Path, workflow_id: str, kind: str) -> None:
    state = _read_json(root / harness_paths.STATE_JSON)
    state = _state_with_structured_retry(state, root, workflow_id)
    if str(state.get("workflow_id") or "").strip() == workflow_id:
        retry_block = retry_artifact_write_block_reason(root, state, kind)
        if retry_block:
            raise SafeArtifactWriterError(retry_block)
    if kind not in {"cartography", "plan"}:
        return
    if str(state.get("workflow_id") or "").strip() != workflow_id:
        return
    worker_status = state.get("worker_status") if isinstance(state.get("worker_status"), dict) else {}
    critic = worker_status.get("maintenance-plan-critic") if isinstance(worker_status.get("maintenance-plan-critic"), dict) else {}
    critic_completed = worker_ready(critic)
    if not critic_completed:
        return
    metadata = _read_json(root / harness_paths.PLAN_REVIEW_METADATA_JSON)
    approval_ready = metadata.get("approval_ready") if isinstance(metadata, dict) else None
    if approval_ready is False:
        if retry_target(state):
            return
        if kind == "plan":
            return
        raise SafeArtifactWriterError("plan review is not approval-ready; rerun maintenance-planner before writing cartography")
    if approval_ready is True:
        raise SafeArtifactWriterError("plan review is approval-ready; do not replace cartography or plan before human approval")
    raise SafeArtifactWriterError("maintenance-plan-critic completed; write plan-review artifact with structured approval-ready metadata before replanning")


def _state_with_structured_retry(state: dict[str, Any], root: Path, workflow_id: str) -> dict[str, Any]:
    if str(state.get("workflow_id") or "").strip() != workflow_id:
        return state
    if retry_target(state):
        return state
    metadata = _read_json(root / harness_paths.PLAN_REVIEW_METADATA_JSON)
    if metadata.get("approval_ready") is not False:
        return state
    worker_status = state.get("worker_status") if isinstance(state.get("worker_status"), dict) else {}
    critic = worker_status.get("maintenance-plan-critic") if isinstance(worker_status.get("maintenance-plan-critic"), dict) else {}
    if worker_ready(critic):
        return {**state, "retry": {"target": "retry-plan", "blockers": ["plan review approval-ready metadata false"]}}
    return state


def _worker_for_evidence(root: Path, kind: str) -> str:
    if kind != "execution-review":
        return KIND_TO_WORKER[kind]
    metadata = _read_json(root / harness_paths.PLAN_METADATA_JSON)
    surfaces = tuple(str(path) for path in metadata.get("affected_surfaces", []) if str(path).strip()) if isinstance(metadata.get("affected_surfaces"), list) else ()
    severity = str(metadata.get("failure_mode_severity") or "P3").upper()
    verification_mode = str(metadata.get("verification_mode") or VerificationMode.SEMANTIC.value)
    if policy_for_surfaces(surfaces, severity=severity, verification_mode=verification_mode).profile == WorkflowProfile.TINY_DOC:
        return "host-verifier"
    return KIND_TO_WORKER[kind]


def _require_policy_route_prerequisites(
    root: Path,
    *,
    kind: str,
    affected_surfaces: Sequence[str],
    acceptance_criteria_ids: Sequence[str],
    failure_mode_severity: str,
    verification_mode: str,
) -> None:
    if kind != "plan":
        return
    surfaces = _normalize_list(affected_surfaces)
    criteria = _normalize_list(acceptance_criteria_ids)
    if not surfaces or not criteria:
        return
    mode = VerificationMode(str(verification_mode).strip() or VerificationMode.SEMANTIC.value)
    severity = failure_mode_severity.strip().upper() or "P3"
    if mode == VerificationMode.MECHANICAL and (len(surfaces) != 1 or severity in {"P0", "P1"}):
        raise SafeArtifactWriterError("mechanical verification requires exactly one affected surface and P2/P3 severity")
    _reject_forbidden_or_unsafe_mechanical_surfaces(surfaces, mode)
    policy = policy_for_surfaces(surfaces, severity=failure_mode_severity, verification_mode=verification_mode)
    if not policy.route:
        raise SafeArtifactWriterError("forbidden affected surfaces cannot be routed by maintenance workflow")
    if not policy.route or policy.route[0] != "maintenance-cartographer":
        return
    cartography_path = root / harness_paths.ARTIFACT_ROOT / harness_paths.LATEST_ARTIFACTS["cartography"]
    if not cartography_path.is_file() or cartography_path.is_symlink():
        raise SafeArtifactWriterError(f"policy route {policy.profile.value} requires cartography evidence before writing plan")
    if policy.profile == WorkflowProfile.CRITICAL_HARNESS and len(surfaces) >= 4 and not _cartography_has_sharded_queue(root):
        raise SafeArtifactWriterError("CRITICAL_HARNESS plans with 4 or more affected surfaces require cartography shard queue before writing plan")


def _cartography_has_sharded_queue(root: Path) -> bool:
    candidate_state = _read_json(root / harness_paths.CANDIDATE_STATE_JSON)
    queued = candidate_state.get("queued_candidate_ids") if isinstance(candidate_state.get("queued_candidate_ids"), list) else []
    active = str(candidate_state.get("active_candidate_id") or "").strip()
    return bool(active and [candidate for candidate in queued if str(candidate).strip()])


def _cartography_artifact_exists(root: Path) -> bool:
    path = root / harness_paths.ARTIFACT_ROOT / harness_paths.LATEST_ARTIFACTS["cartography"]
    return path.is_file() and not path.is_symlink()


def _structured_evidence(
    kind: str,
    *,
    worker: str,
    status: str,
    summary: str,
    blocking_findings: Sequence[str],
    findings: Sequence[dict[str, str]] = (),
    retry_target_value: str = "",
    checked_commands: Sequence[str] = (),
    checked_surfaces: Sequence[str] = (),
    evidence_refs: Sequence[str] = (),
) -> dict[str, Any]:
    normalized_status = status.strip()
    if normalized_status not in VALID_EVIDENCE_STATUSES:
        raise SafeArtifactWriterError("evidence status must be passed or failed")
    normalized_summary = summary.strip()
    if not normalized_summary:
        raise SafeArtifactWriterError("evidence summary is required")
    blockers = [str(item).strip() for item in blocking_findings if str(item).strip()]
    evidence: dict[str, Any] = {
        "schema_version": 1,
        "worker": worker,
        "evidence_kind": "structured-json",
        "status": normalized_status,
        "blocking_findings": blockers,
        "summary": normalized_summary,
    }
    if findings:
        evidence["findings"] = list(findings)
    normalized_retry = retry_target_value.strip()
    if normalized_retry:
        if normalized_retry not in VALID_RETRY_TARGETS:
            allowed = ", ".join(sorted(VALID_RETRY_TARGETS))
            raise SafeArtifactWriterError(f"retry-target must be one of: {allowed}")
        evidence["retry_target"] = normalized_retry
    commands = _normalize_list(checked_commands)
    if commands:
        evidence["checked_commands"] = commands
    surfaces = _normalize_list(checked_surfaces)
    if surfaces:
        evidence["checked_surfaces"] = surfaces
    refs = _normalize_list(evidence_refs)
    if refs:
        evidence["evidence_refs"] = refs
    return evidence


def _structured_findings(
    *,
    finding_ids: Sequence[str],
    finding_surfaces: Sequence[str],
    finding_expected: Sequence[str],
    finding_observed: Sequence[str],
    finding_verdicts: Sequence[str],
    finding_severities: Sequence[str],
) -> tuple[dict[str, str], ...]:
    fields = (finding_ids, finding_surfaces, finding_expected, finding_observed, finding_verdicts, finding_severities)
    width = max((len(field) for field in fields), default=0)
    if width == 0:
        return ()
    findings: list[dict[str, str]] = []
    for index in range(width):
        finding_id = _nth(finding_ids, index)
        verdict = _nth(finding_verdicts, index)
        if not finding_id or not verdict:
            raise SafeArtifactWriterError("finding-id and finding-verdict are required for every finding row")
        if verdict not in {"pass", "fail", "warn"}:
            raise SafeArtifactWriterError("finding-verdict must be pass, fail, or warn")
        severity = _nth(finding_severities, index) or "P3"
        if severity not in {"P0", "P1", "P2", "P3"}:
            raise SafeArtifactWriterError("finding-severity must be P0, P1, P2, or P3")
        row = {
            "id": finding_id,
            "surface": _nth(finding_surfaces, index),
            "expected": _nth(finding_expected, index),
            "observed": _nth(finding_observed, index),
            "verdict": verdict,
            "severity": severity,
        }
        findings.append({key: value for key, value in row.items() if value})
    return tuple(findings)


def _nth(values: Sequence[str], index: int) -> str:
    if index >= len(values):
        return ""
    return str(values[index]).strip()


def _canonical_evidence_text(evidence: dict[str, Any]) -> str:
    return json.dumps(evidence, ensure_ascii=False, indent=2, sort_keys=True) + "\n"


def _require_candidate_lineage_match(root: Path, workflow_id: str, candidate_id: str) -> None:
    candidate_state = _read_json(root / harness_paths.CANDIDATE_STATE_JSON)
    if str(candidate_state.get("workflow_id") or "").strip() != workflow_id:
        return
    active = str(candidate_state.get("active_candidate_id") or "").strip()
    if active and candidate_id != active:
        raise SafeArtifactWriterError(f"candidate-id must match active candidate: {active}")


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file() or path.is_symlink():
        return {}
    try:
        loaded = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return loaded if isinstance(loaded, dict) else {}


def _upsert_artifact_index(root: Path, record: dict[str, Any]) -> None:
    index = _read_json(root / harness_paths.ARTIFACT_INDEX_JSON)
    records = index.get("artifacts") if isinstance(index.get("artifacts"), list) else []
    kept = [
        row
        for row in records
        if isinstance(row, dict)
        and not (
            str(row.get("workflow_id") or "") == str(record.get("workflow_id") or "")
            and str(row.get("kind") or "") == str(record.get("kind") or "")
            and str(row.get("candidate_id") or "") == str(record.get("candidate_id") or "")
        )
    ]
    kept.append(record)
    write_json_atomic_under_root(
        root / harness_paths.ARTIFACT_INDEX_JSON,
        {"schema_version": 1, "workflow_id": record["workflow_id"], "artifacts": kept},
        root,
    )


def _candidate_state_payload(
    *,
    workflow_id: str,
    active_candidate_id: str = "",
    queued_candidate_ids: Sequence[str] = (),
    queue_policy: str = "",
) -> dict[str, Any]:
    active = active_candidate_id.strip()
    queued = _normalize_candidate_ids(queued_candidate_ids)
    if not active and not queued and not queue_policy:
        return {}
    policy = (queue_policy or "human-decision") if queued else ""
    if queued and not active:
        raise SafeArtifactWriterError("active-candidate-id is required when queued candidates are provided")
    if active in queued:
        raise SafeArtifactWriterError("active candidate cannot also be queued")
    return {
        "schema_version": 1,
        "workflow_id": workflow_id,
        "active_candidate_id": active,
        "queued_candidate_ids": queued,
        "queue_policy": policy,
        "terminal_candidate": not queued,
    }


def _plan_metadata_payload(
    *,
    root: Path,
    workflow_id: str,
    candidate_id: str,
    affected_surfaces: Sequence[str],
    acceptance_criteria_ids: Sequence[str],
    failure_mode_severity: str,
    failure_mode_mapped: bool,
    verification_mode: str,
    plan_sha256: str,
) -> dict[str, Any]:
    surfaces = _normalize_list(affected_surfaces)
    criteria = _normalize_list(acceptance_criteria_ids)
    if not surfaces:
        raise SafeArtifactWriterError("plan artifacts require at least one affected-surface metadata value")
    if not criteria:
        raise SafeArtifactWriterError("plan artifacts require at least one acceptance-criteria-id metadata value")
    severity = failure_mode_severity.strip().upper() or "P3"
    if severity not in {"P0", "P1", "P2", "P3"}:
        raise SafeArtifactWriterError("failure-mode-severity must be P0, P1, P2, or P3")
    mode = VerificationMode(str(verification_mode).strip() or VerificationMode.SEMANTIC.value)
    if mode == VerificationMode.MECHANICAL and (len(surfaces) != 1 or severity in {"P0", "P1"}):
        raise SafeArtifactWriterError("mechanical verification requires exactly one affected surface and P2/P3 severity")
    _reject_forbidden_or_unsafe_mechanical_surfaces(surfaces, mode)
    ambiguity = _cartography_artifact_exists(root)
    contract_payload = plan_contract_payload(
        candidate_id=candidate_id,
        affected_surfaces=surfaces,
        acceptance_criteria_ids=criteria,
        severity=severity,
        ambiguity=ambiguity,
        verification_mode=mode,
    )
    contract_hash = plan_contract_hash(
        candidate_id=candidate_id,
        affected_surfaces=surfaces,
        acceptance_criteria_ids=criteria,
        severity=severity,
        ambiguity=ambiguity,
        verification_mode=mode,
    )
    return {
        "schema_version": 1,
        "workflow_id": workflow_id,
        "candidate_id": candidate_id,
        "affected_surfaces": surfaces,
        "acceptance_criteria_ids": criteria,
        "surface_classes": contract_payload["surface_classes"],
        "profile": contract_payload["profile"],
        "route": contract_payload["route"],
        "verification_mode": contract_payload["verification_mode"],
        "reapproval_triggers": contract_payload["reapproval_triggers"],
        "failure_mode_severity": severity,
        "failure_mode_mapped": bool(failure_mode_mapped),
        "plan_body_sha256": plan_sha256,
        "plan_contract_hash": contract_hash,
        "approval_phrase": approval_phrase(candidate_id, contract_hash),
        "approval_phrase_hash_prefix_length": APPROVAL_HASH_PREFIX_LENGTH,
        "plan_sha256": plan_sha256,
    }


def _reject_forbidden_or_unsafe_mechanical_surfaces(surfaces: Sequence[str], mode: VerificationMode) -> None:
    classes = tuple(classify_surface(surface) for surface in surfaces)
    if any(surface_class == SurfaceClass.FORBIDDEN_SURFACE for surface_class in classes):
        raise SafeArtifactWriterError("forbidden affected surfaces cannot be routed by maintenance workflow")
    if mode != VerificationMode.MECHANICAL:
        return
    surface = str(surfaces[0]) if surfaces else ""
    if not mechanical_verification_allowed((surface,), classes):
        raise SafeArtifactWriterError("mechanical verification is limited to low-risk prose or the maintenance contract typo surface")


def _normalize_candidate_ids(values: Sequence[str]) -> list[str]:
    return _normalize_list(values, empty_tokens={"none", "null", "n/a", "na", "없음", "없다", "없음."})


def _normalize_list(values: Sequence[str], *, empty_tokens: set[str] | None = None) -> list[str]:
    normalized: list[str] = []
    for value in values:
        for item in str(value).split(","):
            normalized_item = item.strip()
            if empty_tokens and normalized_item.lower().strip(".。") in empty_tokens:
                continue
            if normalized_item and normalized_item not in normalized:
                normalized.append(normalized_item)
    return normalized


def parse_args(argv: Sequence[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(description="Safely write maintenance evidence artifacts")
    subparsers = parser.add_subparsers(dest="command", required=True)
    write_parser = subparsers.add_parser("write")
    write_parser.add_argument("--root", default=".")
    write_parser.add_argument("--kind", required=True, choices=sorted(KIND_TO_LATEST))
    write_parser.add_argument("--status", required=True, choices=sorted(VALID_EVIDENCE_STATUSES))
    write_parser.add_argument("--summary", required=True)
    write_parser.add_argument("--blocking-finding", action="append", default=[])
    write_parser.add_argument("--finding-id", action="append", default=[])
    write_parser.add_argument("--finding-surface", action="append", default=[])
    write_parser.add_argument("--finding-expected", action="append", default=[])
    write_parser.add_argument("--finding-observed", action="append", default=[])
    write_parser.add_argument("--finding-verdict", action="append", choices=("pass", "fail", "warn"), default=[])
    write_parser.add_argument("--finding-severity", action="append", choices=("P0", "P1", "P2", "P3"), default=[])
    write_parser.add_argument("--retry-target", choices=sorted(VALID_RETRY_TARGETS), default="")
    write_parser.add_argument("--checked-command", action="append", default=[])
    write_parser.add_argument("--checked-surface", action="append", default=[])
    write_parser.add_argument("--evidence-ref", action="append", default=[])
    write_parser.add_argument("--workflow-id", required=True)
    write_parser.add_argument("--candidate-id", default="")
    write_parser.add_argument("--active-candidate-id", default="")
    write_parser.add_argument("--queued-candidate-id", action="append", default=[])
    write_parser.add_argument("--queue-policy", choices=("human-decision", "auto-continuation"), default="")
    write_parser.add_argument("--affected-surface", action="append", default=[])
    write_parser.add_argument("--acceptance-criteria-id", action="append", default=[])
    write_parser.add_argument("--failure-mode-severity", choices=("P0", "P1", "P2", "P3"), default="P3")
    write_parser.add_argument("--failure-mode-mapped", action="store_true")
    write_parser.add_argument("--verification-mode", choices=("semantic", "mechanical"), default="semantic")
    write_parser.add_argument("--approval-ready", choices=("true", "false"), default="")
    write_parser.add_argument("--verification-passed", choices=("true", "false"), default="")
    write_parser.add_argument("--revision", type=int, default=1)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command != "write":
        raise SafeArtifactWriterError(f"unknown command: {args.command}")
    root = Path(args.root).resolve()
    project_dir = os.environ.get("CLAUDE_PROJECT_DIR")
    if project_dir and root != Path(project_dir).resolve():
        raise SafeArtifactWriterError("safe artifact writer --root must match CLAUDE_PROJECT_DIR")
    return write_artifact(
        root,
        kind=args.kind,
        status=args.status,
        summary=args.summary,
        blocking_findings=tuple(args.blocking_finding or ()),
        finding_ids=tuple(args.finding_id or ()),
        finding_surfaces=tuple(args.finding_surface or ()),
        finding_expected=tuple(args.finding_expected or ()),
        finding_observed=tuple(args.finding_observed or ()),
        finding_verdicts=tuple(args.finding_verdict or ()),
        finding_severities=tuple(args.finding_severity or ()),
        retry_target_value=args.retry_target or "",
        checked_commands=tuple(args.checked_command or ()),
        checked_surfaces=tuple(args.checked_surface or ()),
        evidence_refs=tuple(args.evidence_ref or ()),
        workflow_id=args.workflow_id,
        candidate_id=args.candidate_id,
        active_candidate_id=args.active_candidate_id,
        queued_candidate_ids=tuple(args.queued_candidate_id or ()),
        queue_policy=args.queue_policy or "",
        affected_surfaces=tuple(args.affected_surface or ()),
        acceptance_criteria_ids=tuple(args.acceptance_criteria_id or ()),
        failure_mode_severity=args.failure_mode_severity,
        failure_mode_mapped=bool(args.failure_mode_mapped),
        verification_mode=args.verification_mode,
        approval_ready={"true": True, "false": False}.get(args.approval_ready),
        verification_passed={"true": True, "false": False}.get(args.verification_passed),
        revision=args.revision,
    )


def main(argv: Sequence[str] | None = None) -> None:
    result = run(parse_args(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

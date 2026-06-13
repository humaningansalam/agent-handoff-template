from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path
from typing import Any, Sequence

from tools.agent_harness import paths as harness_paths
from tools.agent_harness.harness import MaintenanceHarness
from tools.agent_harness.pass_gate import worker_ready
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
    workflow_id: str,
    candidate_id: str = "",
    active_candidate_id: str = "",
    queued_candidate_ids: Sequence[str] = (),
    queue_policy: str = "",
    affected_surfaces: Sequence[str] = (),
    acceptance_criteria_ids: Sequence[str] = (),
    failure_mode_severity: str = "P3",
    failure_mode_mapped: bool = False,
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

    evidence = _structured_evidence(
        normalized_kind,
        worker=_worker_for_evidence(root, normalized_kind),
        status=status,
        summary=summary,
        blocking_findings=blocking_findings,
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
            workflow_id=workflow,
            candidate_id=candidate,
            affected_surfaces=affected_surfaces,
            acceptance_criteria_ids=acceptance_criteria_ids,
            failure_mode_severity=failure_mode_severity,
            failure_mode_mapped=failure_mode_mapped,
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
    return {
        "kind": normalized_kind,
        "latest_path": latest_path.as_posix(),
        "canonical_path": canonical,
        "workflow_id": workflow,
        "candidate_id": "" if normalized_kind == "cartography" and not candidate_id.strip() else candidate,
        "revision": revision,
        "sha256": sha256,
}


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
    if severity not in {"P0", "P1"} and surfaces and all(surface.endswith((".md", ".txt")) for surface in surfaces):
        return "host-verifier"
    return KIND_TO_WORKER[kind]


def _structured_evidence(kind: str, *, worker: str, status: str, summary: str, blocking_findings: Sequence[str]) -> dict[str, Any]:
    normalized_status = status.strip()
    if normalized_status not in VALID_EVIDENCE_STATUSES:
        raise SafeArtifactWriterError("evidence status must be passed or failed")
    normalized_summary = summary.strip()
    if not normalized_summary:
        raise SafeArtifactWriterError("evidence summary is required")
    blockers = [str(item).strip() for item in blocking_findings if str(item).strip()]
    return {
        "schema_version": 1,
        "worker": worker,
        "evidence_kind": "structured-json",
        "status": normalized_status,
        "blocking_findings": blockers,
        "summary": normalized_summary,
    }


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
    workflow_id: str,
    candidate_id: str,
    affected_surfaces: Sequence[str],
    acceptance_criteria_ids: Sequence[str],
    failure_mode_severity: str,
    failure_mode_mapped: bool,
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
    return {
        "schema_version": 1,
        "workflow_id": workflow_id,
        "candidate_id": candidate_id,
        "affected_surfaces": surfaces,
        "acceptance_criteria_ids": criteria,
        "failure_mode_severity": severity,
        "failure_mode_mapped": bool(failure_mode_mapped),
        "plan_sha256": plan_sha256,
    }


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
    write_parser.add_argument("--workflow-id", required=True)
    write_parser.add_argument("--candidate-id", default="")
    write_parser.add_argument("--active-candidate-id", default="")
    write_parser.add_argument("--queued-candidate-id", action="append", default=[])
    write_parser.add_argument("--queue-policy", choices=("human-decision", "auto-continuation"), default="")
    write_parser.add_argument("--affected-surface", action="append", default=[])
    write_parser.add_argument("--acceptance-criteria-id", action="append", default=[])
    write_parser.add_argument("--failure-mode-severity", choices=("P0", "P1", "P2", "P3"), default="P3")
    write_parser.add_argument("--failure-mode-mapped", action="store_true")
    write_parser.add_argument("--approval-ready", choices=("true", "false"), default="")
    write_parser.add_argument("--verification-passed", choices=("true", "false"), default="")
    write_parser.add_argument("--revision", type=int, default=1)
    return parser.parse_args(argv)


def run(args: argparse.Namespace) -> dict[str, Any]:
    if args.command != "write":
        raise SafeArtifactWriterError(f"unknown command: {args.command}")
    return write_artifact(
        Path(args.root),
        kind=args.kind,
        status=args.status,
        summary=args.summary,
        blocking_findings=tuple(args.blocking_finding or ()),
        workflow_id=args.workflow_id,
        candidate_id=args.candidate_id,
        active_candidate_id=args.active_candidate_id,
        queued_candidate_ids=tuple(args.queued_candidate_id or ()),
        queue_policy=args.queue_policy or "",
        affected_surfaces=tuple(args.affected_surface or ()),
        acceptance_criteria_ids=tuple(args.acceptance_criteria_id or ()),
        failure_mode_severity=args.failure_mode_severity,
        failure_mode_mapped=bool(args.failure_mode_mapped),
        approval_ready={"true": True, "false": False}.get(args.approval_ready),
        verification_passed={"true": True, "false": False}.get(args.verification_passed),
        revision=args.revision,
    )


def main(argv: Sequence[str] | None = None) -> None:
    result = run(parse_args(argv))
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

from __future__ import annotations

import hashlib
import subprocess
from pathlib import Path
from typing import Any

from tools.agent_harness import paths as harness_paths
from tools.agent_harness.harness import ApprovalRequired, ArtifactRecord, MaintenanceHarness, Phase
from tools.agent_harness.policy import APPROVAL_HASH_PREFIX_LENGTH, approval_phrase
from tools.hooks.maintenance.scope import active_marker_for_session, write_marker_record
from tools.runtime.json_io import read_json_object, write_json_atomic_under_root


STATE_PATH = harness_paths.STATE_JSON
PLAN_METADATA_PATH = harness_paths.PLAN_METADATA_JSON
APPROVAL_PREFIX = "승인:"


def is_explicit_approval_prompt(prompt: str) -> bool:
    return prompt.strip().startswith(APPROVAL_PREFIX)


def approval_context_for_prompt(root: Path, payload: dict[str, Any]) -> str:
    """Freeze reviewed maintenance approval from structured plan metadata."""
    prompt = str(payload.get("prompt") or "").strip()
    approval_intent = payload.get("maintenance_approval") is True or is_explicit_approval_prompt(prompt) or prompt.casefold() in {"승인", "approve", "approved", "yes", "go"}
    if not approval_intent:
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
    contract_hash = _metadata_plan_contract_hash(metadata)
    if not contract_hash:
        return "[MAINTENANCE_APPROVAL] Approval was not frozen: plan metadata must include plan contract hash."
    expected_phrase = _expected_approval_phrase(metadata, contract_hash)
    if prompt != expected_phrase:
        return (
            "[MAINTENANCE_APPROVAL] Approval was not frozen: exact approval phrase required. "
            f"Type `{expected_phrase}` to approve this plan contract."
        )
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
    contract_hash = _metadata_plan_contract_hash(metadata)
    if not contract_hash:
        raise ApprovalRequired("plan metadata must include plan contract hash")

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
        approved_plan_sha256=str(metadata.get("plan_body_sha256") or metadata.get("plan_sha256") or ""),
        approved_plan_contract_hash=contract_hash,
        approved_affected_surfaces=affected_surfaces,
        approved_acceptance_criteria_ids=acceptance_criteria_ids,
    )
    checkpoint = harness.state_checkpoint()
    approval_gate = checkpoint.get("approval_gate") if isinstance(checkpoint.get("approval_gate"), dict) else {}
    freeze = approval_gate.get("freeze") if isinstance(approval_gate.get("freeze"), dict) else {}
    checkpoint["approval_gate"] = {
        **approval_gate,
        "freeze": {
            **freeze,
            **_dirty_worktree_baseline(root),
        },
    }
    write_json_atomic_under_root(root / STATE_PATH, checkpoint, root)
    return checkpoint


def _dirty_worktree_baseline(root: Path) -> dict[str, Any]:
    dirty = _git_status_paths(root)
    head = _git_head(root)
    result: dict[str, Any] = {
        "pre_existing_dirty_files": dirty,
        "pre_existing_dirty_fingerprints": _dirty_fingerprints(root, dirty),
    }
    if head:
        result["approval_base_git_head"] = head
    return result


def _git_status_paths(root: Path) -> list[str]:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "status", "--porcelain", "--untracked-files=all"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except Exception:
        return []
    paths: list[str] = []
    for line in completed.stdout.splitlines():
        if not line.strip():
            continue
        raw = line[3:].strip() if len(line) > 3 else ""
        if " -> " in raw:
            raw = raw.rsplit(" -> ", 1)[1].strip()
        if raw and not raw.startswith("ops/agent-harness/") and raw not in paths:
            paths.append(raw)
    return paths


def _dirty_fingerprints(root: Path, paths: list[str]) -> dict[str, str]:
    fingerprints: dict[str, str] = {}
    for path in paths:
        target = root / path
        if not target.is_file() or target.is_symlink():
            fingerprints[path] = ""
            continue
        try:
            fingerprints[path] = hashlib.sha256(target.read_bytes()).hexdigest()
        except OSError:
            fingerprints[path] = ""
    return fingerprints


def _git_head(root: Path) -> str:
    try:
        completed = subprocess.run(
            ["git", "-C", str(root), "rev-parse", "HEAD"],
            check=False,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
            text=True,
            timeout=5,
        )
    except Exception:
        return ""
    return completed.stdout.strip() if completed.returncode == 0 else ""


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


def _expected_approval_phrase(metadata: dict[str, Any], contract_hash: str) -> str:
    candidate_id = str(metadata.get("candidate_id") or "").strip()
    return approval_phrase(candidate_id, contract_hash)


def _metadata_plan_contract_hash(metadata: dict[str, Any]) -> str:
    return str(metadata.get("plan_contract_hash") or "").strip()


def _phase(value: str) -> Phase:
    try:
        return Phase(value)
    except ValueError:
        return Phase.INTAKE

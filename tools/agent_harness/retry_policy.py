from __future__ import annotations

from pathlib import Path
from typing import Any

from tools.agent_harness.policy import WorkflowProfile, route_for_profile

RETRY_AGENT_PRIMARY = {
    "retry-plan": "maintenance-planner",
    "retry-plan-metadata": "maintenance-planner",
    "retry-approval-metadata": "maintenance-planner",
    "retry-implementation": "maintenance-implementer",
    "retry-artifact-metadata": "maintenance-implementer",
    "retry-evaluation": "maintenance-evaluator",
    "retry-scope-ledger": "maintenance-evaluator",
    "retry-verification-metadata": "maintenance-evaluator",
}

RETRY_ARTIFACT_PRIMARY = {
    "retry-plan": ("plan", "maintenance-planner"),
    "retry-plan-metadata": ("plan", "maintenance-planner"),
    "retry-approval-metadata": ("plan", "maintenance-planner"),
    "retry-implementation": ("execution", "maintenance-implementer"),
    "retry-artifact-metadata": ("execution", "maintenance-implementer"),
    "retry-evaluation": ("execution-review", "maintenance-evaluator"),
    "retry-scope-ledger": ("execution-review", "maintenance-evaluator"),
    "retry-verification-metadata": ("execution-review", "maintenance-evaluator"),
}

RETRY_ARTIFACT_REVIEW = {
    "maintenance-plan-critic": "plan-review",
    "maintenance-evaluator": "execution-review",
    "maintenance-skeptic": "skeptic-review",
}

REVIEW_WORKERS = {"maintenance-plan-critic", "maintenance-evaluator", "maintenance-skeptic"}


def retry_target(state: dict[str, Any]) -> str:
    retry = state.get("retry") if isinstance(state.get("retry"), dict) else {}
    target = str(retry.get("target") or "").strip()
    return target if target.startswith("retry-") else ""


def retry_decision_block_reason(state: dict[str, Any], decision: str) -> str:
    target = retry_target(state)
    if not target or decision not in {"pass", "awaiting-human-approval", "needs-human-decision", "stop", "fail"}:
        return ""
    agent = _primary_worker_for_retry(state, target) or "the required retry phase agent"
    return f"maintenance final report가 {target} 상태에서 {decision}을 주장했습니다. user-facing decision으로 닫지 말고 {agent}부터 계속 실행하세요."


def retry_agent_start_block_reason(root: Path, state: dict[str, Any], agent_type: str) -> str:
    target = retry_target(state)
    if not target:
        return ""
    primary = _primary_worker_for_retry(state, target)
    if agent_type == primary:
        return ""
    route = _route_for_state(state)
    review_worker = _review_worker_after(route, primary)
    if review_worker and agent_type == review_worker:
        if primary and worker_ready(state, primary):
            return ""
    allowed = [item for item in (primary, review_worker) if item]
    return f"maintenance retry route {target} allows next worker {' or '.join(allowed)}; blocked {agent_type}."


def retry_artifact_write_block_reason(root: Path, state: dict[str, Any], kind: str) -> str:
    target = retry_target(state)
    if not target:
        return ""
    primary_worker = _primary_worker_for_retry(state, target)
    primary_kind = _artifact_kind_for_retry(state, target)
    if primary_kind and kind == primary_kind:
        required_worker = primary_worker or "host-verifier"
        if worker_ready(state, required_worker):
            return ""
        return f"maintenance retry route {target} requires {required_worker} before writing {kind}."
    route = _route_for_state(state)
    review_worker = _review_worker_after(route, primary_worker)
    review_kind = RETRY_ARTIFACT_REVIEW.get(review_worker or "")
    if review_kind and kind == review_kind:
        if primary_worker and worker_ready(state, primary_worker):
            return ""
        return f"maintenance retry route {target} requires {primary_worker} before writing {kind}."
    allowed = [item for item in (primary_kind, review_kind) if item]
    return f"maintenance retry route {target} allows artifact {' or '.join(allowed)}; blocked {kind}."


def _primary_worker_for_retry(state: dict[str, Any], target: str) -> str:
    route = _route_for_state(state)
    primary = RETRY_AGENT_PRIMARY.get(target, "")
    return primary if primary in route else ""


def _artifact_kind_for_retry(state: dict[str, Any], target: str) -> str:
    primary = _primary_worker_for_retry(state, target)
    if target == "retry-evaluation" and not primary and "host-verifier" in _route_for_state(state):
        return "execution-review"
    rule = RETRY_ARTIFACT_PRIMARY.get(target)
    if not rule:
        return ""
    kind, required_worker = rule
    return kind if required_worker == primary else ""


def _route_for_state(state: dict[str, Any]) -> tuple[str, ...]:
    pass_eligibility = state.get("pass_eligibility") if isinstance(state.get("pass_eligibility"), dict) else {}
    workflow_profile = pass_eligibility.get("workflow_profile") if isinstance(pass_eligibility.get("workflow_profile"), dict) else {}
    route = tuple(str(worker) for worker in workflow_profile.get("route", []) if str(worker).strip()) if isinstance(workflow_profile.get("route"), list) else ()
    if route:
        return route
    profile = _workflow_path_for_state(state)
    try:
        return route_for_profile(profile)
    except ValueError:
        return route_for_profile(WorkflowProfile.STANDARD)


def _workflow_path_for_state(state: dict[str, Any]) -> str:
    pass_eligibility = state.get("pass_eligibility") if isinstance(state.get("pass_eligibility"), dict) else {}
    calculated = pass_eligibility.get("calculated") if isinstance(pass_eligibility.get("calculated"), dict) else {}
    return str(calculated.get("workflow_path") or pass_eligibility.get("workflow_path") or "STANDARD")


def _review_worker_after(route: tuple[str, ...], worker: str) -> str:
    if not worker or worker not in route:
        return ""
    for candidate in route[route.index(worker) + 1 :]:
        if candidate in REVIEW_WORKERS:
            return candidate
    return ""


def worker_ready(state: dict[str, Any], worker: str) -> bool:
    worker_status = state.get("worker_status") if isinstance(state.get("worker_status"), dict) else {}
    row = worker_status.get(worker) if isinstance(worker_status.get(worker), dict) else {}
    return bool(
        row.get("invoked") is True
        and row.get("worker")
        and row.get("evidence_kind")
        and row.get("status") == "passed"
        and not row.get("blocking_findings")
        and (row.get("artifact_path") or row.get("schema_version"))
        and row.get("structured_evidence_valid") is True
    )

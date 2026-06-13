from __future__ import annotations

from pathlib import Path
from typing import Any

RETRY_AGENT_PRIMARY = {
    "retry-plan": "maintenance-planner",
    "retry-implementation": "maintenance-implementer",
    "retry-evaluation": "maintenance-evaluator",
}

RETRY_AGENT_REVIEW = {
    "retry-plan": ("maintenance-plan-critic", "maintenance-planner", "maintenance-plan-critic"),
    "retry-implementation": ("maintenance-evaluator", "maintenance-implementer", "maintenance-evaluator"),
    "retry-evaluation": ("maintenance-skeptic", "maintenance-evaluator", "maintenance-skeptic"),
}

RETRY_ARTIFACT_PRIMARY = {
    "retry-plan": ("plan", "maintenance-planner", "maintenance-plan-critic"),
    "retry-implementation": ("execution", "maintenance-implementer", "maintenance-evaluator"),
    "retry-evaluation": ("execution-review", "maintenance-evaluator", "maintenance-skeptic"),
}

RETRY_ARTIFACT_REVIEW = {
    "retry-plan": ("plan-review", "maintenance-plan-critic", "maintenance-planner"),
    "retry-implementation": ("execution-review", "maintenance-evaluator", "maintenance-implementer"),
    "retry-evaluation": ("skeptic-review", "maintenance-skeptic", "maintenance-evaluator"),
}


def retry_target(state: dict[str, Any]) -> str:
    retry = state.get("retry") if isinstance(state.get("retry"), dict) else {}
    target = str(retry.get("target") or "").strip()
    return target if target.startswith("retry-") else ""


def retry_decision_block_reason(state: dict[str, Any], decision: str) -> str:
    target = retry_target(state)
    if not target or decision not in {"pass", "awaiting-human-approval", "needs-human-decision", "stop", "fail"}:
        return ""
    agent = RETRY_AGENT_PRIMARY.get(target, "the required retry phase agent")
    return f"maintenance final report가 {target} 상태에서 {decision}을 주장했습니다. user-facing decision으로 닫지 말고 {agent}부터 계속 실행하세요."


def retry_agent_start_block_reason(root: Path, state: dict[str, Any], agent_type: str) -> str:
    target = retry_target(state)
    if not target:
        return ""
    primary = RETRY_AGENT_PRIMARY.get(target)
    if agent_type == primary:
        return ""
    review_rule = RETRY_AGENT_REVIEW.get(target)
    if review_rule and agent_type == review_rule[0]:
        _, required_worker, _ = review_rule
        if worker_ready(state, required_worker):
            return ""
    allowed = [item for item in (primary, review_rule[0] if review_rule else "") if item]
    return f"maintenance retry route {target} allows next worker {' or '.join(allowed)}; blocked {agent_type}."


def retry_artifact_write_block_reason(root: Path, state: dict[str, Any], kind: str) -> str:
    target = retry_target(state)
    if not target:
        return ""
    primary_rule = RETRY_ARTIFACT_PRIMARY.get(target)
    if primary_rule and kind == primary_rule[0]:
        _, required_worker, after_worker = primary_rule
        if worker_ready(state, required_worker):
            return ""
        return f"maintenance retry route {target} requires {required_worker} before writing {kind}."
    review_rule = RETRY_ARTIFACT_REVIEW.get(target)
    if review_rule and kind == review_rule[0]:
        _, required_worker, after_worker = review_rule
        if worker_ready(state, required_worker):
            return ""
        return f"maintenance retry route {target} requires {required_worker} before writing {kind}."
    allowed = [rule[0] for rule in (primary_rule, review_rule) if rule]
    return f"maintenance retry route {target} allows artifact {' or '.join(allowed)}; blocked {kind}."


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

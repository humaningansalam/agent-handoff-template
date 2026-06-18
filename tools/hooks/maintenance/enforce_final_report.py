from __future__ import annotations

import json
import sys
from pathlib import Path
from typing import Any

from tools.hooks.hook_failures import emit_block, record_hook_failure
from tools.agent_harness.pass_gate import worker_ready
from tools.agent_harness.retry_policy import retry_decision_block_reason
from tools.hooks.maintenance.trace import reconcile_current_state
from tools.hooks.maintenance.scope import active_marker_for_session, clear_marker, workspace_root
from tools.runtime.json_io import read_json_object, write_json_atomic_under_root


STATE_PATH = Path("ops/agent-harness/current-run-state.json")
ARTIFACT_ROOT = Path("ops/agent-harness")
WORKER_ARTIFACTS = {
    "maintenance-cartographer": "evidence/cartography.json",
    "maintenance-planner": "evidence/plan.json",
    "maintenance-plan-critic": "evidence/plan-review.json",
    "maintenance-implementer": "evidence/execution.json",
    "maintenance-evaluator": "evidence/execution-review.json",
    "maintenance-skeptic": "evidence/skeptic-review.json",
}


def _last_assistant_message(payload: dict[str, Any]) -> str:
    for key in ("last_assistant_message", "message"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value
    return ""


def _state(root: Path) -> dict[str, Any]:
    state = read_json_object(root / STATE_PATH, missing_ok=True)
    if state:
        try:
            return reconcile_current_state(root)
        except ValueError:
            return state
    return state


def final_report_block_reason(root: Path, payload: dict[str, Any]) -> str | None:
    session_id = str(payload.get("session_id") or "").strip()
    state = _state(root)
    marker = active_marker_for_session(root, session_id)
    if not marker and not _active_state_requires_final_gate(state):
        return None

    message = _last_assistant_message(payload)
    if not message.strip():
        return "maintenance final report는 final assistant message를 읽을 수 없어서 차단되었습니다."

    first_line = _first_decision_line(message)

    missing_worker_artifact = _missing_structured_worker_artifact(root, state)
    if missing_worker_artifact:
        return missing_worker_artifact
    phase = str(state.get("phase") or "")
    pass_eligibility = state.get("pass_eligibility") if isinstance(state.get("pass_eligibility"), dict) else {}
    calculated = pass_eligibility.get("calculated") if isinstance(pass_eligibility.get("calculated"), dict) else {}

    next_action = _next_action_hint(state)
    if not first_line:
        return (
            "maintenance final report는 첫 줄에 user-facing decision(pass | awaiting-human-approval | "
            "needs-human-decision | stop | fail)이 필요합니다. " + next_action
        )
    retry_block = retry_decision_block_reason(state, first_line)
    if retry_block:
        return retry_block
    if first_line == "pass" and calculated.get("eligible") is not True:
        return "maintenance final report가 pass를 주장하지만 runner pass_eligibility가 false라 차단되었습니다. " + next_action
    if first_line == "awaiting-human-approval" and phase != "awaiting_human_approval":
        return "maintenance final report가 awaiting-human-approval을 주장하지만 runner state phase가 일치하지 않아 차단되었습니다."
    if first_line == "fail" and phase not in {"failed", "skeptic_reviewed"}:
        return "maintenance final report가 중간 phase에서 fail을 주장해 차단되었습니다. " + next_action
    if first_line == "stop" and _only_tests_not_passed_blocker(calculated):
        return (
            "maintenance final report가 tests_not_passed만 남은 상태에서 stop을 주장했습니다. "
            "targeted verification evidence를 확인한 뒤 safe writer로 execution-review metadata "
            "`--verification-passed true|false`를 기록하고 checker state를 갱신하세요."
        )
    if first_line == "stop" and phase not in {"decided", "skeptic_reviewed"}:
        return "maintenance final report가 중간 phase에서 stop을 주장해 차단되었습니다. " + next_action
    return None


def _next_action_hint(state: dict[str, Any]) -> str:
    pass_eligibility = state.get("pass_eligibility") if isinstance(state.get("pass_eligibility"), dict) else {}
    calculated = pass_eligibility.get("calculated") if isinstance(pass_eligibility.get("calculated"), dict) else {}
    if calculated.get("eligible") is True:
        return "checker is pass-eligible; emit final first line `pass` without calling more workers."
    cursor = pass_eligibility.get("route_cursor") if isinstance(pass_eligibility.get("route_cursor"), dict) else {}
    next_worker = str(cursor.get("next_required_worker") or "").strip()
    remaining = cursor.get("remaining_required_artifacts") if isinstance(cursor.get("remaining_required_artifacts"), list) else []
    next_artifact = str(remaining[0]) if remaining else ""
    changed_files = state.get("changed_files") if isinstance(state.get("changed_files"), list) else []
    if next_worker == "maintenance-implementer" and next_artifact.endswith("evidence/execution.json") and changed_files:
        return (
            "Approved implementation changes are already recorded; do not call maintenance-implementer again. "
            "Persist `--kind execution --status passed` evidence only with "
            "`uv run python -m tools.agent_harness.safe_artifact_writer write ...`; "
            "do not use direct `python`, script paths, `PYTHONPATH=...`, `rg`, or `git` as evidence commands."
        )
    if next_worker:
        artifact_hint = f" and produce `{next_artifact}`" if next_artifact else ""
        return (
            f"Continue with exactly `{next_worker}`{artifact_hint}; do not rerun completed workers or call route-outside agents. "
            "Persist evidence only with `uv run python -m tools.agent_harness.safe_artifact_writer write ...`; "
            "do not use direct `python`, script paths, `PYTHONPATH=...`, `rg`, or `git` as evidence commands."
        )
    blockers = calculated.get("blocked_by") if isinstance(calculated.get("blocked_by"), list) else []
    if blockers:
        return "Resolve checker blockers before final response: " + ", ".join(str(blocker) for blocker in blockers)
    return "Continue via the checker route cursor and structured safe-writer evidence before final response."


def _active_state_requires_final_gate(state: dict[str, Any]) -> bool:
    workflow_id = str(state.get("workflow_id") or "")
    phase = str(state.get("phase") or "")
    return bool(workflow_id.startswith("mw-") and phase and phase not in {"decided", "failed", "stopped"})


def _missing_structured_worker_artifact(root: Path, state: dict[str, Any]) -> str:
    worker_status = state.get("worker_status") if isinstance(state.get("worker_status"), dict) else {}
    for worker, artifact_name in WORKER_ARTIFACTS.items():
        row = worker_status.get(worker) if isinstance(worker_status.get(worker), dict) else {}
        if not worker_ready(row):
            continue
        artifact = root / ARTIFACT_ROOT / artifact_name
        if artifact.is_file() and not artifact.is_symlink():
            continue
        if worker == "maintenance-plan-critic":
            return (
                "maintenance-plan-critic has structured evidence but evidence/plan-review.json is missing. "
                "Do not answer the user or rerun the critic; write plan-review evidence with structured approval-ready metadata."
            )
        return f"{worker} has structured evidence but {artifact_name} is missing. Write the required maintenance evidence artifact before final response."
    return ""


def _first_decision_line(message: str) -> str:
    first_line = message.strip().splitlines()[0].strip().lower() if message.strip() else ""
    return first_line if first_line in {"pass", "awaiting-human-approval", "needs-human-decision", "stop", "fail"} else ""


def _only_tests_not_passed_blocker(calculated: dict[str, Any]) -> bool:
    blockers = calculated.get("blocked_by") if isinstance(calculated, dict) else []
    return isinstance(blockers, list) and [str(item) for item in blockers] == ["tests_not_passed"]


def should_clear_active_marker(payload: dict[str, Any]) -> bool:
    return _first_decision_line(_last_assistant_message(payload)) in {"pass", "fail"}


def _record_final_decision(root: Path, payload: dict[str, Any]) -> None:
    decision = _first_decision_line(_last_assistant_message(payload))
    if decision not in {"pass", "fail"}:
        return
    state_path = root / STATE_PATH
    state = read_json_object(state_path, missing_ok=True)
    if not state:
        return
    state["phase"] = "decided"
    state["decision"] = decision
    write_json_atomic_under_root(state_path, state, root)


def main() -> None:
    root = workspace_root()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except Exception as exc:
        record_hook_failure(root, hook="enforce_maintenance_final_report", exc=exc)
        emit_block("Maintenance final report enforcement failed while reading Stop hook payload.")
        return
    try:
        reason = final_report_block_reason(root, payload)
        if reason:
            emit_block(reason)
            return
        _record_final_decision(root, payload)
        session_id = str(payload.get("session_id") or "").strip()
        if session_id and should_clear_active_marker(payload):
            clear_marker(root, session_id)
    except Exception as exc:
        record_hook_failure(root, hook="enforce_maintenance_final_report", exc=exc, payload=payload)
        emit_block("Maintenance final report enforcement failed; refusing to close maintenance workflow.")


if __name__ == "__main__":
    main()

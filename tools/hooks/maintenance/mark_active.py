from __future__ import annotations

import json
import sys
from pathlib import Path

from tools.hooks.hook_failures import record_hook_failure
from tools.hooks.maintenance.scope import active_marker_for_session, clear_all_markers, is_maintenance_prompt, workspace_root, write_marker, write_marker_record
from tools.hooks.maintenance.prompt_approval import is_explicit_approval_prompt
from tools.hooks.maintenance.trace import initialize_trace, record_event
from tools.runtime.json_io import read_json_object


STATE_PATH = Path("ops/agent-harness/current-run-state.json")


def _pending_approval_state(root):
    state = read_json_object(root / STATE_PATH, missing_ok=True)
    approval_gate = state.get("approval_gate") if isinstance(state.get("approval_gate"), dict) else {}
    if state.get("phase") == "awaiting_human_approval" and approval_gate.get("status") == "awaiting-human-approval":
        return state
    return {}


def _resumable_state(root):
    state = read_json_object(root / STATE_PATH, missing_ok=True)
    phase = str(state.get("phase") or "")
    workflow_id = str(state.get("workflow_id") or "").strip()
    if workflow_id and phase and phase not in {"decided", "failed", "stopped"}:
        return state
    return {}


def _should_resume_pending_approval(payload: dict, state: dict) -> bool:
    approval_gate = state.get("approval_gate") if isinstance(state.get("approval_gate"), dict) else {}
    return bool(
        state
        and state.get("phase") == "awaiting_human_approval"
        and approval_gate.get("status") == "awaiting-human-approval"
        and payload.get("maintenance_approval") is True
    )


def main() -> None:
    root = workspace_root()
    try:
        payload = json.loads(sys.stdin.read() or "{}")
        if not isinstance(payload, dict):
            return
        prompt = str(payload.get("prompt") or "")
        session_id = str(payload.get("session_id") or "default")
        if is_maintenance_prompt(prompt):
            if active_marker_for_session(root, session_id):
                return
            clear_all_markers(root)
            marker = write_marker(root, payload, prompt=prompt)
            initialize_trace(root, marker, payload)
            return
        if active_marker_for_session(root, session_id):
            return
        approval_or_continue = payload.get("maintenance_approval") is True or payload.get("maintenance_continue") is True or is_explicit_approval_prompt(prompt)
        state = _pending_approval_state(root)
        if not state and approval_or_continue:
            existing = _resumable_state(root)
            workflow_id = str(existing.get("workflow_id") or "").strip()
            if workflow_id:
                marker = {"workflow_id": workflow_id, "session_id": session_id, "operation": "maintenance-workflow"}
                record_event(root, marker, payload, event="workflow-resume", phase=str(existing.get("phase") or "running"), result="maintenance continuation resume probe")
                state = _pending_approval_state(root)
                if not state:
                    state = existing
        if _should_resume_pending_approval(payload, state):
            clear_all_markers(root)
            marker = write_marker_record(
                root,
                session_id=session_id,
                workflow_id=str(state.get("workflow_id") or f"mw-{session_id}"),
                prompt=prompt,
            )
            record_event(root, marker, payload, event="workflow-resume", phase="awaiting_human_approval", result="pending approval resumed")
        elif approval_or_continue and state:
            clear_all_markers(root)
            marker = write_marker_record(
                root,
                session_id=session_id,
                workflow_id=str(state.get("workflow_id") or f"mw-{session_id}"),
                prompt=prompt,
            )
            record_event(root, marker, payload, event="workflow-resume", phase=str(state.get("phase") or "running"), result="maintenance continuation resumed")
    except Exception as exc:  # pragma: no cover - defensive hook boundary
        record_hook_failure(root, hook="mark_maintenance_active", exc=exc)


if __name__ == "__main__":
    main()

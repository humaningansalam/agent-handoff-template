from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.hooks.hook_failures import emit_block, record_hook_failure
from tools.hooks.maintenance.trace import maintenance_agent, record_active_event
from tools.hooks.subagent_transcript import AGENT_OPERATIONS, base_event, trace_dir
from tools.agent_harness.retry_policy import retry_agent_start_block_reason
from tools.runtime.json_io import read_json_object, write_json_atomic_under_root, write_text_atomic_under_root


ARTIFACT_ROOT = Path("ops/agent-harness")
STATE_PATH = ARTIFACT_ROOT / "current-run-state.json"
PLAN_REVIEW_METADATA_PATH = ARTIFACT_ROOT / "latest-plan-review-metadata.json"


def workspace_root() -> Path:
    env_root = os.environ.get("WORKSPACE_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[2]


def main() -> None:
    root = workspace_root()
    try:
        payload = json.load(sys.stdin)
    except Exception as exc:
        record_hook_failure(root, hook="capture_subagent_start", exc=exc)
        return
    try:
        capture_start(payload, root)
    except Exception as exc:
        record_hook_failure(root, hook="capture_subagent_start", exc=exc, payload=payload)
        emit_block("SubagentStart trace marker capture failed; refusing silent unaudited subagent start.")


def capture_start(payload: dict[str, Any], root: Path) -> dict[str, Any] | None:
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "")
    if maintenance_agent(agent_type):
        state = read_json_object(root / STATE_PATH, missing_ok=True)
        route_block = retry_agent_start_block_reason(root, state, agent_type)
        if route_block:
            emit_block(route_block)
            return None
        repeat_block = _maintenance_worker_start_block_reason(root, state, agent_type)
        if repeat_block:
            emit_block(repeat_block)
            return None
        record_active_event(root, payload, event="worker-start", phase="worker", result="started")
    operation = AGENT_OPERATIONS.get(agent_type)
    if operation is None:
        return None

    trace_path = _trace_dir(root, operation)
    _reset_trace_for_new_subagent_run(trace_path, root)
    scope, scope_resolution = "latest", "operation-latest"
    marker = {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "operation": operation,
        "scope": scope,
        "agent_type": agent_type,
        "agent_id": str(payload.get("agent_id") or ""),
        "session_id": str(payload.get("session_id") or ""),
        "scope_resolution": scope_resolution,
    }
    metadata = {
        **marker,
        "trace_capture_status": "started",
        "canonical_transcript": "subagent-transcript.jsonl",
        "last_assistant_message_file": "last-assistant-message.md",
    }
    start_event = base_event(payload, operation=operation, event="subagent_start")
    write_json_atomic_under_root(trace_path / "start.json", marker, root)
    write_json_atomic_under_root(trace_path / "metadata.json", metadata, root)
    write_text_atomic_under_root(
        trace_path / "subagent-transcript.jsonl",
        json.dumps(start_event, ensure_ascii=False, separators=(",", ":")) + "\n",
        root,
    )
    return marker


def _reset_trace_for_new_subagent_run(trace_dir: Path, root: Path) -> None:
    for name in (
        "metadata.json",
        "subagent-transcript.jsonl",
        "last-assistant-message.md",
    ):
        path = trace_dir / name
        if path.exists() or path.is_symlink():
            if path.is_symlink():
                raise RuntimeError(f"trace file is symlink: {path}")
            path.relative_to(root)
            path.unlink()


def _trace_dir(root: Path, operation: str) -> Path:
    return trace_dir(root, operation)


def _maintenance_worker_start_block_reason(root: Path, state: dict[str, Any], agent_type: str) -> str:
    if agent_type != "maintenance-plan-critic":
        return ""
    worker_status = state.get("worker_status") if isinstance(state.get("worker_status"), dict) else {}
    critic = worker_status.get("maintenance-plan-critic") if isinstance(worker_status.get("maintenance-plan-critic"), dict) else {}
    critic_completed = bool(
        critic.get("invoked") is True
        and critic.get("worker")
        and critic.get("evidence_kind")
        and critic.get("status") == "passed"
        and not critic.get("blocking_findings")
        and (critic.get("artifact_path") or critic.get("schema_version"))
        and critic.get("structured_evidence_valid") is True
    )
    if not critic_completed:
        return ""
    metadata = read_json_object(root / PLAN_REVIEW_METADATA_PATH, missing_ok=True)
    approval_ready = metadata.get("approval_ready") if isinstance(metadata, dict) else None
    if approval_ready is False:
        return ""
    if approval_ready is True:
        return "maintenance-plan-critic already produced approval-ready review; continue to awaiting-human-approval instead of rerunning critic."
    return "maintenance-plan-critic already completed; write structured plan-review metadata before rerunning or replanning."


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
import os
import sys
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.hooks.hook_failures import record_hook_failure
from tools.agent_harness import paths as harness_paths
from tools.hooks.maintenance.trace import maintenance_agent, record_active_event
from tools.hooks.subagent_transcript import AGENT_OPERATIONS, append_event, base_event, redact_text, trace_dir as transcript_trace_dir
from tools.runtime.json_io import JsonIoError, write_json_atomic_under_root, write_text_atomic_under_root

KNOWN_TRACE_FILES = (
    "metadata.json",
    "last-assistant-message.md",
)
MAX_MESSAGE_CHARS = 20_000
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
        record_hook_failure(root, hook="capture_subagent_trace", exc=exc)
        return

    try:
        capture_trace(payload, root)
    except Exception as exc:
        record_hook_failure(root, hook="capture_subagent_trace", exc=exc, payload=payload)


def capture_trace(payload: dict[str, Any], root: Path) -> dict[str, Any] | None:
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "")
    message = _last_message(payload)
    if maintenance_agent(agent_type):
        if not _has_open_worker_start(root, agent_type):
            return None
        record_active_event(root, payload, event="worker-end", phase="worker", result="ended")
    operation = AGENT_OPERATIONS.get(agent_type)
    if operation is None:
        return None

    trace_dir = transcript_trace_dir(root, operation)
    _clear_known_trace_files(trace_dir, root)

    write_text_atomic_under_root(trace_dir / "last-assistant-message.md", message, root)
    append_event(root, operation, base_event(payload, operation=operation, event="final_message") | {"message": message})
    append_event(root, operation, base_event(payload, operation=operation, event="subagent_stop") | {"trace_capture_status": "completed"})

    metadata = {
        "schema_version": 1,
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "operation": operation,
        "scope": "latest",
        "scope_resolution": "operation-latest",
        "agent_type": agent_type,
        "agent_id": str(payload.get("agent_id") or ""),
        "session_id": str(payload.get("session_id") or ""),
        "cwd": str(payload.get("cwd") or ""),
        "last_assistant_message_chars": len(str(payload.get("last_assistant_message") or "")),
        "canonical_transcript": "subagent-transcript.jsonl",
        "trace_capture_status": "completed",
    }
    write_json_atomic_under_root(trace_dir / "metadata.json", metadata, root)
    return metadata


def _has_open_worker_start(root: Path, agent_type: str) -> bool:
    for event in reversed(read_events(root / harness_paths.EVENTS_JSONL)):
        if event.get("agent_type") != agent_type:
            continue
        if event.get("event") == "worker-start" and event.get("result") == "started":
            return True
        if event.get("event") in {"worker-end", "agent-deny"}:
            return False
    return False


def read_events(path: Path) -> tuple[dict[str, Any], ...]:
    if not path.is_file() or path.is_symlink():
        return ()
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            events.append(loaded)
    return tuple(events)


def _last_message(payload: dict[str, Any]) -> str:
    message = redact_text(str(payload.get("last_assistant_message") or ""))
    if len(message) <= MAX_MESSAGE_CHARS:
        return message
    return message[-MAX_MESSAGE_CHARS:]


def _clear_known_trace_files(trace_dir: Path, root: Path) -> None:
    for name in KNOWN_TRACE_FILES:
        target = trace_dir / name
        if target.is_symlink():
            raise JsonIoError(f"trace target is a symlink: {target}")
        if target.exists():
            target.relative_to(root)
            target.unlink()


if __name__ == "__main__":
    main()

from __future__ import annotations

import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.runtime.json_io import append_jsonl_atomic_under_root

AGENT_OPERATIONS: dict[str, str] = {}
SECRET_KEY_VALUE_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|authorization)\b\s*[:=]\s*([^\s,;'\"<>]+)"
)
BEARER_TOKEN_RE = re.compile(r"(?i)\bBearer\s+[A-Za-z0-9._~+/=-]{8,}")
MAX_STRING_CHARS = 20_000
MAX_TRACE_BYTES = 1024 * 1024


def now_iso() -> str:
    return datetime.now(UTC).isoformat().replace("+00:00", "Z")


def operation_for_payload(payload: dict[str, Any]) -> tuple[str, str]:
    agent_type = str(payload.get("agent_type") or payload.get("subagent_type") or "").strip()
    return agent_type, AGENT_OPERATIONS.get(agent_type, "")


def trace_dir(root: Path, operation: str) -> Path:
    return root / "ops" / "agent-harness" / "subagent-traces" / operation / "latest-trace"


def transcript_path(root: Path, operation: str) -> Path:
    return trace_dir(root, operation) / "subagent-transcript.jsonl"


def redact_text(text: str) -> str:
    text = BEARER_TOKEN_RE.sub("Bearer [REDACTED]", text)
    return SECRET_KEY_VALUE_RE.sub(lambda match: f"{match.group(1)}=[REDACTED]", text)


def redact_payload(value: Any) -> Any:
    if isinstance(value, str):
        text = redact_text(value)
        if len(text) <= MAX_STRING_CHARS:
            return text
        return text[:MAX_STRING_CHARS] + "\n...[truncated]"
    if isinstance(value, list):
        return [redact_payload(item) for item in value]
    if isinstance(value, dict):
        redacted: dict[str, Any] = {}
        for key, item in value.items():
            key_text = str(key)
            if _is_secret_key(key_text) and item is not None:
                redacted[key_text] = "[REDACTED]"
            else:
                redacted[key_text] = redact_payload(item)
        return redacted
    return value


def _is_secret_key(key: str) -> bool:
    return bool(re.search(r"(?i)(api[_-]?key|token|secret|password|authorization)", key))


def append_event(root: Path, operation: str, event: dict[str, Any]) -> None:
    append_jsonl_atomic_under_root(transcript_path(root, operation), event, root, max_bytes=MAX_TRACE_BYTES)


def payload_summary(payload: dict[str, Any]) -> dict[str, str]:
    summary = {
        "hook_event_name": str(payload.get("hook_event_name") or payload.get("hookEventName") or ""),
        "cwd": str(payload.get("cwd") or ""),
        "permission_mode": str(payload.get("permission_mode") or ""),
    }
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    command = str(tool_input.get("command") or "")
    if command:
        summary["command"] = redact_text(command)[:500]
    return {key: value for key, value in summary.items() if value}


def base_event(payload: dict[str, Any], *, operation: str, event: str) -> dict[str, Any]:
    return {
        "event": event,
        "captured_at": now_iso(),
        "operation": operation,
        "agent_type": str(payload.get("agent_type") or payload.get("subagent_type") or ""),
        "agent_id": str(payload.get("agent_id") or ""),
        "session_id": str(payload.get("session_id") or ""),
        "tool_name": str(payload.get("tool_name") or ""),
        "payload_summary": payload_summary(payload),
    }

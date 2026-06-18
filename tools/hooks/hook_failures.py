from __future__ import annotations

import json
import traceback
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

def record_hook_failure(root: Path, *, hook: str, exc: BaseException, payload: dict[str, Any] | None = None) -> None:
    failure_id = datetime.now(UTC).strftime("%Y%m%dT%H%M%S%fZ")
    data = {
        "schema_version": 1,
        "hook": hook,
        "captured_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "error_type": type(exc).__name__,
        "error": str(exc),
        "traceback": traceback.format_exception_only(type(exc), exc),
    }
    if payload is not None:
        data["hook_event_name"] = str(payload.get("hook_event_name") or "")
        data["session_id"] = str(payload.get("session_id") or "")
        data["agent_type"] = str(payload.get("agent_type") or payload.get("subagent_type") or "")
    from tools.runtime.json_io import write_json_atomic_under_root

    agent_type = str(data.get("agent_type") or "")
    if "maintenance" in hook or agent_type.startswith("maintenance-"):
        failure_root = root / "ops" / "agent-harness" / "hook-failures"
    else:
        failure_root = root / "ops" / "agent-harness" / "hook-failures"
    write_json_atomic_under_root(failure_root / f"{hook}-{failure_id}.json", data, root)


def emit_block(reason: str) -> None:
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))


def hook_debug_enabled() -> bool:
    import os

    return os.environ.get("AGENT_HARNESS_HOOK_DEBUG") == "1"


def emit_hook_debug(message: str) -> None:
    import sys

    if hook_debug_enabled():
        print(f"Hook Error: {message}", file=sys.stderr)

from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from typing import Any

from tools.hooks.hook_failures import record_hook_failure
from tools.hooks.maintenance.trace import record_active_event
from tools.hooks.subagent_transcript import append_event, base_event, operation_for_payload

HOOK_EVENTS = {"PreToolUse": "pre_tool", "PostToolUse": "post_tool"}


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
        record_hook_failure(root, hook="capture_subagent_tool_event", exc=exc)
        return
    try:
        capture_tool_event(payload, root)
    except Exception as exc:
        record_hook_failure(root, hook="capture_subagent_tool_event", exc=exc, payload=payload)


def capture_tool_event(payload: dict[str, Any], root: Path) -> None:
    event = HOOK_EVENTS.get(str(payload.get("hook_event_name") or ""))
    if not event:
        return
    record_active_event(root, payload, event=event, phase="tool-event")
    _, operation = operation_for_payload(payload)
    if not operation:
        return
    append_event(root, operation, base_event(payload, operation=operation, event=event))


if __name__ == "__main__":
    main()

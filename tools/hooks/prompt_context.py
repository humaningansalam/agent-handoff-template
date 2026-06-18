from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from tools.hooks.hook_failures import emit_hook_debug


def workspace_root() -> Path:
    env_root = os.environ.get("WORKSPACE_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[2]


def main() -> None:
    root = workspace_root()
    try:
        payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    output = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit"}}
    try:
        from tools.hooks.maintenance.prompt_approval import approval_context_for_prompt

        context = approval_context_for_prompt(root, payload)
        if context:
            output["hookSpecificOutput"]["additionalContext"] = context
    except Exception as exc:
        emit_hook_debug(f"Maintenance approval hook failed closed: {exc}")

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()

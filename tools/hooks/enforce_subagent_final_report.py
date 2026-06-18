from __future__ import annotations

import json
import os
import sys
from pathlib import Path

from tools.hooks.hook_failures import emit_block, record_hook_failure
from tools.hooks.final_report_enforcement import final_report_block_reason


def workspace_root() -> Path:
    env_root = os.environ.get("WORKSPACE_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[2]


def main() -> None:
    root = workspace_root()
    try:
        payload = json.load(sys.stdin)
        if not isinstance(payload, dict):
            payload = {}
    except Exception as exc:
        record_hook_failure(root, hook="enforce_subagent_final_report", exc=exc)
        emit_block("SubagentStop final-report enforcement failed while reading hook payload.")
        return
    try:
        reason = final_report_block_reason(root, payload)
        if reason:
            emit_block(reason)
    except Exception as exc:
        record_hook_failure(root, hook="enforce_subagent_final_report", exc=exc, payload=payload)
        emit_block("SubagentStop final-report enforcement failed closed.")


if __name__ == "__main__":
    main()

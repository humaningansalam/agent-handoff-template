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


def task_resume_context(root: Path) -> str:
    from tools.repoctl.cli import build_task_resume_projection

    projection = build_task_resume_projection(root)
    data = projection["data"]
    selection = data["selection"]
    task = data.get("task")
    guidance = data.get("resume_guidance")
    context: dict[str, object] = {
        "selection": selection,
        "task": task,
        "resume_guidance": None,
        "candidates": data.get("candidates") or [],
    }
    if isinstance(guidance, dict):
        handoff = guidance.get("handoff") if isinstance(guidance.get("handoff"), dict) else {}
        context["resume_guidance"] = {
            "status": guidance.get("status"),
            "changed_inputs": guidance.get("changed_inputs") or [],
            "reason_codes": handoff.get("reason_codes") or [],
            "executable_handoff": guidance.get("executable_handoff"),
        }
    return "[REPOCTL_TASK_RESUME]\n" + json.dumps(context, ensure_ascii=False, separators=(",", ":"))


def main() -> None:
    root = workspace_root()
    try:
        payload = json.load(sys.stdin) if not sys.stdin.isatty() else {}
        if not isinstance(payload, dict):
            payload = {}
    except Exception:
        payload = {}

    output = {"hookSpecificOutput": {"hookEventName": "UserPromptSubmit"}}
    contexts: list[str] = []
    try:
        contexts.append(task_resume_context(root))
    except Exception as exc:
        emit_hook_debug(f"Task resume context hook failed closed: {exc}")
    try:
        from tools.hooks.maintenance.prompt_approval import approval_context_for_prompt

        context = approval_context_for_prompt(root, payload)
        if context:
            contexts.append(context)
    except Exception as exc:
        emit_hook_debug(f"Maintenance approval hook failed closed: {exc}")
    if contexts:
        output["hookSpecificOutput"]["additionalContext"] = "\n\n".join(contexts)

    print(json.dumps(output, ensure_ascii=False))


if __name__ == "__main__":
    main()

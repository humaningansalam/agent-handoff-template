from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any

from tools.hooks.hook_failures import record_hook_failure
from tools.agent_harness.retry_policy import retry_agent_start_block_reason, retry_target
from tools.hooks.maintenance.scope import active_marker_for_session, is_maintenance_artifact_path, is_repo_path, json_dumps, relative_to_root, workspace_root
from tools.hooks.maintenance.trace import record_event
from tools.hooks.tool_input_normalization import UNSAFE_PARSE_ERROR, split_bash_command
from tools.runtime.json_io import read_json_object
PATH_KEYS = ("file_path", "path", "notebook_path")
EVIDENCE_VIEW_NAMES = {
    "evidence/cartography.json",
    "evidence/plan.json",
    "evidence/plan-review.json",
    "evidence/execution.json",
    "evidence/execution-review.json",
    "evidence/skeptic-review.json",
}
STATE_VIEW_NAME = "current-run-state.json"
STATE_VIEW_PATH = "ops/agent-harness/current-run-state.json"
EVENTS_JSONL = "ops/agent-harness/latest-events.jsonl"
IMPLEMENTER_BUDGET = {
    "Read": 30,
    "Grep": 15,
    "Glob": 15,
    "Edit": 10,
    "MultiEdit": 10,
    "Write": 10,
}
IMPLEMENTER_WALL_CLOCK_SECONDS = 300
REVIEW_WORKER_BUDGET = {
    "Read": 16,
    "Grep": 8,
    "Glob": 5,
    "Bash": 8,
}
REVIEW_WORKER_WALL_CLOCK_SECONDS = 180
REVIEW_WORKER_RETRY = {
    "maintenance-plan-critic": ("plan-review", "retry-plan"),
    "maintenance-evaluator": ("execution-review", "retry-evaluation"),
    "maintenance-skeptic": ("skeptic-review", "retry-evaluation"),
}


def _hook_event_name(payload: dict[str, Any]) -> str:
    return str(payload.get("hook_event_name") or payload.get("hookEventName") or "PreToolUse")


def _emit_permission_decision(hook_event_name: str, behavior: str, message: str = "") -> None:
    if hook_event_name == "PermissionRequest":
        decision: dict[str, Any] = {"behavior": behavior}
        if message:
            decision["message"] = message
        if behavior == "deny":
            decision["interrupt"] = True
        print(json_dumps({"hookSpecificOutput": {"hookEventName": "PermissionRequest", "decision": decision}}))
        return

    output = {
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": behavior,
        }
    }
    if message:
        output["hookSpecificOutput"]["permissionDecisionReason"] = message
    print(json_dumps(output))


def _emit_deny(hook_event_name: str, message: str) -> None:
    _emit_permission_decision(hook_event_name, "deny", message)


def _emit_allow(hook_event_name: str, message: str) -> None:
    _emit_permission_decision(hook_event_name, "allow", message)


def _load_payload() -> dict[str, Any]:
    raw = sys.stdin.read()
    if not raw.strip():
        return {}
    loaded = json.loads(raw)
    return loaded if isinstance(loaded, dict) else {}


def _tool_paths(tool_input: dict[str, Any]) -> tuple[str, ...]:
    paths: list[str] = []
    for key in PATH_KEYS:
        value = tool_input.get(key)
        if isinstance(value, str):
            paths.append(value)
    return tuple(paths)


def _blocked_reference(root: Path, tool_name: str, tool_input: dict[str, Any]) -> str:
    for raw_path in _tool_paths(tool_input):
        if is_repo_path(root, raw_path):
            return raw_path
    if tool_name == "Bash":
        parts = split_bash_command(tool_input)
        if parts != [UNSAFE_PARSE_ERROR]:
            for part in parts:
                if is_repo_path(root, part):
                    return part
    return ""


def _stale_run_artifact_read(root: Path, workflow_id: str, tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name != "Read":
        return ""
    for raw_path in _tool_paths(tool_input):
        blocked = _stale_run_artifact_path(root, workflow_id, raw_path)
        if blocked:
            return blocked
    return ""


def _stale_run_artifact_path(root: Path, workflow_id: str, raw_path: str) -> str:
    rel = relative_to_root(root, raw_path)
    if rel is None:
        return ""
    parts = Path(rel).parts
    if len(parts) < 4 or parts[:3] != ("ops", "agent-harness", "runs"):
        return ""
    artifact_workflow_id = parts[3]
    return raw_path if artifact_workflow_id != workflow_id else ""


def _maintenance_artifact_write(root: Path, tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name not in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        return ""
    for raw_path in _tool_paths(tool_input):
        if is_maintenance_artifact_path(root, raw_path):
            return raw_path
    return ""


def _missing_evidence_artifact_read(root: Path, tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name != "Read":
        return ""
    for raw_path in _tool_paths(tool_input):
        if not is_maintenance_artifact_path(root, raw_path):
            continue
        relative = relative_to_root(root, raw_path) or str(raw_path).lstrip("./")
        path = Path(raw_path)
        absolute = path if path.is_absolute() else root / path
        if relative.startswith("ops/agent-harness/"):
            relative = relative.removeprefix("ops/agent-harness/")
        if relative in EVIDENCE_VIEW_NAMES and not absolute.exists():
            return raw_path
    return ""


def _forbidden_agent_harness_command(tool_input: dict[str, Any]) -> bool:
    command = str(tool_input.get("command") or "")
    return "tools.agent_harness." in command and "tools.agent_harness.safe_artifact_writer" not in command


def _forbidden_safe_writer_args(tool_input: dict[str, Any]) -> bool:
    parts = split_bash_command(tool_input)
    forbidden = {"--content-" + suffix for suffix in ("text", "file", "base64")}
    return "tools.agent_harness.safe_artifact_writer" in parts and any(part in forbidden for part in parts)


def _unparseable_bash_command(tool_input: dict[str, Any]) -> bool:
    return UNSAFE_PARSE_ERROR in split_bash_command(tool_input)


def _forbidden_agent_harness_deny_message(root: Path) -> str:
    state = read_json_object(root / STATE_VIEW_PATH, missing_ok=True)
    pass_eligibility = state.get("pass_eligibility") if isinstance(state.get("pass_eligibility"), dict) else {}
    calculated = pass_eligibility.get("calculated") if isinstance(pass_eligibility.get("calculated"), dict) else {}
    if calculated.get("eligible") is True:
        return "maintenance checker is already pass-eligible; do not call phase helper commands, emit final `pass`"
    blockers = calculated.get("blocked_by") if isinstance(calculated.get("blocked_by"), list) else []
    if blockers:
        return "maintenance phase helper commands are not workflow drivers; continue via phase agents/evidence artifacts and resolve blockers: " + ", ".join(str(item) for item in blockers)
    return "maintenance phase helper commands are not workflow drivers; continue via phase agents, evidence artifacts, and hook-updated state"


def _agent_type_from_payload(payload: dict[str, Any], tool_input: dict[str, Any]) -> str:
    for key in ("agent_type", "subagent_type"):
        value = payload.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    for key in ("agent_type", "subagent_type", "agent", "name"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value.strip()
    return ""


def _agent_retry_block_reason(root: Path, payload: dict[str, Any], tool_input: dict[str, Any]) -> str:
    state = read_json_object(root / STATE_VIEW_PATH, missing_ok=True)
    target = retry_target(state)
    if not target:
        return _profile_agent_block_reason(state, payload, tool_input) or _missing_prior_evidence_agent_blocker(root, payload, tool_input)
    agent_type = _agent_type_from_payload(payload, tool_input)
    if not agent_type:
        return f"maintenance retry route {target} cannot verify Agent target from PreToolUse payload; blocked Agent."
    if not agent_type.startswith("maintenance-"):
        return f"maintenance retry route {target} blocks non-maintenance Agent {agent_type}."
    return retry_agent_start_block_reason(root, state, agent_type)


def _profile_agent_block_reason(state: dict[str, Any], payload: dict[str, Any], tool_input: dict[str, Any]) -> str:
    agent_type = _agent_type_from_payload(payload, tool_input)
    pass_eligibility = state.get("pass_eligibility") if isinstance(state.get("pass_eligibility"), dict) else {}
    cursor = pass_eligibility.get("route_cursor") if isinstance(pass_eligibility.get("route_cursor"), dict) else {}
    completed = {str(worker) for worker in cursor.get("completed_workers", []) if str(worker).strip()} if isinstance(cursor.get("completed_workers"), list) else set()
    next_worker = str(cursor.get("next_required_worker") or "").strip()
    workflow_profile = pass_eligibility.get("workflow_profile") if isinstance(pass_eligibility.get("workflow_profile"), dict) else {}
    route = tuple(str(worker) for worker in workflow_profile.get("route", []) if str(worker).strip()) if isinstance(workflow_profile.get("route"), list) else ()
    if route and agent_type.startswith("maintenance-") and agent_type not in route:
        return f"checker policy route does not include {agent_type}; allowed route: {', '.join(route)}"
    if route and agent_type.startswith("maintenance-"):
        if next_worker and agent_type != next_worker:
            return f"checker route cursor requires {next_worker}; blocked {agent_type}."
        if agent_type == "maintenance-implementer" and _implementation_already_allowed_needs_evidence(state):
            return (
                "approved implementation edit is already recorded; do not call maintenance-implementer again. "
                "Write `--kind execution --status passed` evidence with `uv run python -m tools.agent_harness.safe_artifact_writer write ...` "
                "for the approved changed files before continuing. Do not use direct `python`, script paths, `PYTHONPATH=...`, `rg`, or `git` as evidence commands."
            )
        if not next_worker and agent_type in completed:
            return f"checker route cursor already completed {agent_type}; no required workers remain."
    calculated = pass_eligibility.get("calculated") if isinstance(pass_eligibility.get("calculated"), dict) else {}
    profile = str(calculated.get("workflow_path") or pass_eligibility.get("workflow_path") or "")
    if profile == "TINY_DOC" and agent_type in {"maintenance-plan-critic", "maintenance-evaluator", "maintenance-skeptic"}:
        return f"checker profile TINY_DOC does not require {agent_type}; run host verification and write execution-review evidence instead."
    if profile == "STANDARD" and agent_type == "maintenance-skeptic":
        return "checker profile STANDARD does not require maintenance-skeptic; continue to checker-gated decision."
    return ""


def _implementation_already_allowed_needs_evidence(state: dict[str, Any]) -> bool:
    changed = state.get("changed_files") if isinstance(state.get("changed_files"), list) else []
    worker_status = state.get("worker_status") if isinstance(state.get("worker_status"), dict) else {}
    implementer = worker_status.get("maintenance-implementer") if isinstance(worker_status.get("maintenance-implementer"), dict) else {}
    return bool(changed and not implementer.get("structured_evidence_valid"))


def _missing_prior_evidence_agent_blocker(root: Path, payload: dict[str, Any], tool_input: dict[str, Any]) -> str:
    agent_type = _agent_type_from_payload(payload, tool_input)
    required_before_agent = {
        "maintenance-plan-critic": "ops/agent-harness/evidence/plan.json",
        "maintenance-evaluator": "ops/agent-harness/evidence/execution.json",
        "maintenance-skeptic": "ops/agent-harness/evidence/execution-review.json",
    }
    required_path = required_before_agent.get(agent_type)
    if not required_path:
        return ""
    if (root / required_path).is_file() and not (root / required_path).is_symlink():
        return ""
    return f"write {required_path} with safe_artifact_writer JSON evidence before invoking {agent_type}"


def _implementer_budget_block_reason(root: Path, marker: dict[str, Any], payload: dict[str, Any], tool_input: dict[str, Any]) -> str:
    if _agent_type_from_payload(payload, tool_input) != "maintenance-implementer":
        return ""
    tool_name = str(payload.get("tool_name") or "")
    limit = IMPLEMENTER_BUDGET.get(tool_name)
    if limit is None:
        return ""
    workflow_id = str(marker.get("workflow_id") or "")
    events = _recent_events(root)
    worker_events = [
        event for event in events
        if event.get("workflow_id") == workflow_id and event.get("agent_type") == "maintenance-implementer"
    ]
    if _worker_wall_clock_seconds(worker_events) > IMPLEMENTER_WALL_CLOCK_SECONDS:
        return _implementer_budget_handoff("wall clock over 300s")
    used = sum(1 for event in worker_events if event.get("event") == "pre_tool" and event.get("tool_name") == tool_name)
    if used >= limit:
        return _implementer_budget_handoff(f"{tool_name} limit {limit}")
    mutation_used = sum(
        1 for event in worker_events
        if event.get("event") == "pre_tool" and event.get("tool_name") in {"Edit", "MultiEdit", "Write"}
    )
    if tool_name in {"Edit", "MultiEdit", "Write"} and mutation_used >= IMPLEMENTER_BUDGET["Edit"]:
        return _implementer_budget_handoff("mutation limit 10")
    return ""


def _implementer_budget_handoff(reason: str) -> str:
    return (
        f"maintenance-implementer budget exceeded: {reason}; do not call maintenance-implementer again. "
        "Write failed `--kind execution --status failed --retry-target retry-implementation` evidence with "
        "`uv run python -m tools.agent_harness.safe_artifact_writer write ...`, "
        "or return `needs-human-decision` if the failure cannot be represented as structured evidence."
    )


def _review_worker_budget_block_reason(root: Path, marker: dict[str, Any], payload: dict[str, Any], tool_input: dict[str, Any]) -> str:
    agent_type = _agent_type_from_payload(payload, tool_input)
    artifact_retry = REVIEW_WORKER_RETRY.get(agent_type)
    if not artifact_retry:
        return ""
    tool_name = str(payload.get("tool_name") or "")
    limit = REVIEW_WORKER_BUDGET.get(tool_name)
    if limit is None:
        return ""
    workflow_id = str(marker.get("workflow_id") or "")
    events = _recent_events(root)
    worker_events = [
        event for event in events
        if event.get("workflow_id") == workflow_id and event.get("agent_type") == agent_type
    ]
    if _worker_wall_clock_seconds(worker_events) > REVIEW_WORKER_WALL_CLOCK_SECONDS:
        return _review_worker_budget_handoff(agent_type, artifact_retry, f"wall clock over {REVIEW_WORKER_WALL_CLOCK_SECONDS}s")
    used = sum(1 for event in worker_events if event.get("event") == "pre_tool" and event.get("tool_name") == tool_name)
    if used >= limit:
        return _review_worker_budget_handoff(agent_type, artifact_retry, f"{tool_name} limit {limit}")
    return ""


def _review_worker_budget_handoff(agent_type: str, artifact_retry: tuple[str, str], reason: str) -> str:
    artifact_kind, retry = artifact_retry
    return (
        f"{agent_type} budget exceeded: {reason}; do not call {agent_type} again. "
        f"Write failed `--kind {artifact_kind} --status failed --retry-target {retry}` evidence with "
        "`uv run python -m tools.agent_harness.safe_artifact_writer write ...`, "
        "or return `needs-human-decision` if the failure cannot be represented as structured evidence."
    )


def _recent_events(root: Path) -> list[dict[str, Any]]:
    path = root / EVENTS_JSONL
    if not path.is_file() or path.is_symlink():
        return []
    rows: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines()[-400:]:
        try:
            loaded = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(loaded, dict):
            rows.append(loaded)
    return rows


def _worker_wall_clock_seconds(events: list[dict[str, Any]]) -> float:
    timestamps: list[datetime] = []
    for event in events:
        raw = str(event.get("captured_at") or "")
        if not raw:
            continue
        try:
            timestamps.append(datetime.fromisoformat(raw.replace("Z", "+00:00")))
        except ValueError:
            continue
    if len(timestamps) < 2:
        return 0.0
    return (max(timestamps) - min(timestamps)).total_seconds()


def _repo_mutation_before_approval(root: Path, tool_name: str, tool_input: dict[str, Any]) -> str:
    if tool_name not in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        return ""
    for raw_path in _tool_paths(tool_input):
        if is_maintenance_artifact_path(root, raw_path) or is_repo_path(root, raw_path):
            continue
        path = Path(raw_path)
        absolute = path if path.is_absolute() else root / path
        try:
            absolute.resolve(strict=False).relative_to(root.resolve(strict=False))
        except ValueError:
            continue
        state = read_json_object(root / STATE_VIEW_PATH, missing_ok=True)
        if str(state.get("phase") or "") != "approved_frozen":
            return raw_path
    return ""


def _relative_repo_mutation_paths(root: Path, tool_name: str, tool_input: dict[str, Any]) -> tuple[tuple[str, str], ...]:
    if tool_name not in {"Write", "Edit", "MultiEdit", "NotebookEdit"}:
        return ()
    paths: list[tuple[str, str]] = []
    for raw_path in _tool_paths(tool_input):
        if is_maintenance_artifact_path(root, raw_path) or is_repo_path(root, raw_path):
            continue
        path = Path(raw_path)
        absolute = path if path.is_absolute() else root / path
        try:
            relative = absolute.resolve(strict=False).relative_to(root.resolve(strict=False)).as_posix()
        except ValueError:
            continue
        paths.append((raw_path, relative))
    return tuple(paths)


def _surface_allows_path(surface: str, relative_path: str) -> bool:
    normalized_surface = surface.strip().lstrip("./")
    normalized_path = relative_path.strip().lstrip("./")
    if not normalized_surface:
        return False
    if normalized_surface.endswith("/**"):
        prefix = normalized_surface[:-3].rstrip("/")
        return normalized_path == prefix or normalized_path.startswith(prefix + "/")
    return normalized_path == normalized_surface


def _approved_repo_mutation_decision(root: Path, tool_name: str, tool_input: dict[str, Any]) -> tuple[str, str]:
    mutation_paths = _relative_repo_mutation_paths(root, tool_name, tool_input)
    if not mutation_paths:
        return "", ""
    state = read_json_object(root / STATE_VIEW_PATH, missing_ok=True)
    approval_gate = state.get("approval_gate") if isinstance(state.get("approval_gate"), dict) else {}
    freeze = approval_gate.get("freeze") if isinstance(approval_gate.get("freeze"), dict) else {}
    surfaces = tuple(str(item) for item in freeze.get("affected_surfaces", []) if str(item).strip())
    approved = str(state.get("phase") or "") == "approved_frozen" and approval_gate.get("status") == "approved-frozen"
    for raw_path, relative_path in mutation_paths:
        if not approved:
            return "deny:maintenance implementation edits require approved_frozen state", raw_path
        if not any(_surface_allows_path(surface, relative_path) for surface in surfaces):
            return "deny:maintenance implementation edit is outside approved affected surfaces", raw_path
    return "allow", mutation_paths[0][0]


def main() -> None:
    root = workspace_root()
    hook_event_name = "PreToolUse"
    tool_name = ""
    try:
        payload = _load_payload()
        hook_event_name = _hook_event_name(payload)
        tool_name = str(payload.get("tool_name") or "")
        if tool_name not in {"Agent", "Skill", "Read", "Bash", "Write", "Edit", "MultiEdit", "NotebookEdit"}:
            return
        session_id = str(payload.get("session_id") or "").strip()
        marker = active_marker_for_session(root, session_id)
        if not marker:
            return
        tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
        budget_block = _implementer_budget_block_reason(root, marker, payload, tool_input)
        if not budget_block:
            budget_block = _review_worker_budget_block_reason(root, marker, payload, tool_input)
        if budget_block:
            record_event(root, marker, payload, event="worker-budget-deny", phase="budget", guard="deny", result=budget_block)
            _emit_deny(hook_event_name, budget_block)
            return
        if tool_name == "Agent":
            agent_block = _agent_retry_block_reason(root, payload, tool_input)
            if agent_block:
                record_event(root, marker, payload, event="agent-deny", phase="guard", guard="deny", result=agent_block)
                _emit_deny(hook_event_name, agent_block)
                return
        if tool_name == "Skill":
            record_event(root, marker, payload, event="skill-deny", phase="guard", guard="deny", result="maintenance workflow owns orchestration")
            _emit_deny(hook_event_name, "maintenance-workflow must not invoke other skills; continue via phase agents and checker artifacts")
            return
        if tool_name == "Bash":
            if _unparseable_bash_command(tool_input):
                message = "maintenance scope guard blocks unparseable Bash commands"
                record_event(root, marker, payload, event="bash-parse-deny", phase="guard", guard="deny", result=message)
                _emit_deny(hook_event_name, message)
                return
            if _forbidden_agent_harness_command(tool_input):
                message = _forbidden_agent_harness_deny_message(root)
                record_event(root, marker, payload, event="phase-helper-command-deny", phase="guard", guard="deny", result=message)
                _emit_deny(hook_event_name, message)
                return
            if _forbidden_safe_writer_args(tool_input):
                message = "safe_artifact_writer accepts structured flags only; content payload flags are denied"
                record_event(root, marker, payload, event="safe-writer-arg-deny", phase="guard", guard="deny", result=message)
                _emit_deny(hook_event_name, message)
                return
        blocked = _blocked_reference(root, tool_name, tool_input)
        if blocked:
            record_event(root, marker, payload, event="scope-guard-deny", phase="guard", guard="deny", result="repo/** blocked")
            _emit_deny(hook_event_name, "maintenance scope guard blocks repo/** access")
            return
        stale_run_read = _stale_run_artifact_read(root, str(marker.get("workflow_id") or ""), tool_name, tool_input)
        if stale_run_read:
            record_event(root, marker, payload, event="artifact-read-deny", phase="artifact", guard="deny", result="stale run artifact blocked", artifact_path=stale_run_read)
            _emit_deny(hook_event_name, "maintenance cannot read artifacts from a different workflow run; use current run latest views or rerun phase agents")
            return
        missing_evidence_read = _missing_evidence_artifact_read(root, tool_name, tool_input)
        if missing_evidence_read:
            record_event(root, marker, payload, event="artifact-read-deny", phase="artifact", guard="deny", result="missing evidence artifact must be written after worker output", artifact_path=missing_evidence_read)
            _emit_deny(hook_event_name, "missing maintenance evidence artifacts must be created with Write after worker output, not read before they exist")
            return
        artifact_write = _maintenance_artifact_write(root, tool_name, tool_input)
        if artifact_write:
            message = "maintenance artifacts are safe-writer generated; direct Write/Edit to ops/agent-harness is denied"
            record_event(root, marker, payload, event="artifact-write-deny", phase="artifact", guard="deny", result=message, artifact_path=artifact_write)
            _emit_deny(hook_event_name, message)
            return
        repo_mutation_decision, repo_mutation_path = _approved_repo_mutation_decision(root, tool_name, tool_input)
        if repo_mutation_decision.startswith("deny:"):
            reason = repo_mutation_decision.split(":", 1)[1]
            record_event(root, marker, payload, event="implementation-deny", phase="approval", guard="deny", result=reason, artifact_path=repo_mutation_path)
            _emit_deny(hook_event_name, reason)
            return
        if repo_mutation_decision == "allow":
            record_event(root, marker, payload, event="implementation-allow", phase="implementation", guard="allow", result="approved affected surface edit", artifact_path=repo_mutation_path)
            _emit_allow(hook_event_name, "maintenance approved affected surface edit allowed")
            return
        record_event(root, marker, payload, event="scope-guard-check", phase="guard", guard="allow")
    except Exception as exc:  # pragma: no cover - defensive hook boundary
        record_hook_failure(root, hook="enforce_maintenance_scope", exc=exc, payload={"tool_name": tool_name})
        _emit_deny(hook_event_name, "maintenance scope guard failed; refusing unaudited maintenance tool access")


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from tools.hooks.maintenance.scope import active_marker_for_session
from tools.hooks.subagent_transcript import now_iso, redact_text
from tools.agent_harness import paths as harness_paths
from tools.agent_harness.checker import ensure_state, reconcile_current_state as checker_reconcile_current_state
from tools.runtime.json_io import append_jsonl_atomic_under_root, read_json_object, write_text_atomic_under_root


TRACE_ROOT = harness_paths.ARTIFACT_ROOT
LATEST_TRACE = harness_paths.LATEST_TRACE
EVENTS_JSONL = harness_paths.EVENTS_JSONL
STATE_JSON = harness_paths.STATE_JSON
MAX_EVENTS = 80

def maintenance_agent(agent_type: str) -> bool:
    return agent_type.startswith("maintenance-")


def record_active_event(
    root: Path,
    payload: dict[str, Any],
    *,
    event: str,
    phase: str = "running",
    result: str = "",
    guard: str = "",
    artifact_path: str = "",
) -> None:
    session_id = str(payload.get("session_id") or "")
    marker = active_marker_for_session(root, session_id)
    if not marker:
        return
    record_event(root, marker, payload, event=event, phase=phase, result=result, guard=guard, artifact_path=artifact_path)


def record_event(
    root: Path,
    marker: dict[str, Any],
    payload: dict[str, Any],
    *,
    event: str,
    phase: str = "running",
    result: str = "",
    guard: str = "",
    artifact_path: str = "",
) -> None:
    workflow_id = str(marker.get("workflow_id") or marker.get("session_id") or "maintenance-workflow")
    tool_input = payload.get("tool_input") if isinstance(payload.get("tool_input"), dict) else {}
    row = {
        "captured_at": now_iso(),
        "workflow_id": workflow_id,
        "phase": phase,
        "event": event,
        "session_id": str(payload.get("session_id") or marker.get("session_id") or ""),
        "agent_type": str(payload.get("agent_type") or payload.get("subagent_type") or ""),
        "tool_name": str(payload.get("tool_name") or ""),
        "artifact_path": artifact_path,
        "guard": guard,
        "result": result,
        "path": _tool_path(tool_input),
        "command": redact_text(str(tool_input.get("command") or ""))[:300],
    }
    compact = {key: value for key, value in row.items() if value}
    append_jsonl_atomic_under_root(root / EVENTS_JSONL, compact, root, max_bytes=1024 * 1024)
    if not _pre_state_write_event(compact):
        _ensure_state(root, marker, compact)
    append_jsonl_atomic_under_root(root / TRACE_ROOT / "runs" / workflow_id / "events.jsonl", compact, root, max_bytes=1024 * 1024)
    _write_trace_view(root, marker)


def _pre_state_write_event(row: dict[str, Any]) -> bool:
    if row.get("tool_name") != "Write":
        return False
    target = str(row.get("artifact_path") or row.get("path") or "")
    if not target.endswith(str(STATE_JSON)):
        return False
    return row.get("phase") == "pre_tool"


def initialize_trace(root: Path, marker: dict[str, Any], payload: dict[str, Any]) -> None:
    _reset_latest_trace(root)
    record_event(root, marker, payload, event="workflow-start", phase="intake", result="scope guard active")


def reconcile_current_state(root: Path) -> dict[str, Any]:
    return checker_reconcile_current_state(root)


def _reset_latest_trace(root: Path) -> None:
    stale_views = tuple(Path(path) for path in harness_paths.EVIDENCE_ARTIFACT_PATHS)
    stale_metadata = tuple(Path(path) for path in harness_paths.LATEST_METADATA_PATHS)
    for relative in (LATEST_TRACE, EVENTS_JSONL, STATE_JSON, *stale_views, *stale_metadata):
        path = root / relative
        if path.exists() or path.is_symlink():
            if path.is_symlink():
                raise RuntimeError(f"maintenance trace file is symlink: {path}")
            path.relative_to(root)
            path.unlink()


def _ensure_state(root: Path, marker: dict[str, Any], latest: dict[str, Any]) -> None:
    ensure_state(root, marker, latest)


def _write_trace_view(root: Path, marker: dict[str, Any]) -> None:
    events = _read_recent_events(root / EVENTS_JSONL)
    workflow_id = str(marker.get("workflow_id") or marker.get("session_id") or "maintenance-workflow")
    state = read_json_object(root / STATE_JSON, missing_ok=True)
    lines = [
        "# Maintenance Workflow Trace",
        "",
        f"- workflow_id: `{workflow_id}`",
        f"- scope_guard: `{marker.get('scope_guard', 'active')}`",
        f"- topic_focus: {marker.get('prompt_excerpt', '')}",
        "- detailed_worker_output: artifact-only",
        "",
        "## Current State",
        "",
        f"- phase: `{state.get('phase', '')}`",
        f"- active_candidate_id: `{state.get('active_candidate_id', '')}`",
        f"- queued_candidate_ids: `{_compact_list(state.get('queued_candidate_ids'))}`",
        f"- queue_policy: `{state.get('queue_policy', 'human-decision')}`",
        f"- approval_gate: `{_nested_value(state.get('approval_gate'), 'status')}`",
        f"- retry_target: `{_nested_value(state.get('retry'), 'target')}`",
        f"- artifact_paths: `{_artifact_paths(state.get('artifacts'))}`",
        "",
        "## Recent Events",
        "",
        "| time | phase | event | agent/tool | result | path/command |",
        "| --- | --- | --- | --- | --- | --- |",
    ]
    for event in events[-MAX_EVENTS:]:
        actor = event.get("agent_type") or event.get("tool_name") or "orchestrator"
        target = event.get("artifact_path") or event.get("path") or event.get("command") or ""
        lines.append(
            "| {time} | `{phase}` | `{event}` | `{actor}` | {result} | {target} |".format(
                time=str(event.get("captured_at") or ""),
                phase=str(event.get("phase") or ""),
                event=str(event.get("event") or ""),
                actor=str(actor),
                result=str(event.get("result") or event.get("guard") or ""),
                target=str(target).replace("|", "\\|"),
            )
        )
    lines.append("")
    current = _read_existing_trace(root / LATEST_TRACE)
    content = _merge_trace_sections("\n".join(lines), current)
    write_text_atomic_under_root(root / LATEST_TRACE, content, root)


def _read_existing_trace(path: Path) -> str:
    if path.is_symlink():
        raise RuntimeError(f"maintenance trace file is symlink: {path}")
    if not path.is_file():
        return ""
    return path.read_text(encoding="utf-8", errors="replace")


def _merge_trace_sections(managed: str, current: str) -> str:
    return managed


def _compact_list(value: Any) -> str:
    if not isinstance(value, list):
        return ""
    return ",".join(str(item) for item in value if str(item).strip())[:300]


def _nested_value(value: Any, key: str) -> str:
    if not isinstance(value, dict):
        return ""
    return str(value.get(key) or "")[:120]


def _artifact_paths(value: Any) -> str:
    if isinstance(value, dict):
        value = list(value.values())
    if not isinstance(value, list):
        return ""
    paths: list[str] = []
    for item in value:
        if isinstance(item, dict) and str(item.get("path") or "").strip():
            paths.append(str(item["path"]))
    return ",".join(paths)[:500]


def _read_recent_events(path: Path) -> list[dict[str, Any]]:
    if not path.is_file() or path.is_symlink():
        return list()
    events: list[dict[str, Any]] = []
    for line in path.read_text(encoding="utf-8", errors="replace").splitlines():
        if not line.strip():
            continue
        try:
            import json

            loaded = json.loads(line)
        except Exception:
            continue
        if isinstance(loaded, dict):
            events.append(loaded)
    return events


def _tool_path(tool_input: dict[str, Any]) -> str:
    for key in ("file_path", "path", "notebook_path"):
        value = tool_input.get(key)
        if isinstance(value, str) and value.strip():
            return value[:300]
    return ""

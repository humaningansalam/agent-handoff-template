from __future__ import annotations

import json
import os
import re
from datetime import UTC, datetime
from pathlib import Path
from typing import Any

from tools.runtime.json_io import read_json_object, write_json_atomic_under_root


MAINTENANCE_OPERATION = "maintenance-workflow"


def workspace_root() -> Path:
    env_root = os.environ.get("WORKSPACE_ROOT")
    if env_root:
        return Path(env_root).resolve()
    return Path(__file__).resolve().parents[2]


def active_dir(root: Path) -> Path:
    return root / "ops" / "agent-harness" / "active-sessions"


def clear_all_markers(root: Path) -> None:
    directory = active_dir(root)
    if directory.is_symlink():
        raise RuntimeError(f"maintenance active marker dir is symlink: {directory}")
    if not directory.exists():
        return
    for path in directory.glob("*.json"):
        if path.is_symlink():
            raise RuntimeError(f"maintenance active marker is symlink: {path}")
        path.unlink()


def marker_path(root: Path, session_id: str) -> Path:
    safe_session = re.sub(r"[^A-Za-z0-9._-]", "_", session_id or "default")
    return active_dir(root) / f"{safe_session}.json"


def is_maintenance_prompt(prompt: str) -> bool:
    stripped = prompt.strip()
    return stripped == "/maintenance-workflow" or stripped.startswith("/maintenance-workflow ")


def write_marker(root: Path, payload: dict[str, Any], *, prompt: str) -> dict[str, Any]:
    session_id = str(payload.get("session_id") or "default")
    workflow_id = f"mw-{session_id}"
    return write_marker_record(root, session_id=session_id, workflow_id=workflow_id, prompt=prompt)


def write_marker_record(root: Path, *, session_id: str, workflow_id: str, prompt: str) -> dict[str, Any]:
    marker = {
        "schema_version": 1,
        "workflow_id": workflow_id,
        "session_id": session_id,
        "started_at": datetime.now(UTC).isoformat().replace("+00:00", "Z"),
        "operation": MAINTENANCE_OPERATION,
        "command": "/maintenance-workflow",
        "scope_guard": "repo/** denied by maintenance scope hook",
        "prompt_excerpt": prompt.strip()[:240],
    }
    write_json_atomic_under_root(marker_path(root, session_id), marker, root)
    return marker


def active_marker_for_session(root: Path, session_id: str) -> dict[str, Any] | None:
    marker = read_json_object(marker_path(root, session_id), missing_ok=True)
    if not marker or marker.get("operation") != MAINTENANCE_OPERATION:
        return None
    return marker


def clear_marker(root: Path, session_id: str) -> None:
    path = marker_path(root, session_id)
    if path.is_symlink():
        raise RuntimeError(f"maintenance active marker is symlink: {path}")
    if path.exists():
        path.unlink()


def relative_to_root(root: Path, raw_path: str) -> str | None:
    if not raw_path.strip():
        return None
    candidate = Path(raw_path)
    if not candidate.is_absolute():
        candidate = root / candidate
    try:
        resolved = candidate.resolve(strict=False)
        return resolved.relative_to(root.resolve(strict=False)).as_posix()
    except ValueError:
        return None


def is_repo_path(root: Path, raw_path: str) -> bool:
    rel = relative_to_root(root, raw_path)
    if rel is None:
        return False
    parts = Path(rel).parts
    return bool(parts) and parts[0] == "repo"


def is_maintenance_artifact_path(root: Path, raw_path: str) -> bool:
    rel = relative_to_root(root, raw_path)
    if rel is None:
        return False
    parts = Path(rel).parts
    return len(parts) >= 2 and parts[:2] == ("ops", "agent-harness")


def json_dumps(data: dict[str, Any]) -> str:
    return json.dumps(data, ensure_ascii=False)

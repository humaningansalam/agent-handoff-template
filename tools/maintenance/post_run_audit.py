from __future__ import annotations

import argparse
import json
import subprocess
from pathlib import Path
from typing import Any

PLACEHOLDER_MARKERS = (
    "아직 승격된",
    "placeholder shell",
    "placeholder-style",
    "현재 상태\n\n아직",
)


def _read_json(path: Path) -> dict[str, Any]:
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}
    return data if isinstance(data, dict) else {}


def _nested(data: dict[str, Any], *keys: str) -> Any:
    current: Any = data
    for key in keys:
        if not isinstance(current, dict):
            return None
        current = current.get(key)
    return current


def _git_dirty_count(root: Path) -> int | None:
    try:
        result = subprocess.run(
            ["git", "status", "--short"],
            cwd=root,
            text=True,
            check=True,
            stdout=subprocess.PIPE,
            stderr=subprocess.DEVNULL,
        )
    except (OSError, subprocess.CalledProcessError):
        return None
    return len([line for line in result.stdout.splitlines() if line.strip()])


def _active_marker_count(root: Path) -> int:
    active_root = root / "ops" / "research-ops" / "_active"
    if not active_root.exists():
        return 0
    return sum(1 for path in active_root.rglob("*") if path.is_file())


def _project_dirs(root: Path) -> list[str]:
    projects_root = root / "projects"
    if not projects_root.is_dir():
        return []
    return sorted(path.name for path in projects_root.iterdir() if path.is_dir())


def _wiki_quality(root: Path) -> dict[str, Any]:
    knowledge_root = root / "wiki" / "knowledge"
    files = sorted(knowledge_root.rglob("*.md")) if knowledge_root.is_dir() else []
    pattern_files = [path for path in files if "/patterns/" in path.as_posix()]
    placeholder_files: list[str] = []
    for path in files:
        text = path.read_text(encoding="utf-8")
        lowered = text.lower()
        if any(marker.lower() in lowered for marker in PLACEHOLDER_MARKERS):
            placeholder_files.append(path.relative_to(root).as_posix())
    return {
        "durable_file_count": len(files),
        "pattern_file_count": len(pattern_files),
        "placeholder_file_count": len(placeholder_files),
        "placeholder_files": placeholder_files,
    }


def _readback_quality(root: Path) -> dict[str, Any]:
    init_state = _read_json(root / "ops" / "research-ops" / "init-notion" / "repo" / "state.json")
    backup_state = _read_json(root / "ops" / "research-ops" / "backup-wiki" / "repo" / "state.json")
    backup_verify = _read_json(root / "ops" / "research-ops" / "backup-wiki" / "repo" / "backup-verify.json")
    return {
        "init_verified": bool(_nested(init_state, "data", "verify_readback", "verified")),
        "backup_verified": bool(_nested(backup_state, "data", "backup_verify", "verified") or backup_verify.get("verified")),
        "backup_status": _nested(backup_state, "data", "backup_verify", "status") or backup_verify.get("status"),
        "backup_project_count": _nested(backup_state, "data", "backup_verify", "results", "backup", "project_count") or backup_verify.get("project_count"),
    }


def _workflow_evidence_files(root: Path) -> list[Path]:
    evidence_root = root / "ops" / "research-ops" / "post-run-audit"
    if not evidence_root.is_dir():
        return []
    return sorted(evidence_root.rglob("evidence.json"))


def _actual_source_verified(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return payload.get("verified") is True and payload.get("source") == "notion_api_readback"


def _delete_actual_verified(payload: Any) -> bool:
    if not isinstance(payload, dict):
        return False
    return (
        payload.get("source") == "notion_api_readback"
        and payload.get("archived") is True
        and payload.get("in_trash") is True
    )


def _workflow_readback_quality(root: Path) -> dict[str, Any]:
    best: dict[str, Any] = {
        "evidence_file": "",
        "project_verified": False,
        "execution_verified_count": 0,
        "wrapup_verified": False,
        "delete_verified": False,
    }
    for path in _workflow_evidence_files(root):
        payload = _read_json(path)
        execution_readbacks = payload.get("execution_readbacks")
        if not isinstance(execution_readbacks, list):
            execution_readbacks = []
        current = {
            "evidence_file": path.relative_to(root).as_posix(),
            "project_verified": _actual_source_verified(payload.get("project_readback")),
            "execution_verified_count": sum(1 for item in execution_readbacks if _actual_source_verified(item)),
            "wrapup_verified": _actual_source_verified(payload.get("wrapup_readback")),
            "delete_verified": _delete_actual_verified(payload.get("delete_readback")),
        }
        if current["execution_verified_count"] > best["execution_verified_count"]:
            best = current
        elif current["execution_verified_count"] == best["execution_verified_count"] and all(
            current[key] for key in ("project_verified", "wrapup_verified", "delete_verified")
        ):
            best = current
    return best


def audit(root: Path) -> dict[str, Any]:
    root = root.resolve()
    wiki = _wiki_quality(root)
    readback = _readback_quality(root)
    workflow_readback = _workflow_readback_quality(root)
    active_markers = _active_marker_count(root)
    project_dirs = _project_dirs(root)
    dirty_count = _git_dirty_count(root)

    blockers: list[str] = []
    warnings: list[str] = []
    if active_markers:
        blockers.append("open active operation markers remain")
    if project_dirs:
        warnings.append("project directories remain; confirm they are intentional, not disposable residue")
    if wiki["pattern_file_count"] == 0:
        blockers.append("root wiki has no cross-project pattern knowledge")
    if wiki["placeholder_file_count"]:
        warnings.append("root wiki still contains placeholder knowledge shells")
    if not readback["init_verified"]:
        blockers.append("init-notion readback proof is missing")
    if not readback["backup_verified"]:
        blockers.append("backup-wiki readback proof is missing")
    if not workflow_readback["project_verified"]:
        blockers.append("project row readback proof is missing")
    if workflow_readback["execution_verified_count"] < 3:
        blockers.append("fewer than three execution row readbacks are proven")
    if not workflow_readback["wrapup_verified"]:
        blockers.append("wrap-up readback proof is missing")
    if not workflow_readback["delete_verified"]:
        blockers.append("delete archive/trash readback proof is missing")
    if dirty_count and dirty_count > 100:
        warnings.append("worktree is too dirty for easy human review")

    return {
        "schema_version": 1,
        "verdict": "needs-work" if blockers else "usable-with-review",
        "human_assistant_ready": not blockers,
        "blockers": blockers,
        "warnings": warnings,
        "active_marker_count": active_markers,
        "project_dirs": project_dirs,
        "git_dirty_count": dirty_count,
        "wiki": wiki,
        "readback": readback,
        "workflow_readback": workflow_readback,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    args = parser.parse_args()
    print(json.dumps(audit(Path(args.root)), ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

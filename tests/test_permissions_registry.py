from __future__ import annotations

import json
from pathlib import Path

from tools.registries.agent_registry import AGENTS


REPO_ROOT = Path(__file__).resolve().parents[1]

EXPECTED_MAINTENANCE_WORKERS = {
    "maintenance-cartographer": (("Read", "Grep", "Glob"), "plan"),
    "maintenance-planner": (("Read", "Grep", "Glob"), "plan"),
    "maintenance-plan-critic": (("Read", "Grep", "Glob"), "plan"),
    "maintenance-implementer": (("Read", "Grep", "Glob", "Edit", "MultiEdit", "Write"), "default"),
    "maintenance-evaluator": (("Read", "Grep", "Glob", "Bash"), "plan"),
    "maintenance-skeptic": (("Read", "Grep", "Glob", "Bash"), "plan"),
}


def test_default_and_maintenance_settings_keep_their_security_boundaries() -> None:
    settings = json.loads((REPO_ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    permissions = settings.get("permissions", {}).get("allow", [])
    denied = settings.get("permissions", {}).get("deny", [])

    forbidden = {
        "Bash(git *)",
        "Bash(rg *)",
        "Bash(python *)",
        "Bash(python3 *)",
    }
    assert forbidden.isdisjoint(permissions)
    assert not any("repos/**" in permission for permission in denied)
    assert not any(
        "maintenance/" in str(hook)
        for entries in settings.get("hooks", {}).values()
        for entry in entries
        for hook in entry.get("hooks", [])
    )

    maintenance = json.loads((REPO_ROOT / ".claude/settings.maintenance.json").read_text(encoding="utf-8"))
    maintenance_allow = maintenance["permissions"]["allow"]
    assert any("repos/**" in permission for permission in maintenance["permissions"]["deny"])
    assert "Bash(uv run pytest *)" in maintenance_allow
    assert "Bash(uv run python -m tools.agent_harness.safe_artifact_writer write *)" in maintenance_allow
    assert not any(
        entry.startswith(("Write(ops/agent-harness", "Edit(ops/agent-harness", "MultiEdit(ops/agent-harness"))
        for entry in maintenance_allow
    )

    launcher = (REPO_ROOT / "scripts/claude-maintenance").read_text(encoding="utf-8")
    assert '--settings "$ROOT/.claude/settings.maintenance.json"' in launcher


def test_local_claude_settings_are_excluded_from_project_artifacts() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".claude/settings.local.json" in gitignore


def test_maintenance_worker_registry_matches_the_supported_route() -> None:
    actual = {
        agent.name: (agent.tools, agent.permission_mode)
        for agent in AGENTS
        if agent.kind == "maintenance-worker"
    }
    assert actual == EXPECTED_MAINTENANCE_WORKERS

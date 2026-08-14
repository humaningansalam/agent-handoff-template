from __future__ import annotations

import json
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]


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
    result = subprocess.run(
        ["python3", "tools/render_agent_adapters.py", "--check"],
        cwd=REPO_ROOT,
        text=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        check=False,
    )

    assert result.returncode == 0, result.stdout + result.stderr

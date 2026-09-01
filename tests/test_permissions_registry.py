from __future__ import annotations

import json
from pathlib import Path


REPO_ROOT = Path(__file__).resolve().parents[1]


def test_claude_settings_keep_project_security_boundaries() -> None:
    settings = json.loads((REPO_ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    permissions = settings["permissions"]

    assert {
        "Bash(git *)",
        "Bash(rg *)",
        "Bash(python *)",
        "Bash(python3 *)",
    }.isdisjoint(permissions["allow"])
    assert {
        "Bash(rm *)",
        "Bash(curl * | *)",
        "Bash(wget * | *)",
    }.issubset(permissions["deny"])


def test_local_claude_settings_are_ignored() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")

    assert ".claude/settings.local.json" in gitignore

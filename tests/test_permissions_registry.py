from __future__ import annotations

import json
from pathlib import Path

from tools.registries.agent_registry import AGENTS, AGENTS_BY_NAME


REPO_ROOT = Path(__file__).resolve().parents[1]
AGENTS_DIR = REPO_ROOT / ".claude" / "agents"
SKILLS_DIR = REPO_ROOT / ".claude" / "skills"

EXPECTED_MAINTENANCE_WORKERS = {
    "maintenance-cartographer": (("Read", "Grep", "Glob"), "plan"),
    "maintenance-planner": (("Read", "Grep", "Glob"), "plan"),
    "maintenance-plan-critic": (("Read", "Grep", "Glob"), "plan"),
    "maintenance-implementer": (("Read", "Grep", "Glob", "Edit", "MultiEdit", "Write"), "default"),
    "maintenance-evaluator": (("Read", "Grep", "Glob", "Bash"), "plan"),
    "maintenance-skeptic": (("Read", "Grep", "Glob", "Bash"), "plan"),
}


def agent_frontmatter(agent_name: str) -> dict[str, object]:
    text = (AGENTS_DIR / f"{agent_name}.md").read_text(encoding="utf-8")
    return _frontmatter(text)


def skill_frontmatter(skill_name: str) -> dict[str, object]:
    text = (SKILLS_DIR / skill_name / "SKILL.md").read_text(encoding="utf-8")
    return _frontmatter(text)


def _frontmatter(text: str) -> dict[str, object]:
    marker = "---"
    assert text.startswith(marker)
    body = text.split(marker, 2)[1]
    parsed: dict[str, object] = {}
    current_list: str | None = None
    for raw_line in body.splitlines():
        line = raw_line.rstrip()
        if not line.strip():
            continue
        if line.startswith("  - ") and current_list:
            parsed.setdefault(current_list, [])
            assert isinstance(parsed[current_list], list)
            parsed[current_list].append(line[4:].strip())
            continue
        current_list = None
        if ":" not in line:
            continue
        key, value = line.split(":", 1)
        key = key.strip()
        value = value.strip().strip('"')
        if value:
            parsed[key] = value
        else:
            parsed[key] = []
            current_list = key
    return parsed


def _tool_names_from_frontmatter(value: object) -> tuple[str, ...]:
    if isinstance(value, str):
        return tuple(part.strip() for part in value.split(",") if part.strip())
    if isinstance(value, list):
        return tuple(str(part).strip() for part in value if str(part).strip())
    return ()


def test_project_settings_do_not_allow_stale_or_broad_commands() -> None:
    settings = json.loads((REPO_ROOT / ".claude/settings.json").read_text(encoding="utf-8"))
    permissions = settings.get("permissions", {}).get("allow", [])

    forbidden_fragments = [
        "/tmp/test/agent-trading-lab_3",
        "maintenance-improve-loop",
        "Bash(git *)",
        "Bash(rg *)",
        "Bash(python *)",
        "Bash(python3 *)",
    ]
    for permission in permissions:
        for fragment in forbidden_fragments:
            assert fragment not in permission


def test_local_claude_settings_are_excluded_from_project_artifacts() -> None:
    gitignore = (REPO_ROOT / ".gitignore").read_text(encoding="utf-8")
    assert ".claude/settings.local.json" in gitignore


def test_settings_allow_safe_writer_but_not_direct_agent_harness_artifact_writes() -> None:
    settings = json.loads((REPO_ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
    allowed = settings["permissions"]["allow"]

    assert "Bash(uv run pytest *)" in allowed
    assert "Bash(uv run python -m tools.agent_harness.safe_artifact_writer write *)" in allowed
    assert not any(
        entry.startswith((
            "Write(/ops/agent-harness",
            "Write(ops/agent-harness",
            "Edit(/ops/agent-harness",
            "Edit(ops/agent-harness",
            "MultiEdit(/ops/agent-harness",
            "MultiEdit(ops/agent-harness",
        ))
        for entry in allowed
    )


def test_maintenance_worker_registry_matches_contract() -> None:
    actual = {
        agent.name: (agent.tools, agent.permission_mode)
        for agent in AGENTS
        if agent.kind == "maintenance-worker"
    }
    assert actual == EXPECTED_MAINTENANCE_WORKERS


def test_agent_frontmatter_matches_registry() -> None:
    for agent in AGENTS:
        frontmatter = agent_frontmatter(agent.name)
        assert tuple(_tool_names_from_frontmatter(frontmatter.get("tools"))) == agent.tools
        assert frontmatter.get("permissionMode", "default") == agent.permission_mode


def test_maintenance_implementer_has_no_bash() -> None:
    assert "Bash" not in AGENTS_BY_NAME["maintenance-implementer"].tools


def test_only_evaluator_and_skeptic_have_maintenance_bash() -> None:
    bash_workers = {agent.name for agent in AGENTS if "Bash" in agent.tools}
    assert bash_workers == {"maintenance-evaluator", "maintenance-skeptic"}


def test_maintenance_skill_frontmatter_uses_safe_writer_without_generic_write() -> None:
    frontmatter = skill_frontmatter("maintenance-workflow")
    allowed_tools = tuple(frontmatter.get("allowed-tools") or ())
    disallowed_tools = tuple(frontmatter.get("disallowed-tools") or ())

    for agent_name in EXPECTED_MAINTENANCE_WORKERS:
        assert f"Agent({agent_name})" in allowed_tools
    assert "Read" in allowed_tools
    assert "Bash(uv run python -m tools.agent_harness.safe_artifact_writer write *)" in allowed_tools
    assert "Bash(uv run pytest *)" in allowed_tools
    assert "Write" not in allowed_tools
    assert "Edit" not in allowed_tools
    assert "Bash" not in allowed_tools
    assert "Skill" in disallowed_tools

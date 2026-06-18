"""Behavior sentinels for the maintenance-workflow entrypoint contract."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude/skills/maintenance-workflow/SKILL.md"
CONTRACT = ROOT / "docs" / "MAINTENANCE_HARNESS_CONTRACT.md"
AGENT_DIR = ROOT / ".claude" / "agents"
SETTINGS = ROOT / ".claude" / "settings.maintenance.json"
DEFAULT_SETTINGS = ROOT / ".claude" / "settings.json"


def text(path: Path) -> str:
    return path.read_text(encoding="utf-8")


def frontmatter(path: Path) -> str:
    body = text(path)
    return body[: body.index("---", 3)]


def test_skill_is_short_direct_native_loop_entrypoint() -> None:
    skill = text(SKILL)
    assert len(skill.splitlines()) <= 90
    assert "disable-model-invocation: true" in frontmatter(SKILL)
    assert "`/r`" in skill
    assert "safe_artifact_writer" in skill


def test_skill_frontmatter_does_not_expose_runner_or_broad_tools() -> None:
    fm = frontmatter(SKILL)
    for agent in (
        "maintenance-cartographer",
        "maintenance-planner",
        "maintenance-plan-critic",
        "maintenance-implementer",
        "maintenance-evaluator",
        "maintenance-skeptic",
    ):
        assert f"Agent({agent})" in fm
    assert "  - Bash\n" not in fm
    assert "  - Write\n" in fm
    assert "  - Edit\n" not in fm
    assert "tools.agent_harness.safe_artifact_writer write" in fm
    assert "TaskCreate" in fm and "TaskUpdate" in fm and "TaskList" in fm


def test_settings_allow_artifact_writes_but_not_phase_helper_driver() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    allow = settings["permissions"]["allow"]
    assert "Write(/ops/agent-harness/**)" in allow
    assert "Edit(/ops/agent-harness/**)" in allow
    assert "Bash(uv run pytest *)" in allow
    assert "Bash(uv run python -m tools.agent_harness.safe_artifact_writer write *)" in allow
    assert not any(rule == "Write(.claude/**)" for rule in allow)


def test_maintenance_tests_do_not_depend_on_operation_command_surfaces() -> None:
    maintenance_tests = [path for path in sorted((ROOT / "tests" / "maintenance").glob("test_*.py")) if path != Path(__file__).resolve()]
    assert maintenance_tests
    combined = "\n".join(path.read_text(encoding="utf-8") for path in maintenance_tests)
    assert "research" "-ops" not in combined
    assert "Research" "Ops" not in combined
    assert "backup" "-wiki" not in combined
    assert "ingest" "-run" not in combined


def test_agents_remain_phase_workers_not_final_deciders() -> None:
    for path in AGENT_DIR.glob("maintenance-*.md"):
        body = text(path)
        assert 35 <= len(body.splitlines()) <= 90, path


def _existing_maintenance_docs() -> list[Path]:
    candidates: list[Path] = [SKILL, CONTRACT]
    candidates.extend(sorted(AGENT_DIR.glob("maintenance-*.md")))
    for base, pattern in ((ROOT / ".agents/skills", "maintenance-workflow/SKILL.md"), (ROOT / ".codex/agents", "maintenance-*.toml"), (ROOT / "ai/roles", "maintenance-*.yaml")):
        if base.exists():
            candidates.extend(sorted(base.glob(pattern)))
    return [path for path in candidates if path.exists()]


def test_contract_profile_matrix_matches_harness_sources() -> None:
    from tools.agent_harness.harness import MaintenanceHarness

    contract = text(CONTRACT)
    assert "## Profile Matrix" in contract
    assert MaintenanceHarness.mandatory_workers_for_profile("TINY_DOC") == ("maintenance-planner", "maintenance-implementer")
    assert "maintenance-plan-critic" not in MaintenanceHarness.mandatory_workers_for_profile("TINY_DOC")
    assert "maintenance-evaluator" not in MaintenanceHarness.mandatory_workers_for_profile("TINY_DOC")
    assert "maintenance-skeptic" not in MaintenanceHarness.mandatory_workers_for_profile("TINY_DOC")
    assert "maintenance-skeptic" not in MaintenanceHarness.mandatory_workers_for_profile("STANDARD")
    assert "maintenance-skeptic" in MaintenanceHarness.mandatory_workers_for_profile("CRITICAL_HARNESS")

    tiny_artifacts = set(MaintenanceHarness.required_pass_artifact_paths_for_profile("TINY_DOC"))
    assert tiny_artifacts == {
        "ops/agent-harness/current-run-state.json",
        "ops/agent-harness/evidence/plan.json",
        "ops/agent-harness/evidence/execution.json",
        "ops/agent-harness/evidence/execution-review.json",
    }
    for token in ("TINY_DOC", "STANDARD", "CRITICAL_HARNESS", "host-verifier"):
        assert token in contract


def test_contract_hook_manifest_mentions_configured_hook_events() -> None:
    contract = text(CONTRACT)
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    configured_events = set(settings.get("hooks", {}))
    assert "## Hook Manifest" in contract
    assert configured_events <= {
        "SessionStart",
        "UserPromptExpansion",
        "UserPromptSubmit",
        "PreToolUse",
        "PostToolUse",
        "PermissionRequest",
        "SubagentStart",
        "SubagentStop",
        "Stop",
    }
    for event in configured_events:
        assert f"`{event}`" in contract


def test_default_claude_settings_load_maintenance_hooks() -> None:
    settings = json.loads(DEFAULT_SETTINGS.read_text(encoding="utf-8"))
    hooks = settings.get("hooks", {})
    for event in ("UserPromptSubmit", "PreToolUse", "SubagentStop", "Stop"):
        assert event in hooks
    assert "maintenance/enforce_scope.sh" in json.dumps(hooks)
    assert "maintenance/enforce_final_report.sh" in json.dumps(hooks)


def test_safe_writer_documented_flags_are_structured_argparse_flags() -> None:
    import re

    from tools.agent_harness import safe_artifact_writer

    allowed = {
        "--root",
        "--kind",
        "--status",
        "--summary",
        "--blocking-finding",
        "--workflow-id",
        "--candidate-id",
        "--active-candidate-id",
        "--queued-candidate-id",
        "--queue-policy",
        "--affected-surface",
        "--acceptance-criteria-id",
        "--failure-mode-severity",
        "--failure-mode-mapped",
        "--approval-ready",
        "--verification-passed",
        "--revision",
    }
    docs = "\n".join(text(path) for path in _existing_maintenance_docs())
    documented = {flag for flag in re.findall(r"--[a-z][a-z0-9-]*", docs) if flag.startswith("--")}
    assert documented <= allowed

    safe_artifact_writer.parse_args(
        [
            "write",
            "--kind",
            "plan",
            "--status",
            "passed",
            "--summary",
            "ok",
            "--workflow-id",
            "mw-1",
            "--candidate-id",
            "C1",
            "--affected-surface",
            "docs/example.md",
            "--acceptance-criteria-id",
            "AC-001",
        ]
    )

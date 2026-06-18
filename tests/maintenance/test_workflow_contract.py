"""Behavior sentinels for the maintenance-workflow entrypoint contract."""
from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
SKILL = ROOT / ".claude/skills/maintenance-workflow/SKILL.md"
CONTRACT = ROOT / "docs" / "MAINTENANCE_HARNESS_CONTRACT.md"
AGENT_DIR = ROOT / ".claude" / "agents"
SETTINGS = ROOT / ".claude" / "settings.json"


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
    assert "  - Write\n" not in fm
    assert "  - Edit\n" not in fm
    assert "tools.agent_harness.safe_artifact_writer write" in fm
    assert "TaskCreate" in fm and "TaskUpdate" in fm and "TaskList" in fm


def test_settings_allow_safe_writer_but_not_direct_artifact_writes() -> None:
    settings = json.loads(SETTINGS.read_text(encoding="utf-8"))
    allow = settings["permissions"]["allow"]
    assert "Bash(uv run pytest *)" in allow
    assert "Bash(uv run python -m tools.agent_harness.safe_artifact_writer write *)" in allow
    assert not any(
        rule.startswith((
            "Write(/ops/agent-harness",
            "Write(ops/agent-harness",
            "Edit(/ops/agent-harness",
            "Edit(ops/agent-harness",
            "MultiEdit(/ops/agent-harness",
            "MultiEdit(ops/agent-harness",
        ))
        for rule in allow
    )


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
    from tools.agent_harness.checker import _with_policy_required_flags
    from tools.agent_harness.harness import MaintenanceHarness
    from tools.agent_harness.policy import VerificationMode, WorkflowProfile, policy_for_surfaces

    contract = text(CONTRACT)
    assert "## Surface Class Matrix" in contract
    assert "## Route Matrix" in contract
    assert MaintenanceHarness.mandatory_workers_for_profile("TINY_DOC") == ("maintenance-planner", "maintenance-implementer")
    assert "maintenance-plan-critic" not in MaintenanceHarness.mandatory_workers_for_profile("TINY_DOC")
    assert "maintenance-evaluator" not in MaintenanceHarness.mandatory_workers_for_profile("TINY_DOC")
    assert "maintenance-skeptic" not in MaintenanceHarness.mandatory_workers_for_profile("TINY_DOC")
    assert "maintenance-cartographer" not in MaintenanceHarness.mandatory_workers_for_profile("STANDARD")
    assert "maintenance-skeptic" not in MaintenanceHarness.mandatory_workers_for_profile("STANDARD")
    assert "maintenance-skeptic" in MaintenanceHarness.mandatory_workers_for_profile("CRITICAL_HARNESS")

    tiny_artifacts = set(MaintenanceHarness.required_pass_artifact_paths_for_profile("TINY_DOC"))
    assert tiny_artifacts == {
        "ops/agent-harness/current-run-state.json",
        "ops/agent-harness/evidence/plan.json",
        "ops/agent-harness/evidence/execution.json",
        "ops/agent-harness/evidence/execution-review.json",
    }
    assert policy_for_surfaces(("README.md",)).profile == WorkflowProfile.TINY_DOC
    assert policy_for_surfaces(("docs/guide.md",)).profile == WorkflowProfile.TINY_DOC
    assert policy_for_surfaces(("CLAUDE.md",)).profile == WorkflowProfile.STANDARD
    assert policy_for_surfaces(("docs/PRD.md",)).profile == WorkflowProfile.STANDARD
    assert policy_for_surfaces((".claude/skills/example/SKILL.md",)).profile == WorkflowProfile.STANDARD
    assert policy_for_surfaces(("docs/MAINTENANCE_HARNESS_CONTRACT.md",)).profile == WorkflowProfile.CRITICAL_HARNESS
    assert policy_for_surfaces((".claude/skills/maintenance-workflow/SKILL.md",)).profile == WorkflowProfile.CRITICAL_HARNESS
    assert policy_for_surfaces((".claude/agents/maintenance-planner.md",)).profile == WorkflowProfile.CRITICAL_HARNESS
    assert policy_for_surfaces(("tools/agent_harness/checker.py",)).profile == WorkflowProfile.CRITICAL_HARNESS
    assert policy_for_surfaces(("tests/maintenance/test_workflow_contract.py",)).profile == WorkflowProfile.CRITICAL_HARNESS
    forbidden = policy_for_surfaces(("repo/src/app.py",))
    assert forbidden.route == ()
    assert forbidden.required_workers == ()
    assert "forbidden surface" in forbidden.reason
    assert policy_for_surfaces(("README.md",), severity="P1").profile == WorkflowProfile.CRITICAL_HARNESS
    semantic_critical = policy_for_surfaces(("docs/MAINTENANCE_HARNESS_CONTRACT.md",), ambiguity=True)
    assert semantic_critical.verification_mode == VerificationMode.SEMANTIC
    assert semantic_critical.route[-1] == "maintenance-skeptic"
    mechanical_critical = policy_for_surfaces(("docs/MAINTENANCE_HARNESS_CONTRACT.md",), ambiguity=True, verification_mode="mechanical")
    assert mechanical_critical.verification_mode == VerificationMode.MECHANICAL
    assert mechanical_critical.profile == WorkflowProfile.CRITICAL_HARNESS
    assert mechanical_critical.route == (
        "maintenance-cartographer",
        "maintenance-planner",
        "maintenance-plan-critic",
        "maintenance-implementer",
        "maintenance-evaluator",
    )
    assert "maintenance-skeptic" not in mechanical_critical.required_workers
    unsafe_mechanical = policy_for_surfaces((".claude/settings.json",), ambiguity=True, verification_mode="mechanical")
    assert unsafe_mechanical.verification_mode == VerificationMode.SEMANTIC
    assert unsafe_mechanical.route[-1] == "maintenance-skeptic"
    standard_ambiguous = policy_for_surfaces(("CLAUDE.md",), ambiguity=True)
    assert "maintenance-cartographer" in standard_ambiguous.required_workers
    assert policy_for_surfaces(("CLAUDE.md",), ambiguity=False).required_workers == MaintenanceHarness.mandatory_workers_for_profile("STANDARD")
    for token in ("LOW_RISK_PROSE", "INSTRUCTION_DOC", "TINY_DOC", "STANDARD", "CRITICAL_HARNESS", "host-verifier"):
        assert token in contract

    normalized = _with_policy_required_flags(
        {
            "maintenance-cartographer": {"required": True, "invoked": True},
            "maintenance-planner": {"required": False, "invoked": True},
            "maintenance-implementer": {"required": False, "invoked": True},
            "host-verifier": {"required": False, "invoked": True},
        },
        policy_for_surfaces(("docs/guide.md",)).route,
    )
    assert normalized["maintenance-cartographer"]["required"] is False
    assert normalized["maintenance-planner"]["required"] is True
    assert normalized["maintenance-implementer"]["required"] is True
    assert normalized["host-verifier"]["required"] is True


def test_checker_pass_eligibility_ignores_stale_state_pass_flags(tmp_path: Path) -> None:
    from tools.agent_harness.checker import _calculated_pass_eligibility

    state = {
        "pass_eligibility": {
            "tests_passed": True,
            "evaluation_pass_candidate": True,
            "calculated": {"tests_passed": True, "evaluation_pass_candidate": True},
        },
        "approval_gate": {"freeze": {"affected_surfaces": ["docs/MAINTENANCE_HARNESS_CONTRACT.md"]}},
    }

    calculated = _calculated_pass_eligibility(
        tmp_path,
        state,
        evidence_paths=set(),
        worker_status={"maintenance-evaluator": {"required": True, "invoked": True, "status": "passed"}},
        state_blockers=[],
    )

    assert calculated["tests_passed"] is False
    assert "tests_not_passed" in calculated["blocked_by"]
    assert "evaluation_not_pass_candidate" in calculated["blocked_by"]


def test_route_cursor_honors_failed_worker_retry_target() -> None:
    from tools.agent_harness.checker import _route_cursor

    passed = {
        "invoked": True,
        "evidence_kind": "structured-json",
        "status": "passed",
        "blocking_findings": [],
        "artifact_path": "ops/agent-harness/evidence/example.json",
        "schema_version": 1,
        "structured_evidence_valid": True,
    }
    cursor = _route_cursor(
        ("maintenance-cartographer", "maintenance-planner", "maintenance-plan-critic", "maintenance-implementer"),
        {
            "maintenance-cartographer": {**passed, "worker": "maintenance-cartographer"},
            "maintenance-planner": {**passed, "worker": "maintenance-planner"},
            "maintenance-plan-critic": {
                "invoked": True,
                "worker": "maintenance-plan-critic",
                "evidence_kind": "structured-json",
                "status": "failed",
                "blocking_findings": ["retry plan"],
                "artifact_path": "ops/agent-harness/evidence/plan-review.json",
                "structured_evidence_valid": True,
                "retry_target": "retry-plan",
            },
        },
        {
            "ops/agent-harness/evidence/cartography.json",
            "ops/agent-harness/evidence/plan.json",
            "ops/agent-harness/evidence/plan-review.json",
        },
        (
            "ops/agent-harness/current-run-state.json",
            "ops/agent-harness/evidence/cartography.json",
            "ops/agent-harness/evidence/plan.json",
            "ops/agent-harness/evidence/plan-review.json",
            "ops/agent-harness/evidence/execution.json",
        ),
        retry_target="retry-plan",
    )

    assert cursor["next_required_worker"] == "maintenance-planner"
    assert cursor["remaining_required_artifacts"] == ["ops/agent-harness/evidence/plan.json"]
    assert cursor["retry_target"] == "retry-plan"


def test_checker_policy_decision_uses_plan_verification_mode(tmp_path: Path) -> None:
    from tools.agent_harness import safe_artifact_writer
    from tools.agent_harness.checker import _policy_decision
    from tools.agent_harness.policy import VerificationMode, WorkflowProfile

    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="cartography",
        status="passed",
        summary="critical doc mechanical change mapped before planning",
        workflow_id="mw-checker-mechanical",
        active_candidate_id="DOCS-001",
    )
    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="plan",
        status="passed",
        summary="mechanical typo plan",
        workflow_id="mw-checker-mechanical",
        candidate_id="DOCS-001",
        affected_surfaces=("docs/MAINTENANCE_HARNESS_CONTRACT.md",),
        acceptance_criteria_ids=("AC-001",),
        verification_mode="mechanical",
    )

    policy = _policy_decision(tmp_path, {})

    assert policy.profile == WorkflowProfile.CRITICAL_HARNESS
    assert policy.verification_mode == VerificationMode.MECHANICAL
    assert policy.required_workers == (
        "maintenance-cartographer",
        "maintenance-planner",
        "maintenance-plan-critic",
        "maintenance-implementer",
        "maintenance-evaluator",
    )


def test_route_cursor_completed_workers_are_sequential() -> None:
    from tools.agent_harness.checker import _route_cursor

    ready = {
        "required": True,
        "invoked": True,
        "evidence_kind": "structured-json",
        "status": "passed",
        "blocking_findings": [],
        "evidence": "ready worker evidence",
        "artifact_path": "ops/agent-harness/evidence/example.json",
        "schema_version": 1,
        "structured_evidence_valid": True,
    }
    cursor = _route_cursor(
        ("maintenance-planner", "maintenance-implementer", "maintenance-evaluator"),
        {
            "maintenance-planner": {**ready, "worker": "maintenance-planner"},
            "maintenance-evaluator": {**ready, "worker": "maintenance-evaluator"},
        },
        {"ops/agent-harness/evidence/plan.json", "ops/agent-harness/evidence/execution-review.json"},
        (
            "ops/agent-harness/current-run-state.json",
            "ops/agent-harness/evidence/plan.json",
            "ops/agent-harness/evidence/execution.json",
            "ops/agent-harness/evidence/execution-review.json",
        ),
    )

    assert cursor["completed_workers"] == ["maintenance-planner"]
    assert cursor["next_required_worker"] == "maintenance-implementer"


def test_route_cursor_orders_remaining_artifacts_by_route() -> None:
    from tools.agent_harness.checker import _route_cursor

    ready = {
        "required": True,
        "invoked": True,
        "evidence_kind": "structured-json",
        "status": "passed",
        "blocking_findings": [],
        "evidence": "ready worker evidence",
        "artifact_path": "ops/agent-harness/evidence/example.json",
        "schema_version": 1,
        "structured_evidence_valid": True,
    }
    cursor = _route_cursor(
        ("maintenance-cartographer", "maintenance-planner", "maintenance-plan-critic", "maintenance-implementer", "maintenance-evaluator"),
        {
            "maintenance-cartographer": {**ready, "worker": "maintenance-cartographer"},
            "maintenance-planner": {**ready, "worker": "maintenance-planner"},
            "maintenance-plan-critic": {**ready, "worker": "maintenance-plan-critic"},
        },
        {
            "ops/agent-harness/evidence/cartography.json",
            "ops/agent-harness/evidence/plan.json",
            "ops/agent-harness/evidence/plan-review.json",
        },
        (
            "ops/agent-harness/current-run-state.json",
            "ops/agent-harness/evidence/cartography.json",
            "ops/agent-harness/evidence/plan.json",
            "ops/agent-harness/evidence/plan-review.json",
            "ops/agent-harness/evidence/execution.json",
            "ops/agent-harness/evidence/execution-review.json",
        ),
    )

    assert cursor["next_required_worker"] == "maintenance-implementer"
    assert cursor["remaining_required_artifacts"] == [
        "ops/agent-harness/evidence/execution.json",
        "ops/agent-harness/evidence/execution-review.json",
    ]


def test_approval_phrase_uses_plan_contract_hash_not_artifact_sha() -> None:
    skill = text(SKILL)
    contract = text(CONTRACT)
    for source in (skill, contract):
        assert "approval_phrase" in source
        assert "plan_contract_hash[:12]" in source
        assert "plan_sha256" in source
        assert "8-character" in source


def test_pass_gate_rejects_approval_contract_drift(tmp_path: Path) -> None:
    from tools.agent_harness.checker import _approval_hash_ok
    from tools.runtime.json_io import write_json_atomic_under_root

    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "latest-plan-metadata.json",
        {"schema_version": 1, "plan_contract_hash": "b" * 64},
        tmp_path,
    )
    state = {
        "approval_gate": {
            "status": "approved-frozen",
            "freeze": {"plan_contract_hash": "a" * 64, "approval_hash": "present"},
        }
    }

    assert _approval_hash_ok(tmp_path, state) is False


def test_pass_gate_recomputes_post_approval_dirty_scope(tmp_path: Path) -> None:
    from tools.agent_harness.checker import _changed_files_within_approval

    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "approved.md").write_text("approved", encoding="utf-8")
    (tmp_path / "unapproved.py").write_text("dirty", encoding="utf-8")
    state = {
        "changed_files": ["docs/approved.md"],
        "approval_gate": {
            "freeze": {
                "affected_surfaces": ["docs/approved.md"],
                "pre_existing_dirty_files": [],
            }
        },
    }

    assert _changed_files_within_approval(tmp_path, state) is False


def test_pass_gate_allows_unchanged_pre_existing_dirty_baseline(tmp_path: Path) -> None:
    from tools.agent_harness.checker import _changed_files_within_approval, _dirty_fingerprint

    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "approved.md").write_text("approved", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("pre-existing dirty", encoding="utf-8")
    state = {
        "changed_files": ["docs/approved.md"],
        "approval_gate": {
            "freeze": {
                "affected_surfaces": ["docs/approved.md"],
                "pre_existing_dirty_files": ["unrelated.py"],
                "pre_existing_dirty_fingerprints": {"unrelated.py": _dirty_fingerprint(tmp_path, "unrelated.py")},
            }
        },
    }

    assert _changed_files_within_approval(tmp_path, state) is True


def test_pass_gate_rejects_empty_candidate_change_set(tmp_path: Path) -> None:
    from tools.agent_harness.checker import _changed_files_within_approval

    state = {
        "changed_files": [],
        "approval_gate": {"freeze": {"affected_surfaces": ["docs/approved.md"], "pre_existing_dirty_files": []}},
    }

    assert _changed_files_within_approval(tmp_path, state) is False


def test_pass_gate_rejects_stale_changed_file_without_git_diff(tmp_path: Path) -> None:
    from tools.agent_harness.checker import _changed_files_within_approval

    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "approved.md").write_text("approved", encoding="utf-8")
    subprocess.run(["git", "add", "docs/approved.md"], cwd=tmp_path, check=True, capture_output=True)
    subprocess.run(
        ["git", "-c", "user.email=test@example.com", "-c", "user.name=Test", "commit", "-m", "baseline"],
        cwd=tmp_path,
        check=True,
        capture_output=True,
    )
    state = {
        "changed_files": ["docs/approved.md"],
        "approval_gate": {"freeze": {"affected_surfaces": ["docs/approved.md"], "pre_existing_dirty_files": []}},
    }

    assert _changed_files_within_approval(tmp_path, state) is False


def test_pass_gate_rejects_modified_pre_existing_dirty_baseline(tmp_path: Path) -> None:
    from tools.agent_harness.checker import _changed_files_within_approval, _dirty_fingerprint

    import subprocess

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs" / "approved.md").write_text("approved", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("pre-existing dirty", encoding="utf-8")
    baseline_hash = _dirty_fingerprint(tmp_path, "unrelated.py")
    (tmp_path / "unrelated.py").write_text("changed after approval", encoding="utf-8")
    state = {
        "changed_files": ["docs/approved.md"],
        "approval_gate": {
            "freeze": {
                "affected_surfaces": ["docs/approved.md"],
                "pre_existing_dirty_files": ["unrelated.py"],
                "pre_existing_dirty_fingerprints": {"unrelated.py": baseline_hash},
            }
        },
    }

    assert _changed_files_within_approval(tmp_path, state) is False


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


def test_safe_writer_documented_flags_are_structured_argparse_flags() -> None:
    import re

    from tools.agent_harness import safe_artifact_writer

    allowed = {
        "--root",
        "--kind",
        "--status",
        "--summary",
        "--blocking-finding",
        "--finding-id",
        "--finding-surface",
        "--finding-expected",
        "--finding-observed",
        "--finding-verdict",
        "--finding-severity",
        "--retry-target",
        "--checked-command",
        "--checked-surface",
        "--evidence-ref",
        "--workflow-id",
        "--candidate-id",
        "--active-candidate-id",
        "--queued-candidate-id",
        "--queue-policy",
        "--affected-surface",
        "--acceptance-criteria-id",
        "--failure-mode-severity",
            "--failure-mode-mapped",
            "--verification-mode",
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

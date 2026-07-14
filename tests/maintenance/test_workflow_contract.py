from __future__ import annotations

import subprocess
from pathlib import Path


def test_policy_routes_supported_surfaces_through_structured_profiles() -> None:
    from tools.agent_harness.harness import MaintenanceHarness
    from tools.agent_harness.policy import VerificationMode, WorkflowProfile, policy_for_surfaces

    tiny = policy_for_surfaces(("README.md",))
    standard = policy_for_surfaces(("CLAUDE.md",))
    critical = policy_for_surfaces(("docs/MAINTENANCE_HARNESS_CONTRACT.md",), ambiguity=True)
    forbidden = policy_for_surfaces(("repos/src/app.py",))

    assert tiny.profile == WorkflowProfile.TINY_DOC
    assert tiny.required_workers == MaintenanceHarness.mandatory_workers_for_profile("TINY_DOC")
    assert standard.profile == WorkflowProfile.STANDARD
    assert critical.profile == WorkflowProfile.CRITICAL_HARNESS
    assert critical.verification_mode == VerificationMode.SEMANTIC
    assert critical.route[-1] == "maintenance-skeptic"
    assert forbidden.route == ()
    assert forbidden.required_workers == ()

    mechanical = policy_for_surfaces(
        ("docs/MAINTENANCE_HARNESS_CONTRACT.md",),
        ambiguity=True,
        verification_mode="mechanical",
    )
    assert mechanical.verification_mode == VerificationMode.MECHANICAL
    assert "maintenance-skeptic" not in mechanical.required_workers

    unsafe_mechanical = policy_for_surfaces(
        (".claude/settings.json",),
        ambiguity=True,
        verification_mode="mechanical",
    )
    assert unsafe_mechanical.verification_mode == VerificationMode.SEMANTIC
    assert unsafe_mechanical.route[-1] == "maintenance-skeptic"


def test_checker_recomputes_pass_eligibility_from_current_evidence(tmp_path: Path) -> None:
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
    assert set(calculated["blocked_by"]) >= {"tests_not_passed", "evaluation_not_pass_candidate"}


def test_route_cursor_uses_retry_target_and_sequential_completion() -> None:
    from tools.agent_harness.checker import _route_cursor

    passed = {
        "required": True,
        "invoked": True,
        "evidence_kind": "structured-json",
        "status": "passed",
        "blocking_findings": [],
        "artifact_path": "ops/agent-harness/evidence/example.json",
        "schema_version": 1,
        "structured_evidence_valid": True,
    }
    route = (
        "maintenance-cartographer",
        "maintenance-planner",
        "maintenance-plan-critic",
        "maintenance-implementer",
    )
    cursor = _route_cursor(
        route,
        {
            "maintenance-cartographer": {**passed, "worker": "maintenance-cartographer"},
            "maintenance-planner": {**passed, "worker": "maintenance-planner"},
            "maintenance-plan-critic": {
                **passed,
                "worker": "maintenance-plan-critic",
                "status": "failed",
                "blocking_findings": ["retry plan"],
                "retry_target": "retry-plan",
            },
            "maintenance-implementer": {**passed, "worker": "maintenance-implementer"},
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
    assert cursor["completed_workers"] == []
    assert cursor["remaining_required_artifacts"] == ["ops/agent-harness/evidence/plan.json"]


def test_checker_uses_the_plan_verification_mode(tmp_path: Path) -> None:
    from tools.agent_harness import safe_artifact_writer
    from tools.agent_harness.checker import _policy_decision
    from tools.agent_harness.policy import VerificationMode, WorkflowProfile

    safe_artifact_writer.write_artifact(
        tmp_path,
        kind="cartography",
        status="passed",
        summary="critical doc mapped",
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
    assert policy.required_workers[-1] == "maintenance-evaluator"
    assert "maintenance-skeptic" not in policy.required_workers


def test_pass_gate_binds_approval_hash_and_dirty_scope(tmp_path: Path) -> None:
    from tools.agent_harness.checker import _approval_hash_ok, _changed_files_within_approval, _dirty_fingerprint
    from tools.runtime.json_io import write_json_atomic_under_root

    write_json_atomic_under_root(
        tmp_path / "ops/agent-harness/latest-plan-metadata.json",
        {"schema_version": 1, "plan_contract_hash": "b" * 64},
        tmp_path,
    )
    drifted = {
        "approval_gate": {
            "status": "approved-frozen",
            "freeze": {"plan_contract_hash": "a" * 64, "approval_hash": "present"},
        }
    }
    assert _approval_hash_ok(tmp_path, drifted) is False

    subprocess.run(["git", "init"], cwd=tmp_path, check=True, capture_output=True)
    (tmp_path / "docs").mkdir()
    (tmp_path / "docs/approved.md").write_text("approved", encoding="utf-8")
    (tmp_path / "unrelated.py").write_text("pre-existing dirty", encoding="utf-8")
    baseline = _dirty_fingerprint(tmp_path, "unrelated.py")
    state = {
        "changed_files": ["docs/approved.md"],
        "approval_gate": {
            "freeze": {
                "affected_surfaces": ["docs/approved.md"],
                "pre_existing_dirty_files": ["unrelated.py"],
                "pre_existing_dirty_fingerprints": {"unrelated.py": baseline},
            }
        },
    }

    assert _changed_files_within_approval(tmp_path, state) is True
    (tmp_path / "unrelated.py").write_text("changed after approval", encoding="utf-8")
    assert _changed_files_within_approval(tmp_path, state) is False

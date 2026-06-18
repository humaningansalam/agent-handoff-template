from __future__ import annotations


def test_retry_policy_does_not_use_worker_event_completion() -> None:
    from tools.agent_harness import retry_policy

    assert not hasattr(retry_policy, "worker_stopped_after_latest")
    assert not hasattr(retry_policy, "latest_worker_stop_time")


def test_retry_evaluation_review_worker_follows_profile_route(tmp_path) -> None:
    from tools.agent_harness.retry_policy import retry_agent_start_block_reason

    standard_state = {
        "retry": {"target": "retry-evaluation"},
        "pass_eligibility": {"calculated": {"workflow_path": "STANDARD"}},
        "worker_status": {
            "maintenance-evaluator": {
                "invoked": True,
                "worker": "maintenance-evaluator",
                "evidence_kind": "structured-json",
                "status": "passed",
                "blocking_findings": [],
                "artifact_path": "ops/agent-harness/evidence/execution-review.json",
                "structured_evidence_valid": True,
            }
        },
    }
    critical_state = {
        **standard_state,
        "pass_eligibility": {"calculated": {"workflow_path": "CRITICAL_HARNESS"}},
    }

    assert "blocked maintenance-skeptic" in retry_agent_start_block_reason(tmp_path, standard_state, "maintenance-skeptic")
    assert retry_agent_start_block_reason(tmp_path, critical_state, "maintenance-skeptic") == ""


def test_retry_evaluation_tiny_doc_uses_host_verifier_artifact_not_agent(tmp_path) -> None:
    from tools.agent_harness.retry_policy import retry_agent_start_block_reason, retry_artifact_write_block_reason

    state = {
        "retry": {"target": "retry-evaluation"},
        "pass_eligibility": {"calculated": {"workflow_path": "TINY_DOC"}},
        "worker_status": {
            "host-verifier": {
                "invoked": True,
                "worker": "host-verifier",
                "evidence_kind": "structured-json",
                "status": "passed",
                "blocking_findings": [],
                "artifact_path": "ops/agent-harness/evidence/execution-review.json",
                "structured_evidence_valid": True,
            }
        },
    }

    assert "blocked maintenance-evaluator" in retry_agent_start_block_reason(tmp_path, state, "maintenance-evaluator")
    assert retry_artifact_write_block_reason(tmp_path, state, "execution-review") == ""


def test_retry_policy_uses_state_policy_route_before_profile_default(tmp_path) -> None:
    from tools.agent_harness.retry_policy import retry_agent_start_block_reason

    state = {
        "retry": {"target": "retry-plan"},
        "pass_eligibility": {
            "calculated": {"workflow_path": "STANDARD"},
            "workflow_profile": {
                "path": "STANDARD",
                "route": ["maintenance-cartographer", "maintenance-planner", "maintenance-plan-critic", "maintenance-implementer", "maintenance-evaluator"],
            },
        },
        "worker_status": {
            "maintenance-planner": {
                "invoked": True,
                "worker": "maintenance-planner",
                "evidence_kind": "structured-json",
                "status": "passed",
                "blocking_findings": [],
                "artifact_path": "ops/agent-harness/evidence/plan.json",
                "structured_evidence_valid": True,
            }
        },
    }

    assert retry_agent_start_block_reason(tmp_path, state, "maintenance-plan-critic") == ""


def test_typed_retry_targets_narrow_to_expected_worker_and_artifact(tmp_path) -> None:
    from tools.agent_harness.retry_policy import retry_agent_start_block_reason, retry_artifact_write_block_reason

    state = {
        "retry": {"target": "retry-approval-metadata"},
        "pass_eligibility": {
            "workflow_profile": {
                "route": ["maintenance-planner", "maintenance-plan-critic", "maintenance-implementer", "maintenance-evaluator"],
            },
            "calculated": {"workflow_path": "STANDARD"},
        },
        "worker_status": {},
    }

    assert retry_agent_start_block_reason(tmp_path, state, "maintenance-planner") == ""
    assert "blocked maintenance-implementer" in retry_agent_start_block_reason(tmp_path, state, "maintenance-implementer")
    assert retry_artifact_write_block_reason(tmp_path, state, "plan") == "maintenance retry route retry-approval-metadata requires maintenance-planner before writing plan."
    assert "blocked execution" in retry_artifact_write_block_reason(tmp_path, state, "execution")

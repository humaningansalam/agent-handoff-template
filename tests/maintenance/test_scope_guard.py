from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))



class TestMaintenanceScopeGuardContract:
    def _matcher_tools(self, matcher):
        return set(str(matcher).split("|")) if matcher else set()


    def test_maintenance_scope_guard_blocks_wrong_agent_during_retry_route(self, tmp_path, monkeypatch, capsys):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-agent-retry-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs polish")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": f"mw-{session_id}",
                "phase": "plan_reviewed",
                "retry": {"target": "retry-plan", "blockers": ["needs planner"]},
                "pass_eligibility": {"calculated": {"eligible": False}},
            },
            tmp_path,
        )
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Agent",
                        "session_id": session_id,
                        "tool_input": {"agent_type": "maintenance-evaluator"},
                    }
                )
            ),
        )

        enforce_maintenance_scope.main()

        output = json.loads(capsys.readouterr().out)
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"

    def test_maintenance_scope_guard_allows_expected_agent_during_retry_route(self, tmp_path, monkeypatch, capsys):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-agent-retry-allow-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs polish")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": f"mw-{session_id}",
                "phase": "plan_reviewed",
                "retry": {"target": "retry-plan", "blockers": ["needs planner"]},
                "pass_eligibility": {"calculated": {"eligible": False}},
            },
            tmp_path,
        )
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Agent",
                        "session_id": session_id,
                        "tool_input": {"agent_type": "maintenance-planner"},
                    }
                )
            ),
        )

        enforce_maintenance_scope.main()

        assert capsys.readouterr().out == ""



    def test_maintenance_scope_guard_blocks_nested_skill_invocation(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker

        session_id = "maintenance-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs")
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Skill",
                        "session_id": session_id,
                        "tool_input": {"skill_name": "superpowers:systematic-debugging"},
                    }
                )
            ),
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        enforce_maintenance_scope.main()

        decision = json.loads(captured.getvalue())["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"

    @pytest.mark.parametrize(
        ("tool_name", "relative_path", "prompt"),
        [
            pytest.param("Read", "repos/src/state.md", "/maintenance-workflow scope guard", id="repos-read"),
            pytest.param("Write", "ops/agent-harness/evidence/plan.json", "/maintenance-workflow improve trace", id="artifact-write"),
            pytest.param("Write", "tools/maintenance/maintenance_harness.py", "/maintenance-workflow improve trace", id="repo-write"),
        ],
    )
    def test_maintenance_scope_guard_blocks_unapproved_file_operations(
        self,
        tmp_path,
        monkeypatch,
        tool_name,
        relative_path,
        prompt,
    ):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker

        session_id = "maintenance-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt=prompt)
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": tool_name,
                        "session_id": session_id,
                        "tool_input": {"file_path": str(tmp_path / relative_path)},
                    }
                )
            ),
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        enforce_maintenance_scope.main()

        decision = json.loads(captured.getvalue())["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"

    def test_maintenance_scope_guard_blocks_stale_run_artifact_read(self, tmp_path, monkeypatch, capsys):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker

        session_id = "maintenance-stale-run-read-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs polish")
        stale_artifact = tmp_path / "ops" / "agent-harness" / "runs" / "old-workflow" / "candidates" / "DOCS-1" / "r001-plan.json"
        stale_artifact.parent.mkdir(parents=True)
        stale_artifact.write_text("stale plan\n", encoding="utf-8")
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Read",
                        "session_id": session_id,
                        "tool_input": {"file_path": str(stale_artifact)},
                    }
                )
            ),
        )

        enforce_maintenance_scope.main()

        output = json.loads(capsys.readouterr().out)
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"


    def test_maintenance_scope_guard_allows_current_run_artifact_read(self, tmp_path, monkeypatch, capsys):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker

        session_id = "maintenance-current-run-read-session"
        marker = write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs polish")
        artifact = tmp_path / "ops" / "agent-harness" / "runs" / marker["workflow_id"] / "candidates" / "DOCS-1" / "r001-plan.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("current plan\n", encoding="utf-8")
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Read",
                        "session_id": session_id,
                        "tool_input": {"file_path": str(artifact)},
                    }
                )
            ),
        )

        enforce_maintenance_scope.main()

        assert capsys.readouterr().out == ""



    def test_maintenance_scope_guard_blocks_manual_state_write(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker

        harness_root = tmp_path / "ops" / "agent-harness"
        harness_root.mkdir(parents=True)
        (harness_root / "evidence").mkdir()
        (harness_root / "evidence/cartography.json").write_text("# Cartography\n", encoding="utf-8")
        session_id = "maintenance-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow improve trace")
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        stale_state = {
            "schema_version": 1,
            "workflow_id": "mw-session",
            "phase": "intake",
            "active_candidate_id": "",
            "queued_candidate_ids": [],
            "terminal_candidate": True,
            "approval_gate": {"status": "not-ready", "requires_human_approval": False, "approval_evidence_recorded": False, "freeze": {}},
            "retry": {"target": "", "blockers": []},
            "artifacts": [],
            "latest_event": {},
            "failure_mode_ledger": {"required": False, "severity": "P3", "full_replay_required": False, "mapped": True, "direct_evidence": True},
            "pass_eligibility": {"eligible": False, "blocked_by": ["mandatory worker evidence pending"]},
            "worker_status": {},
        }
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Write",
                        "session_id": session_id,
                        "tool_input": {
                            "file_path": str(harness_root / "current-run-state.json"),
                            "content": json.dumps(stale_state),
                        },
                    }
                )
            ),
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        enforce_maintenance_scope.main()

        decision = json.loads(captured.getvalue())["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"


    def test_maintenance_scope_guard_blocks_state_phase_regression_after_execution_evidence(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker

        harness_root = tmp_path / "ops" / "agent-harness"
        harness_root.mkdir(parents=True)
        (harness_root / "evidence").mkdir()
        for name in ("evidence/cartography.json", "evidence/plan.json", "evidence/plan-review.json", "evidence/execution.json"):
            (harness_root / name).write_text(f"# {name}\n", encoding="utf-8")
        session_id = "maintenance-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow improve trace")
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        regressed_state = {
            "schema_version": 1,
            "workflow_id": "mw-session",
            "phase": "plan_reviewed",
            "active_candidate_id": "O1",
            "queued_candidate_ids": [],
            "terminal_candidate": True,
            "approval_gate": {"status": "not-ready", "requires_human_approval": False, "approval_evidence_recorded": False, "freeze": {}},
            "retry": {"target": "", "blockers": []},
            "artifacts": [
                {
                    "path": f"ops/agent-harness/{name}",
                    "canonical_path": f"ops/agent-harness/runs/mw-session/candidates/O1/r001-{name}",
                    "workflow_id": "mw-session",
                    "candidate_id": "O1" if name != "evidence/cartography.json" else "",
                    "phase": "plan_reviewed",
                    "revision": 1,
                }
                for name in ("evidence/cartography.json", "evidence/plan.json", "evidence/plan-review.json", "evidence/execution.json")
            ],
            "latest_event": {},
            "failure_mode_ledger": {"required": False, "severity": "P3", "full_replay_required": False, "mapped": True, "direct_evidence": True},
            "pass_eligibility": {"eligible": False, "blocked_by": ["mandatory worker evidence pending"]},
            "worker_status": {},
        }
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Write",
                        "session_id": session_id,
                        "tool_input": {
                            "file_path": str(harness_root / "current-run-state.json"),
                            "content": json.dumps(regressed_state),
                        },
                    }
                )
            ),
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        enforce_maintenance_scope.main()

        decision = json.loads(captured.getvalue())["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"


    def test_maintenance_scope_guard_allows_repo_edit_after_approval_freeze(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs consistency")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": f"mw-{session_id}",
                "phase": "approved_frozen",
                "approval_gate": {
                    "status": "approved-frozen",
                    "freeze": {"affected_surfaces": ["docs/PRD.md"]},
                },
            },
            tmp_path,
        )
        target = tmp_path / "docs" / "PRD.md"
        target.parent.mkdir(parents=True)
        target.write_text("old", encoding="utf-8")
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Edit",
                        "session_id": session_id,
                        "tool_input": {"file_path": str(target)},
                    }
                )
            ),
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        enforce_maintenance_scope.main()

        decision = json.loads(captured.getvalue())["hookSpecificOutput"]
        assert decision["permissionDecision"] == "allow"




    def test_implementer_budget_blocks_non_converging_edits(self, tmp_path, monkeypatch, capsys):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import append_jsonl_atomic_under_root, write_json_atomic_under_root

        session_id = "maintenance-budget-session"
        workflow_id = f"mw-{session_id}"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow critical")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": workflow_id,
                "phase": "approved_frozen",
                "approval_gate": {"status": "approved-frozen", "freeze": {"affected_surfaces": ["README.md"]}},
                "pass_eligibility": {"calculated": {"eligible": False}},
            },
            tmp_path,
        )
        for index in range(10):
            append_jsonl_atomic_under_root(
                tmp_path / "ops" / "agent-harness" / "latest-events.jsonl",
                {
                    "captured_at": f"2026-06-17T17:00:{index:02d}.000000Z",
                    "workflow_id": workflow_id,
                    "event": "pre_tool",
                    "agent_type": "maintenance-implementer",
                    "tool_name": "Edit",
                },
                tmp_path,
            )
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Edit",
                        "agent_type": "maintenance-implementer",
                        "session_id": session_id,
                        "tool_input": {"file_path": "README.md"},
                    }
                )
            ),
        )

        enforce_maintenance_scope.main()

        output = json.loads(capsys.readouterr().out)
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"


    def test_maintenance_scope_guard_denies_repo_edit_outside_approved_surface(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs consistency")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": f"mw-{session_id}",
                "phase": "approved_frozen",
                "approval_gate": {
                    "status": "approved-frozen",
                    "freeze": {"affected_surfaces": ["docs/PRD.md"]},
                },
            },
            tmp_path,
        )
        target = tmp_path / "docs" / "OTHER.md"
        target.parent.mkdir(parents=True)
        target.write_text("old", encoding="utf-8")
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Edit",
                        "session_id": session_id,
                        "tool_input": {"file_path": str(target)},
                    }
                )
            ),
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        enforce_maintenance_scope.main()

        decision = json.loads(captured.getvalue())["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"

    def test_maintenance_permission_request_blocks_direct_artifact_write(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker

        session_id = "maintenance-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow improve trace")
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PermissionRequest",
                        "tool_name": "Write",
                        "session_id": session_id,
                        "tool_input": {"file_path": str(tmp_path / "ops" / "agent-harness" / "evidence/cartography.json")},
                    }
                )
            ),
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        enforce_maintenance_scope.main()

        decision = json.loads(captured.getvalue())["hookSpecificOutput"]
        assert decision["hookEventName"] == "PermissionRequest"
        assert decision["decision"]["behavior"] == "deny"








    def test_maintenance_prompt_initializes_durable_trace(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import mark_active as mark_maintenance_active

        session_id = "maintenance-session"
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(json.dumps({"session_id": session_id, "prompt": "/maintenance-workflow improve trace"})),
        )

        mark_maintenance_active.main()

        trace = tmp_path / "ops" / "agent-harness" / "views/trace.md"
        state = tmp_path / "ops" / "agent-harness" / "current-run-state.json"
        events = tmp_path / "ops" / "agent-harness" / "latest-events.jsonl"
        assert trace.is_file()
        assert state.is_file()
        assert events.is_file()
        assert "workflow-start" in trace.read_text(encoding="utf-8")
        assert "detailed_worker_output: artifact-only" in trace.read_text(encoding="utf-8")


    def test_maintenance_tool_events_update_durable_trace(self, tmp_path, monkeypatch):
        from tools.hooks import capture_subagent_tool_event
        from tools.hooks.maintenance.scope import write_marker
        from tools.hooks.maintenance.trace import initialize_trace

        session_id = "maintenance-session"
        marker = write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow improve trace")
        initialize_trace(tmp_path, marker, {"session_id": session_id})
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Read",
                        "session_id": session_id,
                        "tool_input": {"file_path": "docs/MAINTENANCE_HARNESS_CONTRACT.md"},
                    }
                )
            ),
        )

        capture_subagent_tool_event.main()

        trace = (tmp_path / "ops" / "agent-harness" / "views/trace.md").read_text(encoding="utf-8")
        assert "pre_tool" in trace
        assert "docs/MAINTENANCE_HARNESS_CONTRACT.md" in trace





    def test_maintenance_tool_events_do_not_mutate_rich_current_run_state(self, tmp_path, monkeypatch):
        from tools.hooks import capture_subagent_tool_event
        from tools.hooks.maintenance.scope import write_marker
        from tools.hooks.maintenance.trace import initialize_trace
        from tools.runtime.json_io import read_json_object, write_json_atomic_under_root

        session_id = "maintenance-session"
        marker = write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow improve trace")
        initialize_trace(tmp_path, marker, {"session_id": session_id})
        state_path = tmp_path / "ops" / "agent-harness" / "current-run-state.json"
        write_json_atomic_under_root(
            state_path,
            {
                "schema_version": 1,
                "workflow_id": marker["workflow_id"],
                "phase": "draft_planned",
                "active_candidate_id": "O1-compact-run-ledger-state-unification",
                "queued_candidate_ids": ["O2-scope-guard-evidence"],
                "terminal_candidate": False,
                "approval_gate": {"status": "not-ready", "requires_human_approval": False},
                "retry": {"target": "retry-plan", "blockers": ["plan/state drift"]},
                "artifacts": [
                    {
                        "path": "ops/agent-harness/evidence/plan.json",
                        "canonical_path": f"ops/agent-harness/runs/{marker['workflow_id']}/candidates/O1-compact-run-ledger-state-unification/r001-plan.json",
                        "workflow_id": marker["workflow_id"],
                        "candidate_id": "O1-compact-run-ledger-state-unification",
                        "phase": "draft_planned",
                        "revision": 1,
                    }
                ],
                "latest_event": {"event": "artifact-write"},
                "failure_mode_ledger": {"required": True, "mapped": True, "direct_evidence": False},
                "pass_eligibility": {"eligible": False, "blocked_by": ["implementation pending"]},
                "worker_status": {},
            },
            tmp_path,
        )
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(
                json.dumps(
                    {
                        "hook_event_name": "PostToolUse",
                        "tool_name": "Read",
                        "session_id": session_id,
                        "tool_input": {"file_path": "ops/agent-harness/evidence/cartography.json"},
                    }
                )
            ),
        )

        capture_subagent_tool_event.main()

        state = read_json_object(state_path)
        assert state["phase"] == "draft_planned"
        assert state["active_candidate_id"] == "O1-compact-run-ledger-state-unification"
        assert state["queued_candidate_ids"] == ["O2-scope-guard-evidence"]
        assert state["approval_gate"]["status"] == "not-ready"
        assert state["retry"]["target"] == "retry-plan"
        assert state["latest_event"]["event"] == "artifact-write"
        trace = (tmp_path / "ops" / "agent-harness" / "views/trace.md").read_text(encoding="utf-8")
        assert "## Current State" in trace
        assert "active_candidate_id: `O1-compact-run-ledger-state-unification`" in trace
        assert "queued_candidate_ids: `O2-scope-guard-evidence`" in trace
        assert "approval_gate: `not-ready`" in trace
        assert "retry_target: `retry-plan`" in trace
        assert "artifact_paths: `ops/agent-harness/evidence/plan.json`" in trace
        assert "post_tool" in trace
        assert "ops/agent-harness/evidence/cartography.json" in trace

    def test_maintenance_prompt_initializes_canonical_current_run_state(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import mark_active as mark_maintenance_active
        from tools.agent_harness.harness import MaintenanceHarness
        from tools.runtime.json_io import read_json_object

        session_id = "maintenance-canonical-session"
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(json.dumps({"session_id": session_id, "prompt": "/maintenance-workflow improve trace"})),
        )

        mark_maintenance_active.main()

        state = read_json_object(tmp_path / "ops" / "agent-harness" / "current-run-state.json")
        MaintenanceHarness.validate_state_checkpoint(state)
        assert set(MaintenanceHarness.STATE_FORBIDDEN_TOP_LEVEL_ALIASES).isdisjoint(state)
        assert isinstance(state["artifacts"], list)




    def test_maintenance_final_report_blocks_pass_claim_without_eligibility(self, tmp_path):
        from tools.hooks.maintenance.enforce_final_report import final_report_block_reason
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-pass-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs consistency")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {"schema_version": 1, "workflow_id": f"mw-{session_id}", "phase": "skeptic_reviewed", "pass_eligibility": {"calculated": {"eligible": False}}},
            tmp_path,
        )

        reason = final_report_block_reason(tmp_path, {"session_id": session_id, "last_assistant_message": "pass"})

        assert reason is not None






    def test_maintenance_final_report_blocks_fail_during_retry_plan(self, tmp_path):
        from tools.hooks.maintenance.enforce_final_report import final_report_block_reason
        from tools.runtime.json_io import write_json_atomic_under_root

        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": "mw-retry-plan-fail",
                "phase": "skeptic_reviewed",
                "retry": {"target": "retry-plan", "blockers": ["plan review scope fit missing"]},
                "pass_eligibility": {"calculated": {"eligible": False}},
            },
            tmp_path,
        )

        reason = final_report_block_reason(tmp_path, {"session_id": "missing", "last_assistant_message": "fail\n막혀서 종료합니다."})

        assert reason is not None


    def test_maintenance_final_report_clears_marker_after_eligible_pass(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import enforce_final_report
        from tools.hooks.maintenance.scope import active_marker_for_session, write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-final-clear-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {"schema_version": 1, "workflow_id": f"mw-{session_id}", "phase": "skeptic_reviewed", "pass_eligibility": {"calculated": {"eligible": True}}},
            tmp_path,
        )
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(sys, "stdin", io.StringIO(json.dumps({"session_id": session_id, "last_assistant_message": "pass"})))

        enforce_final_report.main()

        assert active_marker_for_session(tmp_path, session_id) is None




@pytest.mark.parametrize(
    "command",
    [
        pytest.param('cat repos/secret.txt "', id="unparseable"),
        pytest.param("cat repos/secret.txt", id="parseable-repos-read"),
    ],
)
def test_maintenance_scope_guard_denies_bash_repos_read(tmp_path, monkeypatch, command):
    from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
    from tools.hooks.maintenance.scope import write_marker

    session_id = "maintenance-bash-repos-read"
    write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs")
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "session_id": session_id,
                    "tool_input": {"command": command},
                }
            )
        ),
    )
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)

    enforce_maintenance_scope.main()

    decision = json.loads(captured.getvalue())["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"


def test_maintenance_scope_guard_denies_safe_writer_content_payload_flags(tmp_path, monkeypatch):
    from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
    from tools.hooks.maintenance.scope import write_marker

    session_id = "maintenance-content-payload"
    write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs")
    forbidden_arg = "--content-" + "text"
    monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
    monkeypatch.setattr(
        sys,
        "stdin",
        io.StringIO(
            json.dumps(
                {
                    "hook_event_name": "PreToolUse",
                    "tool_name": "Bash",
                    "session_id": session_id,
                    "tool_input": {
                        "command": f"uv run python -m tools.agent_harness.safe_artifact_writer write --kind plan --status passed --summary ok --workflow-id mw-1 --candidate-id C1 {forbidden_arg} '{{}}'",
                    },
                }
            )
        ),
    )
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)

    enforce_maintenance_scope.main()

    decision = json.loads(captured.getvalue())["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"

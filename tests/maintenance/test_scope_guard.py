from __future__ import annotations

import io
import json
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
sys.path.insert(0, str(ROOT))



class TestMaintenanceScopeGuardContract:
    def _matcher_tools(self, matcher):
        return set(str(matcher).split("|")) if matcher else set()

    def test_maintenance_scope_guard_is_wired_before_general_capture(self):
        settings = json.loads((ROOT / ".claude" / "settings.json").read_text(encoding="utf-8"))
        pretool_commands = [entry["hooks"][0]["command"] for entry in settings["hooks"]["PreToolUse"]]
        permission_allow = settings["permissions"]["allow"]
        expansion_commands = [
            hook["command"]
            for entry in settings["hooks"]["UserPromptExpansion"]
            if "maintenance-workflow" in entry.get("matcher", "")
            for hook in entry["hooks"]
        ]

        assert any("maintenance/mark_active.sh" in command for command in expansion_commands)
        assert "maintenance/enforce_scope.sh" in pretool_commands[0]
        assert "Agent" in settings["hooks"]["PreToolUse"][0]["matcher"]
        assert "Skill" in settings["hooks"]["PreToolUse"][0]["matcher"]
        assert any("capture_subagent_tool_event.sh" in command for command in pretool_commands[1:])
        assert 'Bash(bash "$CLAUDE_PROJECT_DIR/.claude/hooks/maintenance/mark_active.sh")' in permission_allow
        assert "Bash(uv run python -m tools.agent_harness.safe_artifact_writer write *)" in permission_allow
        assert not any(
            rule.startswith((
                "Write(/ops/agent-harness",
                "Write(ops/agent-harness",
                "Edit(/ops/agent-harness",
                "Edit(ops/agent-harness",
                "MultiEdit(/ops/agent-harness",
                "MultiEdit(ops/agent-harness",
            ))
            for rule in permission_allow
        )
        assert "Bash(uv run pytest *)" in permission_allow
        assert not any(rule == "Write(.claude/**)" for rule in permission_allow)

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
        assert "retry-plan" in decision["permissionDecisionReason"]
        assert "blocked maintenance-evaluator" in decision["permissionDecisionReason"]

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

    def test_maintenance_scope_guard_blocks_agent_outside_policy_route(self, tmp_path, monkeypatch, capsys):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-policy-route-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs polish")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": f"mw-{session_id}",
                "phase": "draft_planned",
                "retry": {"target": "", "blockers": []},
                "pass_eligibility": {
                    "calculated": {"eligible": False, "workflow_path": "STANDARD"},
                    "workflow_profile": {
                        "path": "STANDARD",
                        "route": ["maintenance-planner", "maintenance-plan-critic", "maintenance-implementer", "maintenance-evaluator"],
                    },
                },
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
                        "tool_input": {"agent_type": "maintenance-cartographer"},
                    }
                )
            ),
        )

        enforce_maintenance_scope.main()

        output = json.loads(capsys.readouterr().out)
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "policy route" in decision["permissionDecisionReason"]
        assert "maintenance-cartographer" in decision["permissionDecisionReason"]

    def test_maintenance_subagent_stop_ignores_denied_unstarted_worker(self, tmp_path):
        from tools.hooks.capture_subagent_trace import capture_trace
        from tools.hooks.maintenance.scope import write_marker
        from tools.hooks.maintenance.trace import record_event

        session_id = "maintenance-denied-stop-session"
        marker = write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs polish")
        record_event(
            tmp_path,
            marker,
            {"session_id": session_id, "tool_name": "Agent", "tool_input": {"agent_type": "maintenance-plan-critic"}},
            event="agent-deny",
            phase="guard",
            guard="deny",
            result="blocked maintenance-plan-critic",
        )

        result = capture_trace(
            {"session_id": session_id, "agent_type": "maintenance-plan-critic", "last_assistant_message": "should not count"},
            tmp_path,
        )

        assert result is None
        events = (tmp_path / "ops" / "agent-harness" / "latest-events.jsonl").read_text(encoding="utf-8")
        assert '"event":"tool-event"' not in events
        assert not (tmp_path / "ops" / "agent-harness" / "evidence/plan-review.json").exists()

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
        assert "must not invoke other skills" in decision["permissionDecisionReason"]

    def test_maintenance_scope_guard_blocks_repo_read(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker

        session_id = "maintenance-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow scope guard")
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
                        "tool_input": {"file_path": str(tmp_path / "repo" / "src" / "state.md")},
                    }
                )
            ),
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        enforce_maintenance_scope.main()

        decision = json.loads(captured.getvalue())["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "repo/**" in decision["permissionDecisionReason"]


    def test_maintenance_scope_guard_blocks_direct_artifact_write(self, tmp_path, monkeypatch):
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
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Write",
                        "session_id": session_id,
                        "tool_input": {"file_path": str(tmp_path / "ops" / "agent-harness" / "evidence/plan.json")},
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
        assert "different workflow run" in decision["permissionDecisionReason"]


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

    def test_maintenance_scope_guard_blocks_missing_evidence_artifact_read(self, tmp_path, monkeypatch):
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
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Read",
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
        assert decision["permissionDecision"] == "deny"
        assert "created with Write after worker output" in decision["permissionDecisionReason"]

    def test_maintenance_scope_guard_allows_existing_evidence_artifact_read(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker

        artifact = tmp_path / "ops" / "agent-harness" / "evidence/cartography.json"
        artifact.parent.mkdir(parents=True)
        artifact.write_text("# Maintenance Cartography\n", encoding="utf-8")
        session_id = "maintenance-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow improve trace")
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
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        enforce_maintenance_scope.main()

        assert captured.getvalue() == ""

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
        assert "safe-writer generated" in decision["permissionDecisionReason"]

    def test_maintenance_scope_guard_blocks_manual_state_write_even_when_valid(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker

        harness_root = tmp_path / "ops" / "agent-harness"
        harness_root.mkdir(parents=True)
        (harness_root / "evidence").mkdir()
        (harness_root / "evidence/cartography.json").write_text("# Cartography\n", encoding="utf-8")
        session_id = "maintenance-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow improve trace")
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        current_state = {
            "schema_version": 1,
            "workflow_id": "mw-session",
            "phase": "cartographed",
            "active_candidate_id": "",
            "queued_candidate_ids": [],
            "terminal_candidate": True,
            "approval_gate": {"status": "not-ready", "requires_human_approval": False, "approval_evidence_recorded": False, "freeze": {}},
            "retry": {"target": "", "blockers": []},
            "artifacts": [
                {
                    "path": "ops/agent-harness/evidence/cartography.json",
                    "canonical_path": "ops/agent-harness/runs/mw-session/candidates/run/r001-cartography.json",
                    "workflow_id": "mw-session",
                    "candidate_id": "",
                    "phase": "cartographed",
                    "revision": 1,
                }
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
                            "content": json.dumps(current_state),
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
        assert "safe-writer generated" in decision["permissionDecisionReason"]

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
        assert "safe-writer generated" in decision["permissionDecisionReason"]


    def test_maintenance_scope_guard_does_not_auto_allow_repo_write(self, tmp_path, monkeypatch):
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
                        "hook_event_name": "PreToolUse",
                        "tool_name": "Write",
                        "session_id": session_id,
                        "tool_input": {"file_path": str(tmp_path / "tools" / "maintenance" / "maintenance_harness.py")},
                    }
                )
            ),
        )
        captured = io.StringIO()
        monkeypatch.setattr(sys, "stdout", captured)

        enforce_maintenance_scope.main()

        decision = json.loads(captured.getvalue())["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "approved_frozen" in decision["permissionDecisionReason"]

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

    def test_route_cursor_blocks_completed_worker_reentry(self, tmp_path, monkeypatch, capsys):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-route-cursor-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow critical")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": f"mw-{session_id}",
                "phase": "evaluated",
                "pass_eligibility": {
                    "route_cursor": {
                        "route": ["maintenance-implementer", "maintenance-evaluator", "maintenance-skeptic"],
                        "completed_workers": ["maintenance-implementer", "maintenance-evaluator"],
                        "next_required_worker": "maintenance-skeptic",
                        "remaining_required_artifacts": ["ops/agent-harness/evidence/skeptic-review.json"],
                    },
                    "workflow_profile": {"route": ["maintenance-implementer", "maintenance-evaluator", "maintenance-skeptic"]},
                    "calculated": {"eligible": False, "workflow_path": "CRITICAL_HARNESS"},
                },
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
        assert "requires maintenance-skeptic" in decision["permissionDecisionReason"]

    def test_route_cursor_blocks_future_worker_before_next_required(self, tmp_path, monkeypatch, capsys):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-route-cursor-future-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow standard")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": f"mw-{session_id}",
                "phase": "draft_planned",
                "pass_eligibility": {
                    "route_cursor": {
                        "route": ["maintenance-planner", "maintenance-plan-critic", "maintenance-implementer"],
                        "completed_workers": ["maintenance-planner"],
                        "next_required_worker": "maintenance-plan-critic",
                        "remaining_required_artifacts": ["ops/agent-harness/evidence/plan-review.json"],
                    },
                    "workflow_profile": {"route": ["maintenance-planner", "maintenance-plan-critic", "maintenance-implementer"]},
                    "calculated": {"eligible": False, "workflow_path": "STANDARD"},
                },
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
                        "tool_input": {"agent_type": "maintenance-implementer"},
                    }
                )
            ),
        )

        enforce_maintenance_scope.main()

        output = json.loads(capsys.readouterr().out)
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "requires maintenance-plan-critic" in decision["permissionDecisionReason"]
        assert "blocked maintenance-implementer" in decision["permissionDecisionReason"]

    def test_route_cursor_blocks_implementer_rerun_after_approved_edit(self, tmp_path, monkeypatch, capsys):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-implementer-rerun-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow critical")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": f"mw-{session_id}",
                "phase": "evaluated",
                "changed_files": ["docs/MAINTENANCE_HARNESS_CONTRACT.md"],
                "worker_status": {},
                "pass_eligibility": {
                    "route_cursor": {
                        "route": ["maintenance-implementer", "maintenance-evaluator"],
                        "completed_workers": [],
                        "next_required_worker": "maintenance-implementer",
                        "remaining_required_artifacts": ["ops/agent-harness/evidence/execution.json"],
                    },
                    "workflow_profile": {"route": ["maintenance-implementer", "maintenance-evaluator"]},
                    "calculated": {"eligible": False, "workflow_path": "CRITICAL_HARNESS"},
                },
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
                        "tool_input": {"agent_type": "maintenance-implementer"},
                    }
                )
            ),
        )

        enforce_maintenance_scope.main()

        output = json.loads(capsys.readouterr().out)
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "approved implementation edit is already recorded" in decision["permissionDecisionReason"]
        assert "--kind execution --status passed" in decision["permissionDecisionReason"]

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
        assert "maintenance-implementer budget exceeded" in decision["permissionDecisionReason"]
        assert "do not call maintenance-implementer again" in decision["permissionDecisionReason"]
        assert "--retry-target retry-implementation" in decision["permissionDecisionReason"]

    def test_review_worker_budget_blocks_non_converging_plan_critic(self, tmp_path, monkeypatch, capsys):
        from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import append_jsonl_atomic_under_root, write_json_atomic_under_root

        session_id = "maintenance-review-budget-session"
        workflow_id = f"mw-{session_id}"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow critical")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": workflow_id,
                "phase": "draft_planned",
                "pass_eligibility": {"calculated": {"eligible": False}},
            },
            tmp_path,
        )
        for index in range(16):
            append_jsonl_atomic_under_root(
                tmp_path / "ops" / "agent-harness" / "latest-events.jsonl",
                {
                    "captured_at": f"2026-06-17T17:00:{index:02d}.000000Z",
                    "workflow_id": workflow_id,
                    "event": "pre_tool",
                    "agent_type": "maintenance-plan-critic",
                    "tool_name": "Read",
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
                        "tool_name": "Read",
                        "agent_type": "maintenance-plan-critic",
                        "session_id": session_id,
                        "tool_input": {"file_path": "ops/agent-harness/evidence/plan.json"},
                    }
                )
            ),
        )

        enforce_maintenance_scope.main()

        output = json.loads(capsys.readouterr().out)
        decision = output["hookSpecificOutput"]
        assert decision["permissionDecision"] == "deny"
        assert "maintenance-plan-critic budget exceeded" in decision["permissionDecisionReason"]
        assert "do not call maintenance-plan-critic again" in decision["permissionDecisionReason"]
        assert "--kind plan-review --status failed --retry-target retry-plan" in decision["permissionDecisionReason"]

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
        assert "outside approved affected surfaces" in decision["permissionDecisionReason"]

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
        assert "maintenance artifact" in decision["decision"]["message"]







    def test_maintenance_permission_request_does_not_auto_allow_repo_write(self, tmp_path, monkeypatch):
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
                        "tool_input": {"file_path": str(tmp_path / "tools" / "maintenance" / "maintenance_harness.py")},
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
        assert "approved_frozen" in decision["decision"]["message"]

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

    def test_maintenance_prompt_resets_latest_trace_between_runs(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import mark_active as mark_maintenance_active

        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(json.dumps({"session_id": "s1", "prompt": "/maintenance-workflow first topic"})),
        )
        mark_maintenance_active.main()
        trace_path = tmp_path / "ops" / "agent-harness" / "views/trace.md"
        assert "mw-s1" in trace_path.read_text(encoding="utf-8")

        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(json.dumps({"session_id": "s2", "prompt": "/maintenance-workflow second topic"})),
        )
        mark_maintenance_active.main()

        trace = trace_path.read_text(encoding="utf-8")
        assert "mw-s2" in trace
        assert "mw-s1" not in trace

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

    def test_maintenance_trace_replaces_stale_candidate_queue_from_structured_state(self, tmp_path):
        from tools.hooks.maintenance.scope import write_marker
        from tools.hooks.maintenance.trace import record_event
        from tools.runtime.json_io import read_json_object, write_json_atomic_under_root

        session_id = "maintenance-stale-queue-session"
        marker = write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs polish")
        trace_root = tmp_path / "ops" / "agent-harness"
        trace_root.mkdir(parents=True, exist_ok=True)
        write_json_atomic_under_root(
            trace_root / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": marker["workflow_id"],
                "phase": "cartographed",
                "active_candidate_id": "DOC-CAND-001",
                "queued_candidate_ids": ["DOC-CAND-002", "DOC-CAND-003"],
                "queue_policy": "human-decision",
                "worker_status": {},
                "artifacts": [],
                "pass_eligibility": {"calculated": {"eligible": False}},
            },
            tmp_path,
        )
        write_json_atomic_under_root(
            trace_root / "latest-candidate-state.json",
            {
                "schema_version": 1,
                "workflow_id": marker["workflow_id"],
                "active_candidate_id": "DOC-CAND-001",
                "queued_candidate_ids": ["DOC-CAND-003"],
                "queue_policy": "human-decision",
                "terminal_candidate": False,
            },
            tmp_path,
        )

        record_event(
            tmp_path,
            marker,
            {"session_id": session_id, "tool_name": "Read", "tool_input": {"file_path": "docs/PRD.md"}},
            event="post_tool",
            phase="tool-event",
        )

        state = read_json_object(trace_root / "current-run-state.json")
        assert state["queued_candidate_ids"] == ["DOC-CAND-003"]
        assert state["queue_policy"] == "human-decision"
        assert state["terminal_candidate"] is False

    def test_maintenance_trace_does_not_mutate_state_before_state_write(self, tmp_path):
        from tools.hooks.maintenance.scope import write_marker
        from tools.hooks.maintenance.trace import initialize_trace, record_event

        session_id = "maintenance-state-write-session"
        marker = write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow smoke")
        initialize_trace(tmp_path, marker, {"session_id": session_id})
        state_path = tmp_path / "ops" / "agent-harness" / "current-run-state.json"
        before = state_path.read_text(encoding="utf-8")

        record_event(
            tmp_path,
            marker,
            {
                "session_id": session_id,
                "tool_name": "Write",
                "tool_input": {"file_path": "ops/agent-harness/current-run-state.json"},
            },
            event="tool-event",
            phase="pre_tool",
        )
        assert state_path.read_text(encoding="utf-8") == before

        record_event(
            tmp_path,
            marker,
            {
                "session_id": session_id,
                "tool_name": "Write",
                "tool_input": {"file_path": "ops/agent-harness/current-run-state.json"},
            },
            event="artifact-write-deny",
            phase="artifact",
            result="maintenance artifacts are safe-writer generated",
            artifact_path="ops/agent-harness/current-run-state.json",
        )
        assert state_path.read_text(encoding="utf-8") == before

    def test_maintenance_final_trace_refresh_replaces_unmanaged_mutation_with_checker_view(self, tmp_path):
        from tools.hooks.maintenance.scope import write_marker
        from tools.hooks.maintenance.trace import initialize_trace, record_event
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-stale-write-session"
        marker = write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow improve trace")
        marker["active_candidate_id"] = "O1"
        initialize_trace(tmp_path, marker, {"session_id": session_id})
        state_path = tmp_path / "ops" / "agent-harness" / "current-run-state.json"
        write_json_atomic_under_root(
            state_path,
            {
                "schema_version": 1,
                "workflow_id": marker["workflow_id"],
                "phase": "skeptic_reviewed",
                "active_candidate_id": "O1",
                "queued_candidate_ids": [],
                "terminal_candidate": True,
                "approval_gate": {"status": "approved-frozen", "requires_human_approval": False},
                "retry": {"target": "", "blockers": []},
                "artifacts": [
                    {
                        "path": "ops/agent-harness/evidence/plan.json",
                        "canonical_path": f"ops/agent-harness/runs/{marker['workflow_id']}/candidates/O1/r001-plan.json",
                        "workflow_id": marker["workflow_id"],
                        "candidate_id": "O1",
                        "phase": "draft_planned",
                        "revision": 1,
                    },
                    {
                        "path": "ops/agent-harness/evidence/execution.json",
                        "canonical_path": f"ops/agent-harness/runs/{marker['workflow_id']}/candidates/O1/r001-execution.json",
                        "workflow_id": marker["workflow_id"],
                        "candidate_id": "O1",
                        "phase": "implemented",
                        "revision": 1,
                    },
                    {
                        "path": "ops/agent-harness/evidence/execution-review.json",
                        "canonical_path": f"ops/agent-harness/runs/{marker['workflow_id']}/candidates/O1/r001-execution-review.json",
                        "workflow_id": marker["workflow_id"],
                        "candidate_id": "O1",
                        "phase": "evaluated",
                        "revision": 1,
                    },
                ],
                "latest_event": {"event": "tool-event"},
                "failure_mode_ledger": {"required": True, "mapped": True, "direct_evidence": True},
                "pass_eligibility": {"eligible": False, "blocked_by": []},
                "worker_status": {},
            },
            tmp_path,
        )
        record_event(
            tmp_path,
            marker,
            {"session_id": session_id, "agent_type": "maintenance-evaluator"},
            event="tool-event",
            phase="worker",
            result="ended",
            artifact_path="ops/agent-harness/evidence/execution.json",
        )
        trace_path = tmp_path / "ops" / "agent-harness" / "views/trace.md"
        stale_snapshot = trace_path.read_text(encoding="utf-8")
        trace_path.write_text(
            stale_snapshot + "\n## Evaluator Hook Mutation\n\n- evaluator hook event retained after stale snapshot\n",
            encoding="utf-8",
        )

        record_event(
            tmp_path,
            marker,
            {"session_id": session_id, "agent_type": "maintenance-skeptic"},
            event="tool-event",
            phase="worker",
            result="ended",
            artifact_path="ops/agent-harness/evidence/execution-review.json",
        )

        trace = trace_path.read_text(encoding="utf-8")
        assert "Evaluator Hook Mutation" not in trace
        assert "maintenance-evaluator" in trace
        assert "maintenance-skeptic" in trace
        assert "ops/agent-harness/evidence/plan.json" in trace
        assert "ops/agent-harness/evidence/execution.json" in trace
        assert "ops/agent-harness/evidence/execution-review.json" in trace
        assert "active_candidate_id: `O1`" in trace

    def test_maintenance_trace_records_human_decision_queue_from_cartography(self, tmp_path):
        from tools.hooks.maintenance.scope import write_marker
        from tools.hooks.maintenance.trace import initialize_trace, record_event
        from tools.runtime.json_io import read_json_object, write_json_atomic_under_root

        session_id = "maintenance-queue-session"
        marker = write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs")
        trace_root = tmp_path / "ops" / "agent-harness"
        initialize_trace(tmp_path, marker, {"session_id": session_id})
        trace_root.mkdir(parents=True, exist_ok=True)
        write_json_atomic_under_root(
            trace_root / "latest-candidate-state.json",
            {
                "schema_version": 1,
                "workflow_id": marker["workflow_id"],
                "active_candidate_id": "DOC-CAND-001",
                "queued_candidate_ids": ["DOC-CAND-002", "DOC-CAND-003"],
                "queue_policy": "human-decision",
                "terminal_candidate": False,
            },
            tmp_path,
        )

        record_event(tmp_path, marker, {"session_id": session_id, "tool_name": "Read"}, event="post_tool", phase="tool-event")

        state = read_json_object(trace_root / "current-run-state.json")
        assert state["active_candidate_id"] == "DOC-CAND-001"
        assert state["queued_candidate_ids"] == ["DOC-CAND-002", "DOC-CAND-003"]
        assert state["queue_policy"] == "human-decision"

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

    def test_maintenance_prompt_resets_stale_latest_evidence_views(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import mark_active as mark_maintenance_active

        trace_root = tmp_path / "ops" / "agent-harness"
        trace_root.mkdir(parents=True)
        (trace_root / "evidence").mkdir()
        for name in (
            "evidence/cartography.json",
            "evidence/plan.json",
            "evidence/plan-review.json",
            "evidence/execution.json",
            "evidence/execution-review.json",
            "evidence/skeptic-review.json",
            "latest-candidate-state.json",
            "latest-plan-metadata.json",
            "latest-plan-review-metadata.json",
            "latest-execution-review-metadata.json",
            "latest-artifact-index.json",
        ):
            (trace_root / name).write_text(f"stale previous run {name}\n", encoding="utf-8")

        session_id = "maintenance-reset-session"
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(json.dumps({"session_id": session_id, "prompt": "/maintenance-workflow docs consistency"})),
        )

        mark_maintenance_active.main()

        assert (trace_root / "current-run-state.json").is_file()
        assert (trace_root / "views/trace.md").is_file()
        assert (trace_root / "latest-events.jsonl").is_file()
        for name in (
            "evidence/cartography.json",
            "evidence/plan.json",
            "evidence/plan-review.json",
            "evidence/execution.json",
            "evidence/execution-review.json",
            "latest-candidate-state.json",
            "latest-plan-metadata.json",
            "latest-plan-review-metadata.json",
            "latest-execution-review-metadata.json",
            "latest-artifact-index.json",
        ):
            assert not (trace_root / name).exists(), name

    def test_maintenance_tool_event_refreshes_same_workflow_noncanonical_state(self, tmp_path, monkeypatch):
        from tools.hooks import capture_subagent_tool_event
        from tools.hooks.maintenance.scope import write_marker
        from tools.hooks.maintenance.trace import initialize_trace
        from tools.agent_harness.harness import MaintenanceHarness
        from tools.runtime.json_io import read_json_object, write_json_atomic_under_root

        session_id = "maintenance-noncanonical-session"
        marker = write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow improve trace")
        initialize_trace(tmp_path, marker, {"session_id": session_id})
        state_path = tmp_path / "ops" / "agent-harness" / "current-run-state.json"
        write_json_atomic_under_root(
            state_path,
            {
                "schema_version": 1,
                "workflow_id": marker["workflow_id"],
                "phase": "draft_planned",
                "active_candidate_id": "O1",
                "approval": {"status": "not-ready"},
                "workers": {},
                "retry_target": "retry-plan",
                "artifacts": {"plan": {"path": "ops/agent-harness/evidence/plan.json"}},
            },
            tmp_path,
        )
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "latest-plan-metadata.json",
            {
                "schema_version": 1,
                "workflow_id": marker["workflow_id"],
                "candidate_id": "O1",
                "affected_surfaces": ["CLAUDE.md"],
                "acceptance_criteria_ids": ["AC-001"],
                "failure_mode_severity": "P3",
                "plan_contract_hash": "a" * 64,
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
                        "tool_input": {"file_path": "ops/agent-harness/evidence/plan.json"},
                    }
                )
            ),
        )

        capture_subagent_tool_event.main()

        state = read_json_object(state_path)
        MaintenanceHarness.validate_state_checkpoint(state)
        assert set(MaintenanceHarness.STATE_FORBIDDEN_TOP_LEVEL_ALIASES).isdisjoint(state)
        assert isinstance(state["artifacts"], list)
        assert state["latest_event"]["event"] == "post_tool"
        profile = state["pass_eligibility"]["workflow_profile"]
        assert profile["path"] == "STANDARD"
        assert profile["surface_classes"] == ["instruction_doc"]
        assert profile["route"] == ["maintenance-planner", "maintenance-plan-critic", "maintenance-implementer", "maintenance-evaluator"]

    def test_maintenance_final_report_blocks_approval_claim_before_review_state(self, tmp_path):
        from tools.hooks.maintenance.enforce_final_report import final_report_block_reason
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-final-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs consistency")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {"schema_version": 1, "workflow_id": f"mw-{session_id}", "phase": "draft_planned", "pass_eligibility": {"calculated": {"eligible": False}}},
            tmp_path,
        )

        reason = final_report_block_reason(
            tmp_path,
            {"session_id": session_id, "last_assistant_message": "awaiting-human-approval\n승인: DOCS-001 <plan_contract_hash_prefix> 문구가 필요합니다."},
        )

        assert reason is not None
        assert "runner state phase" in reason

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
        assert "pass_eligibility" in reason

    def test_maintenance_final_report_includes_route_cursor_next_action(self, tmp_path):
        from tools.hooks.maintenance.enforce_final_report import final_report_block_reason
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-next-action-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow critical")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": f"mw-{session_id}",
                "phase": "evaluated",
                "pass_eligibility": {
                    "route_cursor": {
                        "route": ["maintenance-evaluator", "maintenance-skeptic"],
                        "completed_workers": ["maintenance-evaluator"],
                        "next_required_worker": "maintenance-skeptic",
                        "remaining_required_artifacts": ["ops/agent-harness/evidence/skeptic-review.json"],
                    },
                    "calculated": {"eligible": False, "blocked_by": ["missing_artifacts"]},
                },
            },
            tmp_path,
        )

        reason = final_report_block_reason(tmp_path, {"session_id": session_id, "last_assistant_message": "pass"})

        assert reason is not None
        assert "Continue with exactly `maintenance-skeptic`" in reason
        assert "ops/agent-harness/evidence/skeptic-review.json" in reason
        assert "do not rerun completed workers" in reason

    def test_maintenance_final_report_prefers_execution_evidence_after_edit(self, tmp_path):
        from tools.hooks.maintenance.enforce_final_report import final_report_block_reason
        from tools.hooks.maintenance.scope import write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-execution-evidence-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow critical")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": f"mw-{session_id}",
                "phase": "approved_frozen",
                "changed_files": ["docs/MAINTENANCE_HARNESS_CONTRACT.md"],
                "pass_eligibility": {
                    "route_cursor": {
                        "route": ["maintenance-implementer", "maintenance-evaluator"],
                        "completed_workers": [],
                        "next_required_worker": "maintenance-implementer",
                        "remaining_required_artifacts": ["ops/agent-harness/evidence/execution.json"],
                    },
                    "calculated": {"eligible": False, "blocked_by": ["missing_artifacts"]},
                },
            },
            tmp_path,
        )

        reason = final_report_block_reason(tmp_path, {"session_id": session_id, "last_assistant_message": "pass"})

        assert reason is not None
        assert "do not call maintenance-implementer again" in reason
        assert "--kind execution --status passed" in reason
        assert "Continue with exactly `maintenance-implementer`" not in reason

    def test_maintenance_final_report_blocks_pass_without_marker_when_state_active(self, tmp_path):
        from tools.hooks.maintenance.enforce_final_report import final_report_block_reason
        from tools.runtime.json_io import write_json_atomic_under_root

        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {"schema_version": 1, "workflow_id": "mw-markerless-pass", "phase": "evaluated", "pass_eligibility": {"calculated": {"eligible": False}}},
            tmp_path,
        )

        reason = final_report_block_reason(tmp_path, {"session_id": "missing", "last_assistant_message": "pass"})

        assert reason is not None
        assert "pass_eligibility" in reason

    def test_maintenance_final_report_blocks_pass_during_stop_hook_reentry(self, tmp_path):
        from tools.hooks.maintenance.enforce_final_report import final_report_block_reason
        from tools.runtime.json_io import write_json_atomic_under_root

        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {"schema_version": 1, "workflow_id": "mw-reentry-pass", "phase": "evaluated", "pass_eligibility": {"calculated": {"eligible": False}}},
            tmp_path,
        )

        reason = final_report_block_reason(tmp_path, {"stop_hook_active": True, "session_id": "missing", "last_assistant_message": "pass"})

        assert reason is not None
        assert "pass_eligibility" in reason

    def test_maintenance_final_report_blocks_stop_when_only_verification_metadata_missing(self, tmp_path):
        from tools.hooks.maintenance.enforce_final_report import final_report_block_reason
        from tools.runtime.json_io import write_json_atomic_under_root

        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": "mw-tests-not-passed",
                "phase": "skeptic_reviewed",
                "pass_eligibility": {"calculated": {"eligible": False, "blocked_by": ["tests_not_passed"]}},
            },
            tmp_path,
        )

        reason = final_report_block_reason(tmp_path, {"session_id": "missing", "last_assistant_message": "stop"})

        assert reason is not None
        assert "--verification-passed true|false" in reason

    def test_maintenance_final_report_blocks_fail_during_retry_plan(self, tmp_path):
        from tools.hooks.maintenance.enforce_final_report import final_report_block_reason
        from tools.runtime.json_io import write_json_atomic_under_root

        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": "mw-retry-plan-fail",
                "phase": "plan_reviewed",
                "retry": {"target": "retry-plan", "blockers": ["plan review scope fit missing"]},
                "pass_eligibility": {"calculated": {"eligible": False}},
            },
            tmp_path,
        )

        reason = final_report_block_reason(tmp_path, {"session_id": "missing", "last_assistant_message": "fail\n막혀서 종료합니다."})

        assert reason is not None
        assert "retry-plan" in reason
        assert "fail" in reason

    def test_maintenance_final_report_blocks_fail_before_terminal_review(self, tmp_path):
        from tools.hooks.maintenance.enforce_final_report import final_report_block_reason
        from tools.runtime.json_io import write_json_atomic_under_root

        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {
                "schema_version": 1,
                "workflow_id": "mw-draft-fail",
                "phase": "draft_planned",
                "retry": {"target": "", "blockers": []},
                "pass_eligibility": {"calculated": {"eligible": False}},
            },
            tmp_path,
        )

        reason = final_report_block_reason(tmp_path, {"session_id": "missing", "last_assistant_message": "fail"})

        assert reason is not None
        assert "중간 phase" in reason

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

    def test_maintenance_final_report_does_not_clear_marker_for_stop_hook_wording(self, tmp_path, monkeypatch):
        from tools.hooks.maintenance import enforce_final_report
        from tools.hooks.maintenance.scope import active_marker_for_session, write_marker
        from tools.runtime.json_io import write_json_atomic_under_root

        session_id = "maintenance-final-nonterminal-session"
        write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs")
        write_json_atomic_under_root(
            tmp_path / "ops" / "agent-harness" / "current-run-state.json",
            {"schema_version": 1, "workflow_id": f"mw-{session_id}", "phase": "intake", "pass_eligibility": {"calculated": {"eligible": False}}},
            tmp_path,
        )
        monkeypatch.setenv("WORKSPACE_ROOT", str(tmp_path))
        monkeypatch.setattr(
            sys,
            "stdin",
            io.StringIO(json.dumps({"session_id": session_id, "last_assistant_message": "needs-human-decision\nStop hook error was observed."})),
        )

        enforce_final_report.main()

        assert active_marker_for_session(tmp_path, session_id) is not None

def test_maintenance_scope_guard_blocks_claude_facing_runner_bash(tmp_path, monkeypatch):
    from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
    from tools.hooks.maintenance.scope import write_marker
    from tools.runtime.json_io import write_json_atomic_under_root

    session_id = "maintenance-session"
    write_marker(tmp_path, {"session_id": session_id}, prompt="/maintenance-workflow docs")
    write_json_atomic_under_root(
        tmp_path / "ops" / "agent-harness" / "current-run-state.json",
        {"schema_version": 1, "workflow_id": f"mw-{session_id}", "phase": "skeptic_reviewed", "pass_eligibility": {"calculated": {"eligible": True, "blocked_by": []}}},
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
                    "tool_name": "Bash",
                    "session_id": session_id,
                    "tool_input": {"command": "uv run python -m tools.agent_harness.decider decide --workflow-id mw-1"},
                }
            )
        ),
    )
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)

    enforce_maintenance_scope.main()

    decision = json.loads(captured.getvalue())["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "already pass-eligible" in decision["permissionDecisionReason"]
    assert "emit final `pass`" in decision["permissionDecisionReason"]


def test_maintenance_scope_guard_denies_unparseable_bash(tmp_path, monkeypatch):
    from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
    from tools.hooks.maintenance.scope import write_marker

    session_id = "maintenance-unparseable-bash"
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
                    "tool_input": {"command": "cat repo/secret.txt \""},
                }
            )
        ),
    )
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)

    enforce_maintenance_scope.main()

    decision = json.loads(captured.getvalue())["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "unparseable Bash" in decision["permissionDecisionReason"]


def test_maintenance_scope_guard_denies_parseable_bash_repo_read(tmp_path, monkeypatch):
    from tools.hooks.maintenance import enforce_scope as enforce_maintenance_scope
    from tools.hooks.maintenance.scope import write_marker

    session_id = "maintenance-parseable-bash-repo"
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
                    "tool_input": {"command": "cat repo/secret.txt"},
                }
            )
        ),
    )
    captured = io.StringIO()
    monkeypatch.setattr(sys, "stdout", captured)

    enforce_maintenance_scope.main()

    decision = json.loads(captured.getvalue())["hookSpecificOutput"]
    assert decision["permissionDecision"] == "deny"
    assert "repo/**" in decision["permissionDecisionReason"]


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
    assert "content payload flags" in decision["permissionDecisionReason"]

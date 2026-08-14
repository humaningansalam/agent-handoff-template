---
id: T-20260813104730Z
title: "Audit and repair test suite contracts"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260813T104730Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
document_language: "en"
---

# T-20260813104730Z - Audit and repair test suite contracts

## Context Docs

<!-- Add only the minimum context docs needed for this task, or leave empty. -->

## Work Area

- Task record: `docs/tasks/T-20260813104730Z--audit-test-suite-contracts.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: Identify the exact repo, docs, or workspace files during the first implementation pass; do not guess them from the title alone.

## Discovery

- Candidate query: `audit tests for false confidence and implementation-coupled assertions`
- Candidate files reviewed:
  - `tests/maintenance/test_json_io.py`
  - `tests/maintenance/test_safe_artifact_writer.py`
  - `tests/maintenance/test_scope_guard.py`
  - `tests/maintenance/test_workflow_contract.py`
  - `tests/repoctl/context/test_context_query.py`
  - `tests/repoctl/graph/test_graph_query.py`
  - `tests/repoctl/knowledge/test_knowledge_projection.py`
  - `tests/repoctl/meta/test_meta_check.py`
  - `tests/repoctl/meta/test_meta_policy.py`
  - `tests/repoctl/test_result_receipts.py`
  - `tests/repoctl/test_upgrade.py`
  - `tests/test_permissions_registry.py`
- Chosen files:
  - `tests/maintenance/test_json_io.py`
  - `tests/maintenance/test_safe_artifact_writer.py`
  - `tests/maintenance/test_scope_guard.py`
  - `tests/maintenance/test_workflow_contract.py`
  - `tests/repoctl/context/test_context_query.py`
  - `tests/repoctl/graph/test_graph_query.py`
  - `tests/repoctl/meta/test_meta_check.py`
  - `tests/repoctl/meta/test_meta_policy.py`
  - `tests/repoctl/test_result_receipts.py`
  - `tests/repoctl/test_upgrade.py`
  - `tests/test_permissions_registry.py`
- Notes: `Removed self-restatements and synthetic implementation tests; repaired retained tests around public behavior and independent oracles.`

## Goal

Remove false-confidence tests, preserve tests that enforce public contracts, and repair weak tests so failures identify real product regressions.

## Scope

- Audit every test module for constant/schema self-restatement, private call-count coupling, circular oracles, weak branch assertions, and incomplete concurrent-write checks.
- Keep lifecycle, authority, fail-closed, I/O-boundary, and external behavior tests.
- Repair product defects exposed by retained contract tests; do not relax expected behavior to make tests pass.

## Execution Log

- 20260813T104730Z: task created via repoctl task create.
- 20260813T104730Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260813T113000Z: removed eight false-confidence tests and consolidated cache coverage around externally observable retention behavior.
- 20260813T113100Z: repaired independent hash, concurrency, rollback, final-report, projection, graph, and lifecycle assertions; retained tests exposed and drove fixes for real product regressions.
- 20260813T113200Z: full suite and static/integrity checks passed on the final worktree.
- 20260813T114249Z: task finished and verified.

## Verification

- Command: `uv run pytest -q`
- Result: `607 passed in 583.77s`.
- Command: `./scripts/repoctl check --json`
- Result: passed with no problems or warnings; Board and task health are clean.
- Command: `python3 -m json.tool repoctl-upgrade-manifest.json >/dev/null && python3 tools/render_agent_adapters.py --check && uv run ruff check tools/repoctl tests/repoctl tests/maintenance tests/test_permissions_registry.py && git diff --check`
- Result: all checks passed.

## Last Active Handoff

- Next exact step: No implementation remains; review the archived task and final diff if preparing a commit.
- First file to open: `docs/tasks/T-20260813104730Z--audit-test-suite-contracts.md`
- First command to run: `git diff --check`
- Done when: The verified worktree is committed or handed to the release workflow without weakening the audited contracts.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260813104730Z--audit-test-suite-contracts.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260813104730Z.json`
- Git delivery: Not managed by repoctl.

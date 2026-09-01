---
id: T-20260901062834Z
title: "Consolidate maintenance release and permission tests without production changes"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260901T062834Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
follow_up_of: "T-20260901061324Z"
document_language: "en"
handoff_origin_commitment: "sha256:de031248c10798fb30b03feed8ba0788711f5e2d1fc5c1b5dacae27ca477a877"
---

# T-20260901062834Z - Consolidate maintenance release and permission tests without production changes

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/reviews/repo-wide-test-inventory.csv`
- `docs/reviews/repo-wide-test-cleanup-plan.md`

## Work Area

- Task record: `docs/tasks/T-20260901062834Z--maintenance-release-test-consolidation.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: the eight TC6-owned maintenance, permission, and release test rows in the approved TC0 inventory.
- Follow-up of: `T-20260901061324Z`
- Clean issuance baseline: root HEAD `dfa5763`.

## Discovery

- Candidate query: `TC6 maintenance permission release duplicate scope guard inputs approval setup artifact path permutations containment dirty binding hashes JSONL concurrency retry reproducibility fail closed`
- Candidate files reviewed: the TC6 plan section, all eight TC6 inventory rows, and every test/helper in the files under `## Allowed Files`.
- Chosen files: exactly the eight TC6 files under `## Allowed Files`; unchanged rows remain reviewed evidence.

## Goal

Audit the complete TC6 surface and remove only duplicate scope-guard spellings, approval setup, or equivalent artifact path permutations with a named surviving observable regression. Preserve containment, dirty-scope binding, approval hashes, JSONL concurrency, retry safety, permission registry, release reproducibility, and fail-closed prompt approval. Apply no production behavior change.

## Scope

- Change only the eight TC6 files and this task record; audit every test/helper before deletion.
- Prefer deletion and existing helper reuse. Add no fixture framework, abstraction, dependency, plugin, skip, or xfail.
- No production, contract, release-manifest, product-repository, or non-TC6 test changes.

## Allowed Files

- `tests/maintenance/test_json_io.py`
- `tests/maintenance/test_prompt_approval.py`
- `tests/maintenance/test_retry_policy.py`
- `tests/maintenance/test_safe_artifact_writer.py`
- `tests/maintenance/test_scope_guard.py`
- `tests/maintenance/test_workflow_contract.py`
- `tests/test_permissions_registry.py`
- `tests/test_release_repository.py`

## Execution Log

- 20260901T062834Z: task created via repoctl task create.
- 20260901T062834Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260901T063103Z: Audited all 76 TC6 cases and every helper across eight rows; parameterized five exact scope-guard setup clones while preserving every deny case. The 76-case TC6 suite passed.
- 20260901T064103Z: All TC6 closure gates passed: TC6 76, full 875, release 8, release-candidate 3/3, repoctl check, and diff check.
- 20260901T064122Z: task finished and verified.

## Verification

- Baseline: clean HEAD `dfa5763`; eight TC6 rows, 2,472 lines, 76 collected cases; current full-suite baseline 875 cases.
- Post-consolidation TC6 surface: 2,407 lines (`-65`) and 76 collected cases (unchanged).

| Removed or consolidated subject | Surviving replacement | Observable regression preserved | Delta |
|---|---|---|---:|
| three exact file-operation hook setup clones | `test_maintenance_scope_guard_blocks_unapproved_file_operations` with `repos-read`, `artifact-write`, and `repo-write` cases | all three unapproved file-operation classes still return `permissionDecision=deny` | -39 lines |
| exact parseable/unparseable Bash hook setup clones | `test_maintenance_scope_guard_denies_bash_repos_read` with two named cases | a repos read is denied both by parsed command inspection and fail-closed parsing failure | -26 lines |

### Final disposition of all eight TC6 rows

- Consolidated: `test_scope_guard.py` uses existing pytest parameterization to share hook setup while retaining five independently collected cases.
- Kept unchanged after every test/helper was reviewed: the other seven TC6 files.
- Wrong/expected retry agents, nested skills, stale/current artifacts, manual state writes, phase regression, approval surface binding, edit budget, prompt/tool/final trace, artifact concurrency/rollback, plan contract hashes, retries, permission registry, and release archive/reproducibility remain distinct.
- Safe-writer and prompt-approval tests with similar setup were retained where they select different state phases, hash bindings, workflow identities, candidate queues, retry routes, containment classes, or rollback boundaries.
- No new fixture/helper abstraction or dependency was introduced; pytest was already the test framework. No skip, xfail, production change, or non-TC6 test change was made.

### Commands and results

- `test_scope_guard.py`: `24 passed in 0.18s`.
- Full TC6 command: `76 passed in 5.27s`.
- Focused/full collection: `76` cases.
- `.venv/bin/python -m pytest -q`: `875 passed in 560.15s`.
- `.venv/bin/python -m pytest tests/test_release_repository.py -q`: `8 passed in 4.72s`.
- `./scripts/repoctl field-gate run release-candidate --repo-id main --json`: `3/3 passed`, run digest `sha256:a2afd820c94bd693e7bda97cc28695f05c311a2133e1cc378aaf056d4a2aceac`.
- `./scripts/repoctl check --json`: passed with no problems or warnings.
- `git diff --check`: passed. The implementation diff contains only `test_scope_guard.py`; no production or non-TC6 test file changed.

## Last Active Handoff

- Next exact step: Bind this reviewed Handoff and finish `T-20260901062834Z` through repoctl, then create the TC7 shared-support and final-reconciliation follow-up task.
- First file to open: `docs/tasks/T-20260901062834Z--maintenance-release-test-consolidation.md`
- First command to run: `.venv/bin/python -m pytest --collect-only -q tests/maintenance tests/test_permissions_registry.py tests/test_release_repository.py`
- Done when: all eight rows have a disposition, all required gates pass, the task is finished, and the local TC6 commit exists.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260901062834Z--maintenance-release-test-consolidation.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260901062834Z.json`
- Git delivery: Not managed by repoctl.

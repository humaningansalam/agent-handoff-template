---
id: T-20260901051403Z
title: "Consolidate lifecycle and history tests without production changes"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260901T051403Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
follow_up_of: "T-20260901042338Z"
document_language: "en"
handoff_origin_commitment: "sha256:6bb9e6078b0aafed4beb1af90602e0ab660c43b54626c479cd4b22f168a98355"
---

# T-20260901051403Z - Consolidate lifecycle and history tests without production changes

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/reviews/repo-wide-test-inventory.csv`
- `docs/reviews/repo-wide-test-cleanup-plan.md`

## Work Area

- Task record: `docs/tasks/T-20260901051403Z--lifecycle-history-test-consolidation.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: the eight TC2-owned task lifecycle, receipt, catalogue, and support rows identified by the approved TC0 inventory.
- Follow-up of: `T-20260901042338Z`
- Clean issuance baseline: root HEAD `2ba73c4`.

## Discovery

- Candidate query: `TC2 lifecycle history duplicate setup success envelopes helper clones validator permutations atomic rollback provenance immutable scope receipts catalogue bounded cold history`
- Candidate files reviewed: the TC2 section of `docs/reviews/repo-wide-test-cleanup-plan.md`, all eight TC2 rows in `docs/reviews/repo-wide-test-inventory.csv`, and every test/helper in the eight files listed under `## Allowed Files`.
- Chosen files: exactly the eight TC2 files listed under `## Allowed Files`; unchanged files remain reviewed evidence rather than mandatory edits.

## Goal

Audit the entire TC2 surface and remove only lifecycle/history test duplication whose observable failure remains protected by a named surviving test. Preserve every distinct atomicity, rollback, interruption, nonregular-file, provenance, immutable-scope, legacy, Chosen-alignment, exact-version, receipt/catalogue, bounded-history, parent/child, committed-range, and zero-mutation regression. Apply no production behavior change.

## Scope

- Change only the eight TC2 test/support files and this repoctl-managed task record.
- Prefer deletion and reuse of existing helpers; add no fixture framework, base class, plugin, dependency, skip, xfail, or speculative abstraction.
- Test/line count is not a quota. A deletion requires an explicit surviving test and observable-regression map.
- Do not change production, contract, release-manifest, product-repository, or non-TC2 test files.
- Audit every test and helper in all eight files before choosing any deletion; named inventory samples are not the audit boundary.

## Allowed Files

- `tests/repoctl/task/test_task_cancel_block.py`
- `tests/repoctl/task/test_task_create.py`
- `tests/repoctl/task/test_task_finish.py`
- `tests/repoctl/task/test_task_lifecycle.py`
- `tests/repoctl/task/test_task_parent_lifecycle.py`
- `tests/repoctl/task_lifecycle_helpers.py`
- `tests/repoctl/test_completion_catalogue.py`
- `tests/repoctl/test_result_receipts.py`

## Required Gates

- Focused TC2 collection and execution.
- Full `pytest` collection and execution.
- Release tests and release-candidate field gate.
- `repoctl check`, changed-path audit, and `git diff --check`.
- Before/after file-line and collected-case totals.
- A final disposition for all eight rows and a deletion/consolidation to surviving-observable map for every changed subject.

## Execution Log

- 20260901T051403Z: task created via repoctl task create.
- 20260901T051403Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260901T052116Z: Audited all 241 collected cases and every local helper across all eight TC2 rows; consolidated four duplicate create/language/validation cases into stronger surviving public tests with no production changes, and the resulting 237-case TC2 suite passed.
- 20260901T053424Z: All TC2 closure gates passed: focused 237, full 877, release 8, release-candidate 3/3, repoctl check, and diff check.
- 20260901T053443Z: task finished and verified.

## Verification

- Baseline: clean HEAD `2ba73c4`; eight TC2 rows, 8,558 lines, 241 collected cases; current full-suite baseline 881 cases.
- Post-consolidation TC2 surface: 8,517 lines (`-41`) and 237 collected cases (`-4`). The case delta reconciles exactly to the four rows below.

| Removed or consolidated subject | Surviving replacement | Observable regression preserved | Delta |
|---|---|---|---:|
| `test_task_create_uses_configured_korean_document_language` | `test_task_lifecycle_keeps_created_document_language_when_workspace_setting_changes` | configured Korean language is applied at create and remains authoritative through start/finish after workspace configuration changes | -1 case |
| `test_task_create_start_returns_started_task` | `test_started_task_defaults_to_the_only_configured_repository` | `create --start --json` returns `started=true`, `status=doing`, persists doing state, and freezes the selected repository baseline | -1 case |
| `test_task_create_blocks_when_repo_ref_uses_non_repo_area` | `test_task_create_rejects_invalid_area_and_repo_ref_combinations` | a repo ref with a root-only area returns `repo_ref_non_repo_area` | -1 case |
| `test_task_create_blocks_root_repo_ref_alias` | `test_task_create_rejects_invalid_area_and_repo_ref_combinations` | a root alias passed as repo ref returns `invalid_repo_ref` | -1 case |

### Final disposition of all eight TC2 rows

- Consolidated: `tests/repoctl/task/test_task_create.py` now owns its create-validation matrix and imports the existing TC2 support owner rather than the workspace test module; `tests/repoctl/task/test_task_lifecycle.py` no longer repeats create success/validation behavior owned there.
- Kept unchanged after every test/helper was reviewed: `test_task_cancel_block.py`, `test_task_finish.py`, `test_task_parent_lifecycle.py`, `task_lifecycle_helpers.py`, `test_completion_catalogue.py`, and `test_result_receipts.py`.
- The unchanged lifecycle/finish/parent cases retain distinct rollback, interruption, baseline-conflict, committed-range, parent/child, archive, nonregular-file, generated-Handoff provenance, legacy migration, Chosen alignment, exact-version verification, and zero-mutation boundaries. Similar result/catalogue cases retain distinct idempotency, conflict, cache retention/recovery, digest/prefix/gap, committed/pending source parity, duplicate-task, hot/cold isolation, and bounded-history contracts.
- All parametrized cases were retained because their members select different typed fields, corruption sites, source-parity directions, unavailable reasons, publication interruption boundaries, or directory/symlink/FIFO classes. No case was removed based on count, age, size, or naming.
- No new helper, abstraction, fixture framework, dependency, plugin, skip, xfail, production change, or non-TC2 test change was introduced.

### Commands and results

- Focused create plus surviving Korean lifecycle regression: `23 passed in 2.45s`.
- Full TC2 command: `237 passed in 81.47s`.
- Focused/full collection: `237` cases.
- `.venv/bin/python -m pytest -q`: `877 passed in 741.53s`.
- `.venv/bin/python -m pytest tests/test_release_repository.py -q`: `8 passed in 6.26s`.
- `./scripts/repoctl field-gate run release-candidate --repo-id main --json`: `3/3 passed`, run digest `sha256:281957f2d9fe6847635df9c3fd4837b475a8fdd3352ab27f27bb233e0e876cd6`.
- `./scripts/repoctl check --json`: passed with no problems or warnings.
- `git diff --check`: passed. The implementation diff contains only the two TC2 test files; no production or non-TC2 test file changed.

## Last Active Handoff

- Next exact step: Bind this reviewed Handoff and finish `T-20260901051403Z` through repoctl, then create the TC3 Graph/provider follow-up task from the approved inventory.
- First file to open: `docs/tasks/T-20260901051403Z--lifecycle-history-test-consolidation.md`
- First command to run: `.venv/bin/python -m pytest --collect-only -q tests/repoctl/task/test_task_cancel_block.py tests/repoctl/task/test_task_create.py tests/repoctl/task/test_task_finish.py tests/repoctl/task/test_task_lifecycle.py tests/repoctl/task/test_task_parent_lifecycle.py tests/repoctl/test_completion_catalogue.py tests/repoctl/test_result_receipts.py`
- Done when: repoctl archives the verified TC2 task and the local TC2 commit exists.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260901051403Z--lifecycle-history-test-consolidation.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260901051403Z.json`
- Git delivery: Not managed by repoctl.

---
id: T-20260901040033Z
title: "Inventory the entire repository test surface before cleanup"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260901T040033Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
follow_up_of: "T-20260901004550Z"
document_language: "en"
handoff_origin_commitment: "sha256:f6ce23d3299b4729de231204068a39e453bd6e5f09a08589ec11ae63d098ebdf"
---

# T-20260901040033Z - Inventory the entire repository test surface before cleanup

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`

## Work Area

- Task record: `docs/tasks/T-20260901040033Z--repo-wide-test-inventory.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: every tracked file under `tests/**`, including fixtures, helpers, support modules, and package markers.
- Follow-up of: `T-20260901004550Z`

## Discovery

- Candidate query: `entire tests tree duplicate fixtures helpers mocks implementation-detail assertions repeated CLI paths unique safety lifecycle regression cleanup inventory`
- Candidate files reviewed: every tracked file returned by `rg --files tests`
- Chosen files: `docs/reviews/repo-wide-test-inventory.csv`, `docs/reviews/repo-wide-test-cleanup-plan.md`

## Goal

Produce a complete, evidence-backed disposition for the repository's entire tracked test surface before any cleanup wave deletes or consolidates code.

## Scope

- Task Key: `RCTL-090-TC0-REPO-WIDE-TEST-INVENTORY`.
- Inventory every tracked file under `tests/**`; do not sample only recent or changed tests.
- Record file type, domain, line count, collected test count, helper/fixture/mock indicators, unique safety boundary, suspected duplication, and one disposition: `keep`, `consolidate`, `delete-candidate`, or `support-review`.
- Rank cleanup candidates by removable complexity, but set no target test count or line-count quota.
- Preserve unique security, permission, isolation, symlink/path escape, data-loss, atomicity/rollback, lifecycle, compatibility, freshness, digest/tamper, concurrency, zero-mutation, and bounded-history regressions.
- Prefer deletion and reuse of existing helpers; do not propose new fixture frameworks, base classes, plugins, dependencies, or production changes.
- Deliver only `docs/reviews/repo-wide-test-inventory.csv` and `docs/reviews/repo-wide-test-cleanup-plan.md`; TC0 applies no test cleanup.
- Establish TC1–TC7 domain waves and require each cleanup task to prove the surviving observable regression for every deletion or consolidation.

## Execution Log

- 20260901T040033Z: task created via repoctl task create.
- 20260901T040033Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260901T041439Z: Scanned every tracked tests/** file and produced a 70-row inventory plus ranked TC1-TC7 cleanup plan; no test or production cleanup was applied.
- 20260901T041439Z: Main Director directly reconciled tracked paths, per-file lines, pytest node IDs, wave ownership, protected boundaries, and dispositions; corrected two review artifacts and returned DIRECTOR_DECISION: APPROVE_TC0.
- 20260901T041511Z: task finished and verified.

## Verification

- Inventory reconciliation: `git ls-files tests` and `docs/reviews/repo-wide-test-inventory.csv` contain the same 70 unique paths; missing, extra, and duplicate paths are all zero.
- Count reconciliation: 59 Python files / 29,892 Python lines; 11 fixture files / 1,175 fixture lines; 31,067 total tracked test-surface lines; every per-row line count matches the current file.
- Pytest collection: `.venv/bin/python -m pytest --collect-only -q` -> `882 tests collected`; all 46 test-module node-ID counts match the CSV and the CSV total is 882.
- Dispositions: 25 `keep`, 23 `consolidate`, 14 `support-review`, and 8 `delete-candidate`; every row has one non-empty wave, protected boundary, and reason.
- Wave ownership: TC1 16, TC2 8, TC3 9, TC4 5, TC5 14, TC6 8, TC7 10; all 70 rows have exactly one owner and empty package markers are TC7-only.
- Main Director session `01M1DJBM44VJFME82GN1J6NRQ9` (`https://chatgpt.com/c/6a95a198-cc8c-83ee-bc14-737649af4837`) directly inspected and corrected only the two review artifacts, found no remaining blocker, and returned `DIRECTOR_DECISION: APPROVE_TC0`.
- Workspace gates: `./scripts/repoctl check --json` -> no problems or warnings; `git diff --check` -> passed.
- Scope integrity: no file under `tests/**`, `tools/**`, or `scripts/**` was changed; TC0 produced inventory and sequencing only.

## Last Active Handoff

- Next exact step: Finish and archive TC0, commit its two approved review artifacts, then create TC1 for Context-only cleanup without touching another domain.
- First file to open: `docs/tasks/T-20260901040033Z--repo-wide-test-inventory.md`
- First command to run: `./scripts/repoctl task doctor T-20260901040033Z --json`
- Done when: TC0 is archived with the approved 70-row inventory and ranked plan; no cleanup is applied until the separately scoped TC1 task starts.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260901040033Z--repo-wide-test-inventory.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260901040033Z.json`
- Git delivery: Not managed by repoctl.

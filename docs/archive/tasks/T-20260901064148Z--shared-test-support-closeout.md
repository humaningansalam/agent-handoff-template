---
id: T-20260901064148Z
title: "Audit shared test support and reconcile repo-wide cleanup"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260901T064148Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
follow_up_of: "T-20260901062834Z"
document_language: "en"
handoff_origin_commitment: "sha256:9c06df8cde3c951f440d212b1340c4188e8801d82aa533b3c288fd0ab24b0b25"
---

# T-20260901064148Z - Audit shared test support and reconcile repo-wide cleanup

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/reviews/repo-wide-test-inventory.csv`
- `docs/reviews/repo-wide-test-cleanup-plan.md`

## Work Area

- Task record: `docs/tasks/T-20260901064148Z--shared-test-support-closeout.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: the ten TC7 shared-support/package-marker rows, their release-manifest entries, and the two TC0 review artifacts for final reconciliation.
- Follow-up of: `T-20260901062834Z`
- Clean issuance baseline: root HEAD `22a30fe`.

## Discovery

- Candidate query: `TC7 shared support zero consumers empty package markers namespace imports pytest collection io audit bootstrap final 70 row reconciliation lines cases dispositions`
- Candidate files reviewed:
  - `all ten TC7 inventory rows, every consumer of `conftest.py` and `io_audit.py`, all eight empty package markers, and all seventy inventory rows for final reconciliation.`
  - `repoctl-upgrade-manifest.json`
- Chosen files:
  - `the ten TC7 rows plus `docs/reviews/repo-wide-test-inventory.csv` and `docs/reviews/repo-wide-test-cleanup-plan.md`.`
  - `repoctl-upgrade-manifest.json`

## Goal

Delete only shared support or empty markers proven unnecessary by import/collection/full-suite evidence, preserve unique bootstrap and cold-scan fault injection, and reconcile all seventy inventory rows to measured final dispositions, lines, cases, and surviving observable contracts. Apply no production behavior change.

## Scope

- Change only the TC7 support/marker rows, their release-manifest entries, the two TC0 review artifacts, and this task record.
- Delete an empty marker only after proving imports and collection without it. Keep `conftest.py` and `io_audit.py` unless current consumers or bootstrap evidence prove them dead.
- No new helper, abstraction, dependency, plugin, skip, xfail, production file, product repository, or non-TC7 test change.

## Allowed Files

- `tests/conftest.py`
- `tests/repoctl/__init__.py`
- `tests/repoctl/context/__init__.py`
- `tests/repoctl/graph/__init__.py`
- `tests/repoctl/io_audit.py`
- `tests/repoctl/knowledge/__init__.py`
- `tests/repoctl/meta/__init__.py`
- `tests/repoctl/repository/__init__.py`
- `tests/repoctl/task/__init__.py`
- `tests/repoctl/workspace/__init__.py`
- `docs/reviews/repo-wide-test-inventory.csv`
- `docs/reviews/repo-wide-test-cleanup-plan.md`
- `repoctl-upgrade-manifest.json`

## Execution Log

- 20260901T064148Z: task created via repoctl task create.
- 20260901T064148Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260901T064441Z: Verified seven representative namespace imports and 875-case collection after deleting all eight zero-byte package markers; retained conftest bootstrap and io_audit with live consumers; reconciled all 70 rows to 30,524 lines, 875 cases, and 45 kept/17 consolidated/8 deleted.
- 20260901T070435Z: Final gate rerun passed: audit-history 12 events, full suite 875 tests, release 8 tests, release-candidate 3/3, workspace check, and diff check; removed eight stale upgrade-manifest entries exposed by the first full-suite failure.
- 20260901T070442Z: task finished and verified.

## Verification

- Baseline: clean HEAD `22a30fe`; ten TC7 rows, 81 lines, 0 cases; repository baseline before TC1 was 70 rows, 31,067 lines, 882 cases; current pre-TC7 full-suite baseline is 875 cases.
- TC7 surface remains 81 lines and 0 cases: the deleted eight markers were zero-byte files. Current tracked test surface is 62 files after the eight deletions.
- Seven representative namespace imports succeeded without package markers: Context support, Graph build, Knowledge support, metadata, repository, lifecycle support, and workspace check modules.
- Full collection without markers: `875 tests collected in 0.32s`.
- `tests/conftest.py` is kept as the root import bootstrap. `tests/repoctl/io_audit.py` is kept because seven test modules still import `reject_directory_enumeration` for bounded cold-scan fault injection.
- All eight zero-byte package markers are deleted: `tests/repoctl/__init__.py` and the `context`, `graph`, `knowledge`, `meta`, `repository`, `task`, and `workspace` markers.
- The first full-suite attempt exposed five upgrade/release failures because `repoctl-upgrade-manifest.json` still named the deleted files. Removing only those eight stale entries fixed the release contract; the focused rerun passed all 10 upgrade/release tests in 15.37s.
- Final 70-row inventory: 30,524 lines (`-543` from 31,067), 875 cases (`-7` from 882), 45 kept, 17 consolidated, and 8 deleted. Every row now records final lines, cases, deltas, and disposition.
- The seven-case reduction reconciles exactly to archived TC1 (`-1`), TC2 (`-4`), TC4 (`-1`), and TC5 (`-1`) replacement maps. TC3, TC6, and TC7 removed no collected case.
- `./scripts/repoctl check --audit-history --json`: passed; all 12 completion events audited with source parity.
- `.venv/bin/python -m pytest -q`: 875 passed in 522.88s.
- `.venv/bin/python -m pytest tests/test_release_repository.py -q`: 8 passed in 4.44s.
- `./scripts/repoctl field-gate run release-candidate --repo-id main --json`: 3/3 gates passed; run digest `sha256:72564324e327057bf61910c344021b24b5108606ba5cdda5d3b359ecee88038b`.
- `./scripts/repoctl check --json`: passed with a current Board and no problems or warnings.
- `git diff --check`: passed.

## Last Active Handoff

- Next exact step: Bind this reviewed Handoff, finish TC7, and create the local closeout commit.
- First file to open: `docs/tasks/T-20260901064148Z--shared-test-support-closeout.md`
- First command to run: `./scripts/repoctl task handoff bind T-20260901064148Z --json`
- Done when: all ten TC7 rows and all seventy inventory rows have final dispositions, all required gates pass, the task is finished, and the local TC7 commit exists.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260901064148Z--shared-test-support-closeout.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260901064148Z.json`
- Git delivery: Not managed by repoctl.

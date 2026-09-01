---
id: T-20260901061324Z
title: "Consolidate repository metadata workspace and upgrade tests without production changes"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260901T061324Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
follow_up_of: "T-20260901055750Z"
document_language: "en"
handoff_origin_commitment: "sha256:bde716d25553cc30ad01f1860b52d95cad5052e4cecbf1064abc64c33bdff6e2"
---

# T-20260901061324Z - Consolidate repository metadata workspace and upgrade tests without production changes

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/reviews/repo-wide-test-inventory.csv`
- `docs/reviews/repo-wide-test-cleanup-plan.md`

## Work Area

- Task record: `docs/tasks/T-20260901061324Z--repository-workspace-test-consolidation.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: the fourteen TC5-owned metadata, repository, workspace, CLI, and upgrade rows in the approved TC0 inventory.
- Follow-up of: `T-20260901055750Z`
- Clean issuance baseline: root HEAD `6648992`.

## Discovery

- Candidate query: `TC5 metadata repository workspace upgrade duplicate setup validator permutations path integrity symlink escape repository identity stale plan compatibility rollback no mutation CLI discoverability`
- Candidate files reviewed: the TC5 plan section, all fourteen TC5 inventory rows, and every test/helper in the files under `## Allowed Files`.
- Chosen files: exactly the fourteen TC5 files under `## Allowed Files`; unchanged rows remain reviewed evidence.

## Goal

Audit the complete TC5 surface and remove only duplicate setup, happy paths, or equivalent validator permutations with a named surviving observable regression. Preserve repository/path identity, symlink escape, duplicate Git toplevel aliases, metadata shard integrity, missing-file no-mutation, stale upgrade plans, compatibility, rollback, multi-repo isolation, registry integrity, and public CLI discoverability. Apply no production behavior change.

## Scope

- Change only the fourteen TC5 files and this task record; audit every test/helper before deletion.
- Prefer deletion and existing helper reuse. Add no fixture framework, abstraction, dependency, plugin, skip, or xfail.
- No production, contract, release-manifest, product-repository, or non-TC5 test changes.

## Allowed Files

- `tests/repoctl/meta/test_meta_check.py`
- `tests/repoctl/meta/test_meta_paths.py`
- `tests/repoctl/meta/test_meta_policy.py`
- `tests/repoctl/meta/test_meta_query_suggest.py`
- `tests/repoctl/repository/test_repositories.py`
- `tests/repoctl/repository/test_repository_adoption.py`
- `tests/repoctl/repository/test_repository_config.py`
- `tests/repoctl/repository/test_repository_meta_paths.py`
- `tests/repoctl/repository/test_repository_task_scope.py`
- `tests/repoctl/test_cli_discoverability.py`
- `tests/repoctl/test_upgrade.py`
- `tests/repoctl/workspace/test_backlog.py`
- `tests/repoctl/workspace/test_check.py`
- `tests/repoctl/workspace/test_check_validation.py`

## Execution Log

- 20260901T061324Z: task created via repoctl task create.
- 20260901T061324Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260901T061633Z: Audited all 135 TC5 cases and every helper across fourteen rows; centralized exact Git setup helpers in existing owners and merged duplicate meta changed-command setup while preserving both CLI contracts. The resulting 134-case TC5 suite passed.
- 20260901T062755Z: All TC5 closure gates passed: TC5 134, full 875, release 8, release-candidate 3/3, repoctl check, and diff check.
- 20260901T062812Z: task finished and verified.

## Verification

- Baseline: clean HEAD `6648992`; fourteen TC5 rows, 3,433 lines, 135 collected cases; current full-suite baseline 876 cases.
- Post-consolidation TC5 surface: 3,408 lines (`-25`) and 134 collected cases (`-1`).

| Removed or consolidated subject | Surviving replacement | Observable regression preserved | Delta |
|---|---|---|---:|
| local `init_repo` clones in meta and repository tests | existing `workspace/test_check.py::init_repo`, re-exported through the prior modules | all callers still create a configured Git repository; `exist_ok` also preserves callers that pre-create the directory | -11 lines |
| local `repository/test_repositories.py::commit_all` clone | existing `meta/test_meta_check.py::commit_all`, re-exported through the repository helper module | every external consumer still stages and creates the same `base` commit | -4 lines |
| separate changed-mode Git-unavailable tests for `meta check` and `meta status` | `test_meta_changed_commands_report_repo_git_unavailable` | both public commands still return `repository_identity_unbound`; the `meta.check` envelope remains asserted | -1 case / -10 lines |

### Final disposition of all fourteen TC5 rows

- Consolidated: `test_meta_check.py` reuses the existing workspace Git initializer and owns the remaining commit helper; `test_repositories.py` re-exports both existing owners instead of cloning them.
- Kept unchanged after every test/helper was reviewed: the other twelve TC5 files.
- Full-hidden versus partial coverage/exclude overlap, absolute/outside/direct-id/nested/symlink/duplicate-toplevel repository topology, explicit wrong-repo versus traversal discovery, missing/wrong-shard/unicode/leading-space metadata identity, stale/forged upgrade plans, compatibility, rollback, manifested deletion, Board/Backlog registry directions, and CLI surfaces remain separate.
- Same-shape validator tests were retained whenever they select different error codes, trust boundaries, lifecycle states, or path-normalization classes.
- No new helper, abstraction, dependency, plugin, skip, xfail, production change, or non-TC5 test change was introduced.

### Commands and results

- Meta/repository suites: `75 passed in 13.30s`.
- Full TC5 command: `134 passed in 31.91s`.
- Focused/full collection: `134` cases.
- `.venv/bin/python -m pytest -q`: `875 passed in 636.77s`.
- `.venv/bin/python -m pytest tests/test_release_repository.py -q`: `8 passed in 5.23s`.
- `./scripts/repoctl field-gate run release-candidate --repo-id main --json`: `3/3 passed`, run digest `sha256:07f1643a5b78b11f93582a80f16e3b01b8dd1369f1c7ba076ae641da01cfced4`.
- `./scripts/repoctl check --json`: passed with no problems or warnings.
- `git diff --check`: passed. The implementation diff contains only two TC5 helper/test owner files; no production or non-TC5 test file changed.

## Last Active Handoff

- Next exact step: Bind this reviewed Handoff and finish `T-20260901061324Z` through repoctl, then create the TC6 maintenance/release/permission follow-up task.
- First file to open: `docs/tasks/T-20260901061324Z--repository-workspace-test-consolidation.md`
- First command to run: `.venv/bin/python -m pytest --collect-only -q tests/repoctl/meta tests/repoctl/repository tests/repoctl/workspace tests/repoctl/test_upgrade.py tests/repoctl/test_cli_discoverability.py`
- Done when: all fourteen rows have a disposition, all required gates pass, the task is finished, and the local TC5 commit exists.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260901061324Z--repository-workspace-test-consolidation.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260901061324Z.json`
- Git delivery: Not managed by repoctl.

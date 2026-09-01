---
id: T-20260901055750Z
title: "Consolidate Knowledge tests without production changes"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260901T055750Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
follow_up_of: "T-20260901053513Z"
document_language: "en"
handoff_origin_commitment: "sha256:b9c0849fba025d72b97638151584b7bf8cd1807e49b67b2faaf61c29a5a24ed4"
---

# T-20260901055750Z - Consolidate Knowledge tests without production changes

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/reviews/repo-wide-test-inventory.csv`
- `docs/reviews/repo-wide-test-cleanup-plan.md`

## Work Area

- Task record: `docs/tasks/T-20260901055750Z--knowledge-test-consolidation.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: the five TC4-owned Knowledge test/support rows in the approved TC0 inventory.
- Follow-up of: `T-20260901053513Z`
- Clean issuance baseline: root HEAD `636ab83`.

## Discovery

- Candidate query: `TC4 Knowledge duplicate candidate approve query render setup authority digest drift supersession deprecation archive rollback global identity generated view`
- Candidate files reviewed: the TC4 plan section, all five TC4 inventory rows, and every test/helper in the five files under `## Allowed Files`.
- Chosen files: exactly the five TC4 files under `## Allowed Files`; unchanged rows remain reviewed evidence.

## Goal

Audit the complete TC4 surface and remove only duplicate Knowledge test/setup behavior with a named surviving observable regression. Preserve explicit approval authority, candidate/record/event digest binding, source drift, supersession/deprecation, archive relocation, rollback, global identity, and generated-view non-authority. Apply no production behavior change.

## Scope

- Change only the five TC4 files and this task record; audit every test/helper before deletion.
- Prefer deletion and existing helper reuse. Add no fixture framework, abstraction, dependency, plugin, skip, or xfail.
- No production, contract, release-manifest, product-repository, or non-TC4 test changes.

## Allowed Files

- `tests/repoctl/knowledge/test_knowledge_candidates.py`
- `tests/repoctl/knowledge/test_knowledge_context_pack.py`
- `tests/repoctl/knowledge/test_knowledge_lifecycle.py`
- `tests/repoctl/knowledge/test_knowledge_render.py`
- `tests/repoctl/knowledge_test_helpers.py`

## Execution Log

- 20260901T055750Z: task created via repoctl task create.
- 20260901T055750Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260901T060048Z: Audited all 48 TC4 cases and every helper across five rows; reused the existing approval helper in six render setups and merged duplicate event/record namespace setup while preserving both error contracts. The resulting 47-case Knowledge suite passed.
- 20260901T061243Z: All TC4 closure gates passed: Knowledge 47, full 876, release 8, release-candidate 3/3, repoctl check, and diff check.
- 20260901T061300Z: task finished and verified.

## Verification

- Baseline: clean HEAD `636ab83`; five TC4 rows, 2,427 lines, 48 collected cases; current full-suite baseline 877 cases.
- Post-consolidation TC4 surface: 2,399 lines (`-28`) and 47 collected cases (`-1`).

| Removed or consolidated subject | Surviving replacement | Observable regression preserved | Delta |
|---|---|---|---:|
| six repeated render candidate-build/approve setup blocks | existing `_approve_knowledge_source` in `knowledge_test_helpers.py` | each render test still starts from a current approved record/event while testing its distinct render behavior | -24 lines |
| separate event and record repository-namespace tests | `test_knowledge_record_and_event_show_enforce_repo_namespace` | API event listing is isolated; wrong-repo event show returns `knowledge_event_repo_mismatch`; correct/wrong-repo record show remains enforced | -1 case / -4 lines |

### Final disposition of all five TC4 rows

- Consolidated: `test_knowledge_render.py` reuses the existing TC4 approval helper; `test_knowledge_lifecycle.py` shares one multirepo approval between record/event namespace checks.
- Kept unchanged after every test/helper was reviewed: `test_knowledge_candidates.py`, `test_knowledge_context_pack.py`, and `knowledge_test_helpers.py`.
- Candidate build/from-task/context-pack modes, explicit approval, source/record/event digest binding, drift refresh, archive relocation, rollback, supersession/deprecation, hot projection, global candidate identity, generated-view non-authority, and output containment remain distinct.
- Query and render invalid-event tests remain separate because they prove independent consumers fail closed. Machine-state and generated-source exclusions remain separate authority boundaries.
- No new helper, abstraction, dependency, plugin, skip, xfail, production change, or non-TC4 test change was introduced.

### Commands and results

- Changed render/lifecycle files: `27 passed in 14.52s`.
- `.venv/bin/python -m pytest -q tests/repoctl/knowledge`: `47 passed in 25.53s`.
- Focused/full collection: `47` cases.
- `.venv/bin/python -m pytest -q`: `876 passed in 671.68s`.
- `.venv/bin/python -m pytest tests/test_release_repository.py -q`: `8 passed in 5.95s`.
- `./scripts/repoctl field-gate run release-candidate --repo-id main --json`: `3/3 passed`, run digest `sha256:1b1395a200a07a953d35fb6351359e185386d0cd55f40ba692bab06fe3f3383e`.
- `./scripts/repoctl check --json`: passed with no problems or warnings.
- `git diff --check`: passed. The implementation diff contains only two TC4 Knowledge test files; no production or non-TC4 test file changed.

## Last Active Handoff

- Next exact step: Bind this reviewed Handoff and finish `T-20260901055750Z` through repoctl, then create the TC5 repository/meta/workspace/upgrade follow-up task.
- First file to open: `docs/tasks/T-20260901055750Z--knowledge-test-consolidation.md`
- First command to run: `.venv/bin/python -m pytest --collect-only -q tests/repoctl/knowledge`
- Done when: all five rows have a disposition, all required gates pass, the task is finished, and the local TC4 commit exists.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260901055750Z--knowledge-test-consolidation.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260901055750Z.json`
- Git delivery: Not managed by repoctl.

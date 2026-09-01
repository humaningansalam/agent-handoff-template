---
id: T-20260901053513Z
title: "Consolidate Graph and provider tests without production changes"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260901T053513Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
follow_up_of: "T-20260901051403Z"
document_language: "en"
handoff_origin_commitment: "sha256:bee8d9911a3059e2dcbe038c0d577077ce8c55b6160abb298958fcc1614d9965"
---

# T-20260901053513Z - Consolidate Graph and provider tests without production changes

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/reviews/repo-wide-test-inventory.csv`
- `docs/reviews/repo-wide-test-cleanup-plan.md`

## Work Area

- Task record: `docs/tasks/T-20260901053513Z--graph-provider-test-consolidation.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: the nine TC3-owned Graph/provider/component test rows identified by the approved TC0 inventory.
- Follow-up of: `T-20260901051403Z`
- Clean issuance baseline: root HEAD `182f057`.

## Discovery

- Candidate query: `TC3 Graph provider component duplicate materialization scaffolding output forms call import identity Dart RPC structured ambiguity snapshot digest freshness exact selectors repository boundaries`
- Candidate files reviewed: the TC3 plan section, all nine TC3 inventory rows, and every test/helper in the nine files under `## Allowed Files`.
- Chosen files: exactly the nine TC3 files under `## Allowed Files`; unchanged rows remain reviewed evidence rather than mandatory edits.

## Goal

Audit the entire TC3 surface and remove only Graph/provider test duplication whose observable failure remains protected by a named surviving test. Preserve provider-specific semantics, call/import identity, Dart RPC, structured ambiguity, snapshot digest/freshness, component crossing, exact selectors, and repository boundaries. Apply no production behavior change.

## Scope

- Change only the nine TC3 test files and this repoctl-managed task record.
- Prefer deletion and reuse of existing helpers. Add no fixture framework, base class, plugin, dependency, skip, xfail, or speculative abstraction.
- Test/line count is not a quota. Every deletion requires a named surviving observable regression.
- Do not change production, contracts, release manifests, product repositories, or non-TC3 tests.
- Audit every test/helper in all nine files before choosing any deletion.

## Allowed Files

- `tests/repoctl/graph/test_component_projection.py`
- `tests/repoctl/graph/test_graph_build.py`
- `tests/repoctl/graph/test_graph_calls.py`
- `tests/repoctl/graph/test_graph_dart_rpc.py`
- `tests/repoctl/graph/test_graph_import_resolution.py`
- `tests/repoctl/graph/test_graph_query.py`
- `tests/repoctl/graph/test_graph_receipts.py`
- `tests/repoctl/graph/test_graph_structured_relations.py`
- `tests/repoctl/test_code_index.py`

## Execution Log

- 20260901T053513Z: task created via repoctl task create.
- 20260901T053513Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260901T054411Z: Audited all 95 TC3 cases and every helper across all nine rows; centralized three exact Graph materialization helpers in the existing build-test owner and removed one duplicate full-output structured-relation check. The 95-case TC3 suite passed.
- 20260901T055704Z: All TC3 closure gates passed: Graph/provider 95, full 877, release 8, release-candidate 3/3, repoctl check, and diff check.
- 20260901T055722Z: task finished and verified.

## Verification

- Baseline: clean HEAD `182f057`; nine TC3 rows, 4,623 lines, 95 collected cases; current full-suite baseline 877 cases.
- Post-consolidation TC3 surface: 4,599 lines (`-24`) and 95 collected cases (unchanged).

| Removed or consolidated subject | Surviving replacement | Observable regression preserved | Delta |
|---|---|---|---:|
| Exact `_materialize` clones in `test_graph_calls.py`, `test_graph_query.py`, and `test_graph_structured_relations.py` | the same helper in existing Graph materialization owner `test_graph_build.py` | every caller still requires a non-null materialized snapshot with no error-severity problem | -15 net lines |
| duplicate `graph query --full` structured seed relation assertion | snapshot `expected_pairs`, compact Graph evidence/continuation, and Context `callers_and_dependents` assertions in `test_graph_resolves_structured_file_relations_from_explicit_syntax` | structured relations remain materialized, compact-visible with typed evidence, continuable, and Context-visible | -9 lines |

### Final disposition of all nine TC3 rows

- Consolidated: `test_graph_build.py` now owns the single existing Graph materialization helper; `test_graph_calls.py`, `test_graph_query.py`, and `test_graph_structured_relations.py` reuse it. The structured integration test no longer repeats the same seed relation through full and compact output.
- Kept unchanged after every test/helper was reviewed: `test_component_projection.py`, `test_graph_dart_rpc.py`, `test_graph_import_resolution.py`, `test_graph_receipts.py`, and `test_code_index.py`.
- Distinct same-file/class/cross-file/re-export/shadowing/lexical call identities, Python/JS/TS/Dart import precedence and ambiguity, provider availability/coverage, exact selector/freshness, component crossing, receipt authority/repository isolation, and index truncation contracts remain.
- All Dart RPC outcomes remain because direct/stored/implicit tearoff handling, unexpected arguments, schema ambiguity, dropped routines, provider configuration, source/target staleness, and exact RPC identity select different observable states.
- No new abstraction was introduced: an existing three-line helper was moved to the already imported Graph build owner. No production, dependency, plugin, skip, xfail, or non-TC3 test change was made.

### Commands and results

- Four changed Graph files: `51 passed in 52.05s`.
- `.venv/bin/python -m pytest -q tests/repoctl/graph tests/repoctl/test_code_index.py`: `95 passed in 319.08s`.
- Focused/full collection: `95` cases.
- `.venv/bin/python -m pytest -q`: `877 passed in 727.62s`.
- `.venv/bin/python -m pytest tests/test_release_repository.py -q`: `8 passed in 5.55s`.
- `./scripts/repoctl field-gate run release-candidate --repo-id main --json`: `3/3 passed`, run digest `sha256:23ba8277fba60db70f88bd5acbff60d08f2e639eeb9dca2f4109fe6e9af25660`.
- `./scripts/repoctl check --json`: passed with no problems or warnings.
- `git diff --check`: passed. The implementation diff contains only four TC3 Graph test files; no production or non-TC3 test file changed.

## Last Active Handoff

- Next exact step: Bind this reviewed Handoff and finish `T-20260901053513Z` through repoctl, then create the TC4 Knowledge follow-up task.
- First file to open: `docs/tasks/T-20260901053513Z--graph-provider-test-consolidation.md`
- First command to run: `.venv/bin/python -m pytest --collect-only -q tests/repoctl/graph tests/repoctl/test_code_index.py`
- Done when: all nine rows have a disposition, every deletion has a surviving observable owner, all required gates pass, the task is finished, and the local TC3 commit exists.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260901053513Z--graph-provider-test-consolidation.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260901053513Z.json`
- Git delivery: Not managed by repoctl.

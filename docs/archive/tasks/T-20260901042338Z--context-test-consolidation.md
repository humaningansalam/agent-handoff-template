---
id: T-20260901042338Z
title: "Consolidate Context tests and fixtures without production changes"
# canonical values: todo | doing | blocked | done | canceled (see AGENTS.md)
status: done
owner: "codex"
# optional future branch/worktree hint; never used as repository selector
repo_ref: ""
# optional stable product repository id; empty means no product repo selected
repo_id: ""
created: 20260901T042338Z
# optional: frontend | backend | infra | docs | ops
area: "ops"
# optional: parent task ID for child tasks
parent: ""
# optional: advisory prerequisite task IDs (not enforced)
depends_on: []
follow_up_of: "T-20260901040033Z"
document_language: "en"
handoff_origin_commitment: "sha256:22b4a395e9b65363d5bb03ba696ce45d5642a967cac9a65b3f7d2a27d290e7e8"
---

# T-20260901042338Z - Consolidate Context tests and fixtures without production changes

## Context Docs

- `AGENTS.md`
- `docs/PRD.md`
- `docs/reviews/repo-wide-test-inventory.csv`
- `docs/reviews/repo-wide-test-cleanup-plan.md`

## Work Area

- Task record: `docs/tasks/T-20260901042338Z--context-test-consolidation.md`
- Product repository: none selected
- Area hint: ops
- Primary surface: the 16 TC1-owned Context test, fixture, and support rows identified by the approved TC0 inventory.
- Follow-up of: `T-20260901040033Z`
- Clean issuance baseline: root HEAD `43ecfccaeff81b98e561d151d0c74754ee44ee02`.

## Discovery

- Candidate query: `TC1 Context duplicate benchmark questions fixture helpers repeated CLI render paths private helper assertions unique observable regression`
- Candidate files reviewed: all 16 rows whose `cleanup_wave` is `TC1` in `docs/reviews/repo-wide-test-inventory.csv`, plus the TC1 section of `docs/reviews/repo-wide-test-cleanup-plan.md`.
- Chosen files: exactly the 16 implementation files listed in `## Allowed Files` below. The task record may change only for lifecycle evidence and Handoff maintenance.

## Goal

Reduce semantic duplication, unused Context test support, excessive implementation coupling, and repeated public-path assertions while preserving every distinct observable Context, Task Pack, benchmark, attribution, isolation, integrity, and zero-mutation regression. Apply no production behavior change.

## Scope

- Task Key: `RCTL-090-TC1-CONTEXT-TEST-CONSOLIDATION`.
- Priority: P1 test maintainability under correctness-preserving gates.
- Lifecycle predecessor: completed TC0 `T-20260901040033Z`; no other live dependency.
- Domain Expert Key: `DE-REPOCTL-CONTEXT-EVALUATION`.
- TC0 baseline for this wave: 16 owned files, 9,473 lines, 287 collected Context cases, and 882 full-suite cases.
- Deletion is preferred over a new abstraction. Test or line count is not a target.

## Allowed Files

Implementation changes are restricted to these exact TC1 inventory rows:

- `tests/repoctl/context/test_context_benchmark.py`
- `tests/repoctl/context/test_context_pack.py`
- `tests/repoctl/context/test_context_query.py`
- `tests/repoctl/context/test_field_gate.py`
- `tests/repoctl/context_test_helpers.py`
- `tests/fixtures/context-benchmark/attribution-cases.json`
- `tests/fixtures/context-benchmark/corpus.json`
- `tests/fixtures/context-benchmark/expected-sources.json`
- `tests/fixtures/context-benchmark/mutation-cases.json`
- `tests/fixtures/context-benchmark/questions.jsonl`
- `tests/fixtures/context-benchmark-multirepo/corpus.json`
- `tests/fixtures/context-benchmark-multirepo/expected-sources.json`
- `tests/fixtures/context-benchmark-multirepo/mutation-cases.json`
- `tests/fixtures/context-benchmark-multirepo/questions.jsonl`
- `tests/fixtures/context-pack-benchmark/cases.json`
- `tests/fixtures/context-pack-benchmark/tasks.json`

`docs/tasks/T-20260901042338Z--context-test-consolidation.md` may change only to record lifecycle meaning, evidence, disposition, and Handoff. Board and machine state remain repoctl-owned.

The following are outside implementation scope even when a test cleanup would be easier with them:

- `tools/**`, `scripts/**`, `pyproject.toml`, dependency or plugin configuration;
- `docs/contracts/**`, `docs/PRD.md`, and the TC0 review artifacts;
- `repoctl-upgrade-manifest.json`;
- `tests/repoctl/context/__init__.py`, which remains TC7-owned;
- every non-TC1 test, fixture, helper, and support file.

## Required Consolidation Work

1. Consolidate the three exact duplicate benchmark executions without changing production field-gate code:
   - remove Q-036 and transfer the `typed-consumer-closure` category responsibility to canonical Q-004;
   - remove Q-037 and transfer the `typed-structured-dependency-closure` category responsibility to canonical Q-025;
   - remove Q-038 and merge its disconnected-test `noise` expectations into Q-004;
   - remove the corresponding duplicate expected-source entries;
   - preserve the production-required typed category names, Q-004 consumer visibility, Q-025 structured Graph edge and dependency visibility, and global no-forbidden behavior;
   - update only test expectations made stale by the three removals, including the benchmark question count.
2. Delete the four zero-consumer helpers confirmed by TC0 and current static search:
   - `_write_context_benchmark_corpus`;
   - `_approve_superseded_context_knowledge`;
   - `_approve_deprecated_context_knowledge`;
   - `_write_pack_benchmark_task`.
3. Consolidate `test_context_benchmark_attribution_keeps_first_full_occurrence_for_q004` into the surviving public attribution regression that already proves first-occurrence rank/score preservation. Retain the distinct duplicate typed-contribution union contract, either in its current minimal unit test or in an equally small public-path replacement, and document why any remaining private import is necessary.
4. Narrow `test_context_query_prefers_connected_test_over_weak_lexical_test_candidate` to the unique public observable contract: the current owner and typed consumer remain compact-visible, the connected test is selected, and the disconnected lexical test is absent. Remove duplicate text/Markdown renderer assertions and redundant complete seed-payload equality already owned by dedicated renderer and seed-identity tests.
5. Narrow `test_context_query_auto_balances_product_source_tests_and_project_documents` to public complete-versus-partial Graph behavior and source/document lane survival. Remove direct permutation assertions against `_graph_test_path_relations_usable`; the surviving test must still prove that complete usable Graph suppresses an unrelated test and that incomplete provider coverage permits the established lexical fallback.
6. Review all other TC1 tests, fixtures, and support functions for semantic duplication, excessive mocks, repeated CLI success envelopes, implementation-detail ordering, and equivalent parameter permutations. Delete or merge only when a named surviving test proves the same observable failure. A retained private helper call must have a written justification showing that a public-path replacement would obscure a distinct fail-closed boundary or require materially larger fixture setup.
7. Retain both `mutation-cases.json` files in this task. They have no current benchmark loader, but deleting them would require the out-of-scope release manifest decision owned by a later task.

## Observable Regressions That Must Survive

- Context compact/full result receipts preserve source-bound representative citations, full selectable membership, digests, and repository identity.
- Exact path, filename, provider symbol, relationship, task, quoted, Unicode, sigiled, and ambiguous identity cases remain fail-closed where applicable; distinct parser classes must not be collapsed merely because they share an error code.
- Current resolved Graph evidence preserves direction, assertion, one-hop distance, bounded relation closure, direct-test preference, compact budgets, continuation identity, omission diagnostics, and deterministic output.
- Stale, partial, unavailable, unresolved, or ambiguous Graph state never fabricates current semantic evidence and retains the documented lexical fallback boundary.
- Current source, component, structured data, authority/procedure/reference documents, prior outcome, explicit history, and Reviewed Knowledge retain their separate authority and ranking boundaries.
- Cross-repository Context and Knowledge paths remain isolated; stale, superseded, deprecated, ambiguous, missing, and wrong-repository Knowledge paths fail closed.
- Ordinary Context remains hot/bounded and does not scan raw cold completion history; explicit historical modes remain isolated from current ranking.
- Task Pack retains repository binding, current Discovery episode, Chosen/supporting separation, required-reference overflow behavior, stale Graph exclusion, generated-view exclusion, source-drift detection, symlink rejection, tamper/wrong-task/legacy rejection, and no unrelated Knowledge loading.
- Context benchmark retains fixture integrity, source/Knowledge/Graph metrics, multi-repository isolation, current typed category gates, and no-forbidden behavior.
- Attribution remains opt-in, deterministic, read-only, `correlation_only`, non-gating, exact-version bound, tri-state when capture is absent, tamper/provenance/timezone rejecting, and causally insufficient without an independent execution receipt.
- Release-candidate field-gate cleanup occurs on success and raised failure without mutating product fixture state.
- Every unique zero-mutation, source-digest, citation, isolation, freshness, and integrity failure remains covered.

## Non-goals

- No production change, bug fix, ranking adjustment, schema change, new Context behavior, or contract rewrite.
- No new fixture framework, fixture DSL, base class, helper framework, pytest plugin, dependency, or generated test matrix.
- No changes to release manifests or deletion of manifest-managed dormant fixture files.
- No repository-wide cleanup outside the 16 TC1 rows; TC2-TC7 retain their assigned surfaces.
- No test-count or line-count quota and no deletion based only on age, size, mock count, private naming, or parameterization.
- No new `skip`, `xfail`, conditional masking, network dependency, or environment-specific bypass.
- No permutation-style negative test growth. Add a test only when an independently observable regression would otherwise have no surviving protection.

## Acceptance Criteria

1. `git diff --name-only` contains only the 16 allowed implementation files plus this task record and repoctl-managed lifecycle files.
2. Every one of the 16 TC1 inventory rows has a final task-local disposition: unchanged/kept, simplified, consolidated, or deleted. Each non-unchanged item names its surviving replacement and observable regression.
3. Q-036, Q-037, and Q-038 no longer exist; Q-004 alone proves typed consumer visibility plus disconnected-test exclusion, and Q-025 alone proves typed structured dependency visibility plus its resolved Graph edge.
4. The benchmark still exposes `typed-consumer-closure` and `typed-structured-dependency-closure` category summaries at visible recall `1.0`, and global forbidden selection remains zero, without modifying `tools/repoctl/cli.py`.
5. The four zero-consumer Context helpers are removed and a tracked-Python reference scan reports no remaining reference to them.
6. The Q-004-specific duplicate first-occurrence attribution test is removed or merged; one surviving regression proves canonical first rank/score, and one surviving regression proves logical union of duplicate Graph/Knowledge typed contributions.
7. The connected-test regression uses public compact/full output for its unique behavior and no longer repeats unrelated text/Markdown rendering or whole seed-payload equality.
8. The source/test/document balance regression proves both complete-Graph suppression and partial-Graph fallback through public Context output, without direct branch permutations against the private capability helper.
9. Every remaining direct use of a Context production-private symbol is listed in Verification with its unique contract and reason a public-path replacement was rejected. Unjustified private coupling is removed.
10. Root locator injection and unique fault injection may remain. No mock or stub replaces the ranker, selector, validator, or projection under test merely to manufacture the expected result.
11. Equivalent parameter cases that reach the same parser/validator branch and observable result are reduced to one representative; genuinely different quoting, Unicode, sigil, ambiguity, freshness, repository, and authority boundaries remain.
12. Both dormant `mutation-cases.json` files remain byte-identical unless only formatting is required for a proven test consumer; no release-manifest change is made.
13. No production file, dependency declaration, plugin configuration, contract, TC0 review artifact, TC7 package marker, or non-TC1 test file changes.
14. The Context domain suite, full suite, release-candidate field gate, workspace check, and diff check pass. Test count may decrease only by the disposition table; no new skip/xfail is present.
15. A `DE-REPOCTL-CONTEXT-EVALUATION` reviewer directly inspects the final diff and approves the replacement map, preserved observable contracts, field-gate behavior, and absence of production changes before finish.

## Required Runtime Evidence

- Baseline confirmation: clean pre-task HEAD `43ecfccaeff81b98e561d151d0c74754ee44ee02`, 16 TC1 rows / 9,473 lines / 287 Context cases / 882 full-suite cases.
- Before/after table with exact test, fixture case, helper, or assertion block; disposition; surviving test/case; observable failure; protected boundary; and measured line/case delta.
- Machine-readable proof that the post-cleanup benchmark question IDs are unique by `(repo_id, mode, question)` and that Q-004/Q-025 carry the required typed categories.
- Static tracked-Python reference proof for each removed helper and removed private-test symbol use.
- Exact list of remaining Context private production references and task-local justification for each.
- Before/after `pytest --collect-only` totals; any reduction must reconcile exactly to deleted or merged tests.
- Before/after TC1 file line totals; report the measured delta without treating it as a quota.
- `git diff --stat`, `git diff --name-only`, and `git diff --check`.
- Domain Expert review identifier and direct-diff decision.

Required verification commands, using surviving test names after consolidation where a named test is removed:

```bash
.venv/bin/python -m pytest --collect-only -q
.venv/bin/python -m pytest tests/repoctl/context -q
.venv/bin/python -m pytest tests/repoctl/context/test_context_benchmark.py \
  tests/repoctl/context/test_context_pack.py \
  tests/repoctl/context/test_context_query.py \
  tests/repoctl/context/test_field_gate.py -q
./scripts/repoctl field-gate run release-candidate --repo-id main --json
.venv/bin/python -m pytest tests/test_release_repository.py -q
.venv/bin/python -m pytest -q
./scripts/repoctl check --json
git diff --check
```

Also run a focused set containing the surviving tests for:

- canonical benchmark materialization and typed category/no-forbidden gates;
- attribution first-occurrence rank and duplicate typed-contribution union;
- connected test versus lexical decoy;
- complete-versus-partial Graph test fallback;
- Task Pack tamper/wrong-task/legacy/source-drift boundaries;
- field-gate cleanup on success and raised failure.

## Execution Log

- 20260901T042338Z: task created via repoctl task create.
- 20260901T042338Z: task started; repo dirty check unavailable (task has no product repository target).
- 20260901T042548Z: Main Director issued TC1 from clean HEAD 43ecfcc with the exact 16-row Context scope, mandatory duplicate fixture/helper consolidation, observable-regression replacement map, no-production boundary, and DE-REPOCTL-CONTEXT-EVALUATION closeout.
- 20260901T045221Z: TC1 consolidation removed three duplicate benchmark executions and four zero-consumer helpers, narrowed two Context regressions to public output, and passed focused 7, Context 286, explicit four-module 286, full 881, release 8, and release-candidate 3/3 checks.
- 20260901T051020Z: DE-REPOCTL-CONTEXT-EVALUATION session 01M1DN5MNPS5BX23CDR7KR0K5G directly inspected the exact diff, removed one now-unused import, corrected final evidence wording/totals, reran narrow checks, and returned EXPERT_DECISION: APPROVE.
- 20260901T051102Z: task finished and verified.

## Verification

- Baseline: clean HEAD `43ecfccaeff81b98e561d151d0c74754ee44ee02`; 16 TC1 rows, 9,473 lines, 287 Context cases, 882 full-suite cases.
- Result: 9,113 TC1 lines (`-360`), 286 Context cases (`-1`), 881 full-suite cases (`-1`). The case delta is exactly the removed duplicate Q-004 first-occurrence test; the three removed fixture questions were data rows, not collected pytest cases.

| Removed or consolidated subject | Disposition | Surviving replacement | Observable regression | Protected boundary | Delta |
|---|---|---|---|---|---:|
| Q-036 | merged into Q-004 | Q-004 / `typed-consumer-closure` | typed incoming consumer remains compact-visible | Graph relation closure | -1 fixture case |
| Q-037 | merged into Q-025 | Q-025 / `typed-structured-dependency-closure` | resolved `USES_FILE` edge and dependency remain visible | structured Graph integrity | -1 fixture case |
| Q-038 | noise merged into Q-004 | Q-004 `selected_forbidden == []` and global forbidden count | disconnected lexical tests remain unselected | false-positive isolation | -1 fixture case |
| `test_context_benchmark_attribution_keeps_first_full_occurrence_for_q004` | consolidated | `test_context_benchmark_attribution_is_opt_in_stable_and_read_only` plus `test_context_benchmark_attribution_unions_duplicate_typed_contributions` | canonical public rank/score and logical Graph/Knowledge contribution union | deterministic attribution | -1 pytest case / -34 lines |
| `_write_context_benchmark_corpus` | deleted | `_write_context_benchmark_collection_corpus` remains for its distinct multirepo consumer | removed helper had zero tracked Python consumers | fixture materialization | -11 lines |
| `_approve_superseded_context_knowledge` | deleted | existing Context/Knowledge lifecycle tests | removed helper had zero tracked Python consumers | Knowledge lifecycle | -12 lines |
| `_approve_deprecated_context_knowledge` | deleted | existing Context/Knowledge lifecycle tests | removed helper had zero tracked Python consumers | Knowledge lifecycle | -12 lines |
| `_write_pack_benchmark_task` | deleted | `test_context_pack.py` and context-pack fixture data | removed 202-line task writer had zero tracked Python consumers | Task Pack integrity | -204 lines |
| connected-test full/text/Markdown/seed assertions | simplified in place | compact public path assertions in `test_context_query_prefers_connected_test_over_weak_lexical_test_candidate` | owner and typed consumer visible, connected test selected, disconnected test absent | direct-test preference | -40 lines |
| `_graph_test_path_relations_usable` capability permutations | removed | complete/partial public Context outputs in `test_context_query_auto_balances_product_source_tests_and_project_documents` | complete Graph suppresses unrelated test; partial coverage permits lexical fallback without starving source/document lanes | partial-provider fallback | -17 lines |
| per-category `no-disconnected-test` field-gate assertion | consolidated | Q-004 forbidden assertion and field-gate global forbidden count | release artifact retains zero forbidden selections | release gate integrity | 0 lines |

### Final disposition of all 16 TC1 rows

- Consolidated/simplified: `test_context_benchmark.py`, `test_context_query.py`, `test_field_gate.py`, `context_test_helpers.py`, `context-benchmark/questions.jsonl`, and `context-benchmark/expected-sources.json`.
- Kept unchanged after review: `test_context_pack.py`; `context-benchmark/attribution-cases.json`, `corpus.json`, and `mutation-cases.json`; all four `context-benchmark-multirepo` fixtures; and both `context-pack-benchmark` fixtures.
- The two dormant `mutation-cases.json` files remain byte-identical: SHA-256 `274bdccff4499bc70872b39c8014b140f7c4d0d8e569d2e3b60a463d44a59525` and `8ac343a49e1e22c66fd79fe5635a8a5a2e6a949a0bebab1e02524341fb611d6d`.
- `(repo_id, mode, question)` duplicate scan returned `[]`; Q-004 and Q-025 carry the required typed categories; the four removed helper names and deleted Q-004 test name have zero tracked-Python matches, while `_graph_test_path_relations_usable` has zero TC1 test references.
- Remaining production-private symbols: `_retrieval_evidence` uniquely proves first-occurrence rank/score preservation and typed Graph/Knowledge union without a second full benchmark workspace; `_coverage_profile_test_target_paths`, `_direct_source_relation_connects`, and `_select_compact_evidence_profiles` uniquely exercise fail-closed typed direction and one-neighbor compact selection. Public CLI replacements would duplicate large Graph fixtures and obscure these bounded internal safety contracts.
- Parameter families for quoted, unmatched, repeated, Unicode combining-mark, sigil, exact identity, ambiguity, freshness, repository, and authority cases were retained because they exercise distinct parser classes or safety boundaries. No new helper, fixture framework, dependency, plugin, skip, or xfail was added.

### Commands and results

- Focused surviving benchmark/attribution/connected-test/fallback/field-gate set: `7 passed in 19.04s`.
- `.venv/bin/python -m pytest tests/repoctl/context -q`: `286 passed in 185.07s`.
- Explicit four-module Context command: `286 passed in 185.02s`.
- `.venv/bin/python -m pytest tests/test_release_repository.py -q`: `8 passed in 4.59s`.
- `.venv/bin/python -m pytest --collect-only -q`: `881 tests collected`.
- `.venv/bin/python -m pytest -q`: `881 passed in 536.50s`.
- `./scripts/repoctl field-gate run release-candidate --repo-id main --json`: `3/3 passed`, run digest `sha256:c14cca8e1642ba5cb175d47838ebfad4dd0900c9958a429466a3f059b42d0ed6`.
- `git diff --check`: passed. `git diff --name-only` contains only TC1 implementation files plus repoctl lifecycle files; no production or non-TC1 test file changed.
- Domain Expert `DE-REPOCTL-CONTEXT-EVALUATION`, agbrowse session `01M1DN5MNPS5BX23CDR7KR0K5G`: directly inspected the exact diff in DevSpace, removed one now-unused import, corrected the final line total/reference wording, reran focused checks, and returned `EXPERT_DECISION: APPROVE`.

## Last Active Handoff

- Next exact step: Run final post-review workspace/diff checks and finish `T-20260901042338Z` through repoctl.
- First file to open: `docs/tasks/T-20260901042338Z--context-test-consolidation.md`
- First command to run: `./scripts/repoctl check --json && git diff --check && ./scripts/repoctl task finish T-20260901042338Z --json`
- Done when: final post-review checks pass and repoctl archives the Expert-approved TC1 task.

## Closure

- Task result: Implementation and verification completed.
- Task record at completion: `docs/archive/tasks/T-20260901042338Z--context-test-consolidation.md`
- Repo evidence mode: `none`
- Completion receipt: `docs/tasks/.repoctl-state/completions/T-20260901042338Z.json`
- Git delivery: Not managed by repoctl.
